#!/usr/bin/env python3
"""
Generate a realistic sample ledger page image (bahi-khata) in fixtures/sample_ledger.png.
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def generate_ledger_image(output_path: str = "fixtures/sample_ledger.png"):
    width, height = 900, 1100
    # Cream / aged paper background
    img = Image.new("RGB", (width, height), color=(253, 250, 241))
    draw = ImageDraw.Draw(img)

    # Draw notebook margin line (red/magenta)
    margin_x = 90
    draw.line([(margin_x, 0), (margin_x, height)], fill=(225, 120, 120), width=2)

    # Draw horizontal ruling lines (light blue)
    line_spacing = 42
    y_start = 120
    for y in range(y_start, height - 50, line_spacing):
        draw.line([(0, y), (width, y)], fill=(200, 220, 240), width=1)

    # Header section
    draw.rectangle([(20, 30), (width - 20, 95)], outline=(180, 160, 140), width=2)
    # Title
    draw.text((width // 2 - 140, 45), "श्री गणेशाय नमः", fill=(180, 40, 40))
    draw.text((width // 2 - 120, 68), "बही-खाता (Credit Ledger) - अक्टूबर 2026", fill=(40, 40, 90))

    # Column titles
    draw.text((30, y_start - 25), "तारीख", fill=(100, 100, 100))
    draw.text((120, y_start - 25), "ग्राहक का नाम (Customer)", fill=(100, 100, 100))
    draw.text((380, y_start - 25), "विवरण (Description)", fill=(100, 100, 100))
    draw.text((680, y_start - 25), "जमा/नामे (Amount)", fill=(100, 100, 100))

    # Vertical column separator lines
    draw.line([(360, y_start - 35), (360, height - 70)], fill=(215, 225, 235), width=1)
    draw.line([(650, y_start - 35), (650, height - 70)], fill=(215, 225, 235), width=1)

    # Handwritten entries
    entries = [
        ("10/10/26", "Sharma ji", "Atta 10kg, Mustard Oil 2L", "₹ 1,250 (उधार)"),
        ("10/10/26", "Gupta Sahab", "Basmati Rice 5kg, Sugar 2kg", "₹ 820 (उधार)"),
        ("11/10/26", "Ramesh Bhai", "Toor Dal 2kg, Spices", "₹ 450 (उधार)"),
        ("11/10/26", "Sharma ji", "Cash payment received", "₹ 500 [जमा / PAID]"),
        ("12/10/26", "Verma ji", "Dairy items, Ghee 1kg", "₹ 650 (उधार)"),
        ("12/10/26", "Gupta Sahab", "UPI payment received", "₹ 820 [जमा / PAID]"),
        ("12/10/26", "Sunil Kumar", "Sabun, Surf, Chai", "₹ 340 (उधार)"),
    ]

    # Draw entries in fountain pen blue / dark navy
    ink_color = (25, 45, 115)
    paid_color = (20, 100, 40)

    for i, (date, name, desc, amt) in enumerate(entries):
        y = y_start + (i * line_spacing) + 10
        color = paid_color if "PAID" in amt or "जमा" in amt else ink_color
        draw.text((25, y), date, fill=color)
        draw.text((115, y), name, fill=color)
        draw.text((375, y), desc, fill=color)
        draw.text((665, y), amt, fill=color)

    # Footer note
    draw.text((width // 2 - 130, height - 40), "* शुद्ध बही खाता - कम्प्यूटरीकृत सत्यापन *", fill=(140, 140, 140))

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_p))
    print(f"Generated realistic ledger image -> {output_path}")

if __name__ == "__main__":
    generate_ledger_image()
