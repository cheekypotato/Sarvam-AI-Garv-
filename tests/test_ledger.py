"""
Unit tests for Bahi-Khata Bolo core logic.
Tests normalization, database balances, intent parsing, and answers.
"""

import sqlite3
import pytest

from src.config import Settings
from src.normalize import (
    normalize_amount,
    normalize_date,
    normalize_name,
    customer_key,
    fuzzy_match_customer,
    _to_ascii_digits,
)
from src.db import (
    get_connection,
    insert_entry,
    insert_rows,
    balance_of,
    outstanding_total,
    top_debtors,
    entries_of,
    list_customers,
)
from src.intents import Intent, _keyword_fallback, _resolve_customer
from src.answers import generate_answer
from src.reminders import generate_reminder
from src.voice import _split_text, _wav_duration


# ============================================================================
# 1. Normalization Tests
# ============================================================================

def test_ascii_digits_conversion():
    # Devanagari ०१२३४५६७८९ -> 0123456789
    assert _to_ascii_digits("१५००") == "1500"
    assert _to_ascii_digits("२५०.५०") == "250.50"
    # Bengali
    assert _to_ascii_digits("১২৩৪") == "1234"
    # Gujarati
    assert _to_ascii_digits("૧૨૩૪") == "1234"


def test_normalize_amount():
    assert normalize_amount(500) == 500.0
    assert normalize_amount(500.75) == 500.75
    assert normalize_amount("500") == 500.0
    assert normalize_amount("₹ 1,500.00") == 1500.0
    assert normalize_amount("1,20,000") == 120000.0
    assert normalize_amount("₹१,५००") == 1500.0
    assert normalize_amount("  $450  ") == 450.0
    assert normalize_amount("") is None
    assert normalize_amount(None) is None
    assert normalize_amount("nil") is None
    assert normalize_amount("-") is None


def test_customer_key_and_honorifics():
    assert customer_key("Sharma ji") == "sharma"
    assert customer_key("Sharma Ji") == "sharma"
    assert customer_key("sharma") == "sharma"
    assert customer_key("Gupta Sahab") == "gupta"
    assert customer_key("Ramesh Bhai") == "ramesh"
    assert customer_key("शर्मा जी") == "शर्मा"
    assert customer_key("Mr. Rajesh Kumar") == "rajesh kumar"


def test_fuzzy_match():
    known = ["sharma", "gupta", "verma", "rajesh"]
    matches = fuzzy_match_customer("Sharma ji", known)
    assert len(matches) > 0
    assert matches[0][0] == "sharma"
    assert matches[0][1] >= 80

    matches_typo = fuzzy_match_customer("shrma", known)
    assert len(matches_typo) > 0
    assert matches_typo[0][0] == "sharma"


# ============================================================================
# 2. Database Balance Computation Tests (Deterministic, in SQL)
# ============================================================================

@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_ledger.db"
    conn = get_connection(str(db_file))
    yield conn
    conn.close()


def test_database_balances(test_db):
    # Sharma ji: credit 1000, credit 500, paid 300 -> balance 1200
    insert_entry(test_db, customer_raw="Sharma ji", amount=1000.0, entry_type="credit_given")
    insert_entry(test_db, customer_raw="Sharma Ji", amount=500.0, entry_type="credit_given")
    insert_entry(test_db, customer_raw="Sharma", amount=300.0, entry_type="payment_received")

    # Verma: credit 2000, paid 2000 -> balance 0
    insert_entry(test_db, customer_raw="Verma Sahab", amount=2000.0, entry_type="credit_given")
    insert_entry(test_db, customer_raw="Verma", amount=2000.0, entry_type="payment_received")

    # Gupta: credit 5000 -> balance 5000
    insert_entry(test_db, customer_raw="Gupta ji", amount=5000.0, entry_type="credit_given")

    # Verify balance_of Sharma (grouped under customer_key='sharma')
    sharma_bal = balance_of(test_db, "sharma")
    assert sharma_bal is not None
    assert sharma_bal.total_credit == 1500.0
    assert sharma_bal.total_paid == 300.0
    assert sharma_bal.balance == 1200.0

    # Verify balance_of Verma
    verma_bal = balance_of(test_db, "verma")
    assert verma_bal is not None
    assert verma_bal.balance == 0.0

    # Total outstanding: 1200 + 0 + 5000 = 6200
    total = outstanding_total(test_db)
    assert total == 6200.0

    # Top debtors
    debtors = top_debtors(test_db, n=2)
    assert len(debtors) == 2
    assert debtors[0].customer_key == "gupta"
    assert debtors[0].balance == 5000.0
    assert debtors[1].customer_key == "sharma"
    assert debtors[1].balance == 1200.0

    # Unknown customer
    assert balance_of(test_db, "nonexistent") is None


def test_bulk_insert_rows(test_db):
    rows = [
        {"customer_name": "Ramesh", "date": "10/10/2026", "amount": 250, "entry_type": "credit_given"},
        {"customer_name": "Ramesh", "date": "11/10/2026", "amount": 50, "entry_type": "payment_received"},
    ]
    inserted = insert_rows(test_db, rows, source_image="test.jpg")
    assert inserted == 2

    bal = balance_of(test_db, "ramesh")
    assert bal is not None
    assert bal.balance == 200.0
    entries = entries_of(test_db, "ramesh")
    assert len(entries) == 2


# ============================================================================
# 3. Intent Resolution & Answers Tests
# ============================================================================

def test_intent_fallback():
    intent = _keyword_fallback("Sharma ji ka kitna baaki hai?")
    assert intent.intent == "balance_of"

    intent_total = _keyword_fallback("kul kitna hisab hai sabka?")
    assert intent_total.intent == "outstanding_total"

    intent_top = _keyword_fallback("sabse zyada udhar kiska hai?")
    assert intent_top.intent == "top_debtors"


def test_answer_generation(test_db):
    insert_entry(test_db, customer_raw="Sharma ji", amount=1200.0, entry_type="credit_given")
    settings = Settings(sarvam_api_key="test_key")

    # Test balance_of answer
    intent = Intent(intent="balance_of", customer_name="Sharma ji", customer_key="sharma")
    ans = generate_answer(intent, test_db, settings, detected_language="hi-IN")
    assert "1,200" in ans.text
    assert ans.numbers["balance"] == 1200.0
    assert len(ans.rows) == 1

    # In English
    ans_en = generate_answer(intent, test_db, settings, detected_language="en-IN")
    assert "1,200" in ans_en.text
    assert "owes" in ans_en.text

    # Test outstanding_total answer
    intent_tot = Intent(intent="outstanding_total")
    ans_tot = generate_answer(intent_tot, test_db, settings, detected_language="hi-IN")
    assert "1,200" in ans_tot.text


# ============================================================================
# 4. Voice & Reminder Helpers Tests
# ============================================================================

def test_text_splitting():
    short_text = "This is a short sentence."
    assert _split_text(short_text, 100) == [short_text]

    long_text = "Sentence one. Sentence two. Sentence three. Sentence four."
    chunks = _split_text(long_text, 30)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 30


def test_reminder_template():
    res = generate_reminder("Sharma ji", 1200.0, shop_name="Gupta General Store", language="hi-IN")
    assert "1,200" in res["message"]
    assert "Sharma ji" in res["message"]
    assert "https://wa.me/?text=" in res["whatsapp_url"]
