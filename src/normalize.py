"""
Normalization utilities for Bahi-Khata Bolo.

- Amount parsing: strip currency symbols / commas, convert Devanagari and
  regional digits to ASCII, handle edge cases.
- Date parsing: lenient, best-effort.
- Customer name normalization: trim, lowercase, collapse whitespace,
  strip honorifics ("ji", "jee", "sahab", "bhai", etc.) for grouping.
- Fuzzy name matching via ``rapidfuzz``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from rapidfuzz import fuzz, process


# ---------------------------------------------------------------------------
# Digit conversion tables (Devanagari, Bengali, Gujarati, Tamil, etc.)
# ---------------------------------------------------------------------------

_DIGIT_MAP: dict[str, str] = {}
# Devanagari: U+0966..U+096F
for i in range(10):
    _DIGIT_MAP[chr(0x0966 + i)] = str(i)
# Bengali: U+09E6..U+09EF
for i in range(10):
    _DIGIT_MAP[chr(0x09E6 + i)] = str(i)
# Gujarati: U+0AE6..U+0AEF
for i in range(10):
    _DIGIT_MAP[chr(0x0AE6 + i)] = str(i)
# Tamil: U+0BE6..U+0BEF
for i in range(10):
    _DIGIT_MAP[chr(0x0BE6 + i)] = str(i)
# Telugu: U+0C66..U+0C6F
for i in range(10):
    _DIGIT_MAP[chr(0x0C66 + i)] = str(i)
# Kannada: U+0CE6..U+0CEF
for i in range(10):
    _DIGIT_MAP[chr(0x0CE6 + i)] = str(i)
# Malayalam: U+0D66..U+0D6F
for i in range(10):
    _DIGIT_MAP[chr(0x0D66 + i)] = str(i)
# Odia: U+0B66..U+0B6F
for i in range(10):
    _DIGIT_MAP[chr(0x0B66 + i)] = str(i)
# Gurmukhi: U+0A66..U+0A6F
for i in range(10):
    _DIGIT_MAP[chr(0x0A66 + i)] = str(i)


def _to_ascii_digits(text: str) -> str:
    """Replace all regional-script digits with ASCII 0-9."""
    return "".join(_DIGIT_MAP.get(ch, ch) for ch in text)


# ---------------------------------------------------------------------------
# Amount normalization
# ---------------------------------------------------------------------------

# Characters to strip from an amount string
_AMOUNT_STRIP_RE = re.compile(r"[₹$€£,\s\u20B9]")  # ₹ = U+20B9


def normalize_amount(raw: object) -> Optional[float]:
    """Parse a raw amount value into a float, or return None.

    Handles:
      - Already numeric (int / float)
      - Strings with currency symbols, commas, spaces
      - Devanagari / regional digits
    """
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        return float(raw) if raw == raw else None  # NaN check

    text = str(raw).strip()
    if not text:
        return None

    # Regional digits → ASCII
    text = _to_ascii_digits(text)

    # Strip currency symbols, commas, whitespace
    text = _AMOUNT_STRIP_RE.sub("", text)

    # Remove trailing dash or hyphen (sometimes used for "nil")
    text = text.strip("-–—")

    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Date normalization (best-effort)
# ---------------------------------------------------------------------------

def normalize_date(raw: str) -> str:
    """Return a cleaned date string (best-effort, keeps original if unparseable).

    We don't try to be too clever here: the dates on a handwritten ledger
    are often partial (day/month only) or in DD-MM or DD/MM/YYYY format.
    """
    text = str(raw).strip()
    text = _to_ascii_digits(text)
    # Normalize separators
    text = text.replace(".", "/").replace("-", "/")
    return text


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

# Honorifics to strip (case-insensitive, whole-word)
_HONORIFICS = {
    "ji", "jee", "sahab", "saheb", "bhai", "bhaiya", "didi",
    "sir", "madam", "ma'am", "mr", "mrs", "ms", "shri", "smt",
    "श्री", "श्रीमती", "जी",
}


def normalize_name(name: str) -> str:
    """Clean a customer name for display (preserves original script case).

    Trims whitespace and collapses multiple spaces.
    """
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    return name


def customer_key(name: str) -> str:
    """Compute a canonical grouping key for a customer name.

    Lowercase, strip honorifics, collapse whitespace.
    Handles Indic scripts accurately by matching tokens rather than regex word boundaries.
    Used as ``customer_key`` in the DB for dedup / grouping.
    """
    raw_lower = name.strip().lower()
    tokens = [
        t for t in raw_lower.split()
        if t.strip(".,;:?!") not in _HONORIFICS
    ]
    if tokens:
        return " ".join(tokens)
    return raw_lower


# ---------------------------------------------------------------------------
# Fuzzy name matching
# ---------------------------------------------------------------------------

def fuzzy_match_customer(
    query: str,
    known_customers: list[str],
    *,
    threshold: int = 80,
) -> list[tuple[str, float]]:
    """Return matching customers sorted by score (descending).

    Uses ``rapidfuzz.fuzz.WRatio`` for robust partial + token matching.

    Returns a list of ``(customer_key, score)`` tuples where score >= threshold.
    """
    if not known_customers or not query.strip():
        return []

    query_key = customer_key(query)
    results = process.extract(
        query_key,
        known_customers,
        scorer=fuzz.WRatio,
        limit=5,
        score_cutoff=threshold,
    )
    # results: list of (match, score, index)
    return [(match, score) for match, score, _ in results]
