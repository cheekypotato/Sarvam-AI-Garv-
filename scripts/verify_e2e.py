#!/usr/bin/env python3
"""
End-to-end verification script for Bahi-Khata Bolo.
Tests:
  1. Ledger extraction (using sample fixture)
  2. Database insertions and deterministic queries
  3. Intent parsing & customer fuzzy resolution
  4. Answer generation with audit trail
  5. WhatsApp reminder generation
"""

import sys
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.config import load_settings
from src.db import get_connection, insert_rows, balance_of, outstanding_total, top_debtors, list_customers
from src.ledger_extract import extract_ledger
from src.intents import parse_intent
from src.answers import generate_answer
from src.reminders import generate_reminder

def main():
    print("=" * 60)
    print("Bahi-Khata Bolo — End-to-End Pipeline Verification")
    print("=" * 60)

    settings = load_settings()
    # Use in-memory DB or test DB for verification
    test_db_path = str(_ROOT / "data" / "verify_test.db")
    conn = get_connection(test_db_path)
    conn.execute("DELETE FROM ledger_entries")
    conn.commit()

    # Step 1: Ledger extraction
    print("\n1. Testing Ledger Extraction (sample fixture)...")
    sample_img = str(_ROOT / "fixtures" / "sample_ledger.png")
    rows = extract_ledger(sample_img, settings)
    print(f"   Extracted {len(rows)} rows from ledger.")
    assert len(rows) > 0, "No rows extracted!"

    # Step 2: Insert into Database
    print("\n2. Inserting rows into SQLite database...")
    dict_rows = [
        {
            "customer_name": r.customer_name,
            "date": r.date,
            "description": r.description,
            "amount": r.amount,
            "entry_type": r.entry_type,
            "needs_review": r.needs_review,
            "review_reason": r.review_reason,
        }
        for r in rows
    ]
    count = insert_rows(conn, dict_rows, source_image=sample_img)
    print(f"   Inserted {count} rows.")

    # Step 3: Verify Deterministic DB Calculations
    print("\n3. Verifying Deterministic Balances in SQL...")
    total = outstanding_total(conn)
    print(f"   Total Outstanding across all customers: ₹{total:,.0f}")
    assert total > 0, "Total outstanding should be > 0"

    sharma_bal = balance_of(conn, "sharma")
    assert sharma_bal is not None, "Sharma balance not found!"
    print(f"   Sharma ji balance: ₹{sharma_bal.balance:,.0f} (Credit: ₹{sharma_bal.total_credit:,.0f}, Paid: ₹{sharma_bal.total_paid:,.0f})")
    assert sharma_bal.balance == 750.0, f"Expected 750, got {sharma_bal.balance}"

    # Step 4: Intent Parsing
    print("\n4. Testing Spoken Intent Parsing...")
    known_customers = list_customers(conn)
    test_queries = [
        ("Sharma ji ka kitna baaki hai?", "balance_of", "sharma"),
        ("Sabka kul hisab kitna hai?", "outstanding_total", None),
        ("Sabse zyada udhar kiska hai?", "top_debtors", None),
    ]

    for q, expected_intent, expected_key in test_queries:
        intent = parse_intent(q, known_customers, settings)
        print(f"   Query: '{q}' -> Intent: {intent.intent}, Customer: {intent.customer_key}")
        assert intent.intent == expected_intent, f"Expected {expected_intent}, got {intent.intent}"
        if expected_key:
            assert intent.customer_key == expected_key, f"Expected key {expected_key}, got {intent.customer_key}"

    # Step 5: Answer Generation
    print("\n5. Testing Deterministic Answer Generation...")
    sharma_intent = parse_intent("Sharma ji ka kitna baaki hai?", known_customers, settings)
    answer = generate_answer(sharma_intent, conn, settings, detected_language="hi-IN")
    print(f"   Generated Answer: '{answer.text}'")
    assert "750" in answer.text, "Answer should contain exact balance 750!"
    assert len(answer.rows) >= 2, "Audit trail should include Sharma ji's rows"

    # Step 6: WhatsApp Reminder Generation
    print("\n6. Testing WhatsApp Reminder Generation...")
    reminder = generate_reminder("Sharma ji", sharma_bal.balance, shop_name="Gupta General Store", language="hi-IN")
    print(f"   Reminder message:\n   {reminder['message']}")
    print(f"   WhatsApp URL: {reminder['whatsapp_url']}")
    assert "750" in reminder["message"]
    assert "https://wa.me/?text=" in reminder["whatsapp_url"]

    print("\n" + "=" * 60)
    print("✅ All end-to-end pipeline components verified successfully!")
    print("=" * 60)

if __name__ == "__main__":
    main()
