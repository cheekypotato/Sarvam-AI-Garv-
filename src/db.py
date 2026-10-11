"""
SQLite database layer for Bahi-Khata Bolo.

Schema: ``ledger_entries`` table.
Balance for a customer = sum(credit_given) - sum(payment_received).

All balance computations happen HERE in SQL — never in the LLM.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.normalize import customer_key

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ledger_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_raw    TEXT    NOT NULL,
    customer_key    TEXT    NOT NULL,
    entry_date      TEXT    DEFAULT '',
    description     TEXT    DEFAULT '',
    amount          REAL    NOT NULL DEFAULT 0,
    entry_type      TEXT    NOT NULL CHECK(entry_type IN ('credit_given', 'payment_received')),
    source_image    TEXT    DEFAULT '',
    needs_review    INTEGER NOT NULL DEFAULT 0,
    review_reason   TEXT    DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_customer_key ON ledger_entries(customer_key);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_connection(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database and ensure the schema exists."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_INDEX)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def insert_entry(
    conn: sqlite3.Connection,
    *,
    customer_raw: str,
    entry_date: str = "",
    description: str = "",
    amount: float = 0.0,
    entry_type: str = "credit_given",
    source_image: str = "",
    needs_review: bool = False,
    review_reason: str = "",
) -> int:
    """Insert a single ledger entry and return its ``id``."""
    key = customer_key(customer_raw)
    cur = conn.execute(
        """
        INSERT INTO ledger_entries
            (customer_raw, customer_key, entry_date, description,
             amount, entry_type, source_image, needs_review, review_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer_raw,
            key,
            entry_date,
            description,
            amount,
            entry_type,
            source_image,
            int(needs_review),
            review_reason,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def insert_rows(conn: sqlite3.Connection, rows: list[dict], source_image: str = "") -> int:
    """Bulk-insert a list of row dicts. Returns number of rows inserted."""
    count = 0
    for row in rows:
        insert_entry(
            conn,
            customer_raw=row.get("customer_name", row.get("customer_raw", "")),
            entry_date=row.get("date", row.get("entry_date", "")),
            description=row.get("description", ""),
            amount=float(row.get("amount", 0) or 0),
            entry_type=row.get("entry_type", "credit_given"),
            source_image=source_image,
            needs_review=bool(row.get("needs_review", False)),
            review_reason=row.get("review_reason", ""),
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Read operations — deterministic, code-computed balances
# ---------------------------------------------------------------------------

@dataclass
class CustomerBalance:
    customer_key: str
    customer_raw: str
    total_credit: float
    total_paid: float
    balance: float  # credit_given - payment_received (positive = they owe you)


def balance_of(conn: sqlite3.Connection, key: str) -> Optional[CustomerBalance]:
    """Return the balance for a single customer by their normalized key."""
    row = conn.execute(
        """
        SELECT
            customer_key,
            MAX(customer_raw) AS customer_raw,
            COALESCE(SUM(CASE WHEN entry_type = 'credit_given'      THEN amount ELSE 0 END), 0) AS total_credit,
            COALESCE(SUM(CASE WHEN entry_type = 'payment_received'  THEN amount ELSE 0 END), 0) AS total_paid
        FROM ledger_entries
        WHERE customer_key = ?
        GROUP BY customer_key
        """,
        (key,),
    ).fetchone()

    if not row:
        return None

    return CustomerBalance(
        customer_key=row["customer_key"],
        customer_raw=row["customer_raw"],
        total_credit=row["total_credit"],
        total_paid=row["total_paid"],
        balance=row["total_credit"] - row["total_paid"],
    )


def outstanding_total(conn: sqlite3.Connection) -> float:
    """Total outstanding balance across all customers."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN entry_type = 'credit_given'      THEN amount ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN entry_type = 'payment_received'  THEN amount ELSE 0 END), 0) AS total
        FROM ledger_entries
        """
    ).fetchone()
    return row["total"] if row else 0.0


def top_debtors(conn: sqlite3.Connection, n: int = 5) -> list[CustomerBalance]:
    """Return the top *n* customers by outstanding balance (descending)."""
    rows = conn.execute(
        """
        SELECT
            customer_key,
            MAX(customer_raw) AS customer_raw,
            COALESCE(SUM(CASE WHEN entry_type = 'credit_given'      THEN amount ELSE 0 END), 0) AS total_credit,
            COALESCE(SUM(CASE WHEN entry_type = 'payment_received'  THEN amount ELSE 0 END), 0) AS total_paid
        FROM ledger_entries
        GROUP BY customer_key
        HAVING total_credit - total_paid > 0
        ORDER BY (total_credit - total_paid) DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()

    return [
        CustomerBalance(
            customer_key=r["customer_key"],
            customer_raw=r["customer_raw"],
            total_credit=r["total_credit"],
            total_paid=r["total_paid"],
            balance=r["total_credit"] - r["total_paid"],
        )
        for r in rows
    ]


def entries_of(conn: sqlite3.Connection, key: str) -> list[dict]:
    """Return all ledger entries for a customer (for audit trail)."""
    rows = conn.execute(
        """
        SELECT id, customer_raw, entry_date, description, amount,
               entry_type, source_image, needs_review, review_reason, created_at
        FROM ledger_entries
        WHERE customer_key = ?
        ORDER BY created_at ASC
        """,
        (key,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_customers(conn: sqlite3.Connection) -> list[str]:
    """Return a sorted list of all unique customer_keys."""
    rows = conn.execute(
        "SELECT DISTINCT customer_key FROM ledger_entries ORDER BY customer_key"
    ).fetchall()
    return [r["customer_key"] for r in rows]


def customer_display_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Map customer_key → most-recent customer_raw (for display)."""
    rows = conn.execute(
        """
        SELECT customer_key, customer_raw
        FROM ledger_entries
        GROUP BY customer_key
        """
    ).fetchall()
    return {r["customer_key"]: r["customer_raw"] for r in rows}
