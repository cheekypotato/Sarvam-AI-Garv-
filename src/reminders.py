"""
Reminder message generator for Bahi-Khata Bolo (stretch goal).

Generates a polite payment reminder in the customer's language using
Sarvam-105B, including the exact amount from the DB. Provides a copy
button and a ``wa.me`` link for WhatsApp.
"""

from __future__ import annotations

import logging
import urllib.parse

from src.config import Settings
from src.sarvam_client import chat, get_client

logger = logging.getLogger(__name__)


def generate_reminder(
    customer_name: str,
    amount: float,
    shop_name: str = "हमारी दुकान",
    language: str = "hi-IN",
    settings: Settings | None = None,
) -> dict[str, str]:
    """Generate a polite reminder message and a WhatsApp link.

    Returns:
        {"message": "...", "whatsapp_url": "https://wa.me/?text=..."}
    """
    if settings:
        message = _llm_reminder(customer_name, amount, shop_name, language, settings)
    else:
        message = _template_reminder(customer_name, amount, shop_name, language)

    wa_url = f"https://wa.me/?text={urllib.parse.quote(message)}"

    return {"message": message, "whatsapp_url": wa_url}


def _template_reminder(
    customer_name: str, amount: float, shop_name: str, language: str
) -> str:
    """Simple template-based reminder (no LLM)."""
    if language.startswith("hi"):
        return (
            f"नमस्ते {customer_name} जी,\n\n"
            f"{shop_name} से आपका ₹{amount:,.0f} का हिसाब बाकी है। "
            f"कृपया सुविधानुसार भुगतान कर दें।\n\n"
            f"धन्यवाद 🙏"
        )
    return (
        f"Hello {customer_name},\n\n"
        f"This is a friendly reminder that you have an outstanding balance "
        f"of ₹{amount:,.0f} at {shop_name}. "
        f"Kindly settle it at your convenience.\n\n"
        f"Thank you 🙏"
    )


def _llm_reminder(
    customer_name: str,
    amount: float,
    shop_name: str,
    language: str,
    settings: Settings,
) -> str:
    """Use Sarvam-105B to generate a polite, natural reminder."""
    try:
        client = get_client(settings)
        prompt = (
            f"Write a short, polite payment reminder message in {'Hindi' if language.startswith('hi') else 'English'}. "
            f"Customer name: {customer_name}. "
            f"Amount due: ₹{amount:,.0f}. "
            f"Shop name: {shop_name}. "
            f"The message must include the exact amount ₹{amount:,.0f}. "
            f"Keep it under 200 characters. Be warm and respectful. "
            f"Do not add any greeting from the shop owner's name."
        )
        reply = chat(
            client,
            messages=[{"role": "user", "content": prompt}],
            model=settings.chat_model,
            reasoning_effort=None,
        )

        # Verify the amount appears in the output
        amount_str = f"₹{amount:,.0f}"
        if amount_str not in reply:
            logger.warning("LLM reminder missing amount, falling back to template.")
            return _template_reminder(customer_name, amount, shop_name, language)

        return reply.strip()
    except Exception as exc:
        logger.warning("LLM reminder failed: %s — using template.", exc)
        return _template_reminder(customer_name, amount, shop_name, language)
