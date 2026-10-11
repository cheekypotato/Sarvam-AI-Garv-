"""
Ledger extraction: photo → structured rows via Sarvam Document AI (Extract).

Flow:
  1. Submit image to doc_ai.extract() with our ledger schema.
  2. Poll get_status() until terminal.
  3. Fetch results with get_results().
  4. Validate and normalize each row.
  5. Cache the raw result to fixtures/ for DEMO_MODE.

Fallback: if Extract misbehaves, use Digitise + Chat to parse.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import Settings
from src.sarvam_client import (
    SarvamError,
    doc_extract_create,
    doc_get_results,
    doc_get_status,
    get_client,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema for the Extract call
# ---------------------------------------------------------------------------

LEDGER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "description": "Every row of the handwritten credit ledger page, top to bottom",
            "items": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date written on the row, as written",
                    },
                    "customer_name": {
                        "type": "string",
                        "description": "Name of the customer in the original script",
                    },
                    "description": {
                        "type": "string",
                        "description": "Items or note written for this row",
                    },
                    "amount": {
                        "type": "number",
                        "description": "Amount in rupees for this row",
                    },
                    "entry_type": {
                        "type": "string",
                        "enum": ["credit_given", "payment_received"],
                        "description": (
                            "credit_given if goods were given on credit; "
                            "payment_received if the customer paid"
                        ),
                    },
                },
            },
        }
    },
}


# ---------------------------------------------------------------------------
# Row dataclass
# ---------------------------------------------------------------------------

@dataclass
class LedgerRow:
    """One row extracted from the ledger image."""

    date: str = ""
    customer_name: str = ""
    description: str = ""
    amount: float | None = None
    entry_type: str = "credit_given"
    needs_review: bool = False
    review_reason: str = ""


# ---------------------------------------------------------------------------
# Terminal states for Document AI jobs
# ---------------------------------------------------------------------------

_TERMINAL_STATES = {"completed", "partially_completed", "failed", "rejected"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_ledger(
    image_path: str,
    settings: Settings,
    *,
    on_status: Any = None,
) -> list[LedgerRow]:
    """Extract ledger rows from an image file.

    Args:
        image_path: Path to PNG/JPG/PDF image.
        settings: App settings (contains API key, language, timeouts).
        on_status: Optional callback ``(status_dict) -> None`` for UI updates.

    Returns:
        List of LedgerRow objects (some may have ``needs_review=True``).

    Raises:
        SarvamError: On unrecoverable API failures.
        TimeoutError: If the job doesn't finish within ``doc_timeout_seconds``.
    """
    # --- DEMO_MODE: return cached fixture --------------------------------
    if settings.demo_mode:
        return _load_fixture(image_path, settings)

    client = get_client(settings)

    # 1. Create Extract job
    job_id = doc_extract_create(
        client,
        image_path,
        LEDGER_SCHEMA,
        language=settings.ledger_language,
        output_format="json",
    )

    # 2. Poll until terminal
    deadline = time.time() + settings.doc_timeout_seconds
    while time.time() < deadline:
        status = doc_get_status(client, job_id)
        logger.info("Job %s: %s", job_id, status)

        if on_status:
            on_status(status)

        state = status["status"].lower()
        if state in _TERMINAL_STATES:
            break

        time.sleep(settings.doc_poll_interval_seconds)
    else:
        raise TimeoutError(
            f"Document AI job {job_id} did not finish within "
            f"{settings.doc_timeout_seconds}s."
        )

    # 3. Handle terminal state
    if state == "failed":
        raise SarvamError(500, f"Extract job {job_id} failed.")
    if state == "rejected":
        raise SarvamError(400, f"Extract job {job_id} was rejected.")
    if state == "partially_completed":
        logger.warning("Job %s partially completed – some pages may be missing.", job_id)

    # 4. Fetch results
    raw_result = doc_get_results(client, job_id)

    # 5. Cache to fixtures
    _save_fixture(image_path, raw_result, settings)

    # 6. Parse into rows
    return _parse_rows(raw_result)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_rows(raw_result: Any) -> list[LedgerRow]:
    """Turn the raw Extract result into validated LedgerRow objects."""
    from src.normalize import normalize_amount, normalize_name

    # raw_result might be a dict or a string (JSON)
    if isinstance(raw_result, str):
        try:
            raw_result = json.loads(raw_result)
        except json.JSONDecodeError:
            logger.error("Could not parse extract result as JSON.")
            return []

    entries = raw_result.get("entries", []) if isinstance(raw_result, dict) else []
    rows: list[LedgerRow] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        row = LedgerRow(
            date=str(entry.get("date", "")).strip(),
            customer_name=str(entry.get("customer_name", "")).strip(),
            description=str(entry.get("description", "")).strip(),
            entry_type=entry.get("entry_type", "credit_given") or "credit_given",
        )

        # Normalize amount
        raw_amount = entry.get("amount")
        parsed_amount = normalize_amount(raw_amount)
        if parsed_amount is None and raw_amount is not None:
            row.needs_review = True
            row.review_reason = f"Could not parse amount: {raw_amount}"
        row.amount = parsed_amount

        # Normalize name
        if not row.customer_name:
            row.needs_review = True
            row.review_reason = "Customer name is empty."
        else:
            row.customer_name = normalize_name(row.customer_name)

        # Validate entry_type
        if row.entry_type not in ("credit_given", "payment_received"):
            row.entry_type = "credit_given"
            row.needs_review = True
            row.review_reason = "entry_type unclear, defaulted to credit_given."

        rows.append(row)

    return rows


def _save_fixture(image_path: str, raw_result: Any, settings: Settings) -> None:
    """Cache the raw extract result for DEMO_MODE."""
    try:
        fixtures_dir = Path(settings.fixtures_dir)
        fixtures_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(image_path).stem
        fixture_path = fixtures_dir / f"{stem}_extract.json"
        if isinstance(raw_result, str):
            try:
                data = json.loads(raw_result)
                fixture_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            except Exception:
                fixture_path.write_text(raw_result)
        else:
            fixture_path.write_text(json.dumps(raw_result, ensure_ascii=False, indent=2))
        logger.info("Cached extract result → %s", fixture_path)
    except Exception:
        logger.warning("Could not cache fixture for %s", image_path, exc_info=True)


def _load_fixture(image_path: str, settings: Settings) -> list[LedgerRow]:
    """Load a cached fixture for DEMO_MODE."""
    stem = Path(image_path).stem
    fixtures_dir = Path(settings.fixtures_dir)
    fixture_path = fixtures_dir / f"{stem}_extract.json"

    if not fixture_path.exists():
        fallback_path = fixtures_dir / "sample_ledger_extract.json"
        if fallback_path.exists():
            fixture_path = fallback_path
        else:
            any_fixtures = list(fixtures_dir.glob("*_extract.json"))
            if any_fixtures:
                fixture_path = any_fixtures[0]
            else:
                logger.warning("No fixture found in %s, returning empty.", fixtures_dir)
                return []

    raw = json.loads(fixture_path.read_text())
    return _parse_rows(raw)
