"""Read vendor, date, registration number, subtotal, HST and total from receipt text.

Swappable layer: a photo/OCR or vision-model step can produce the same text or fields.
"""
import re
from datetime import datetime
from decimal import Decimal

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%b %d, %Y"]
SKIP_FIRST_LINES = {"invoice", "receipt", "tax invoice"}
BN_RE = re.compile(r"\b(\d{9})\s*RT\s*(\d{4})\b", re.I)
LABELS = {
    "subtotal": r"(?:subtotal|net|amount\s+before\s+tax)",
    "hst": r"(?:hst|h\.s\.t\.|gst/hst)",
    "total": r"(?:total(?:\s+due)?|amount\s+due|amount\s+paid|amount(?!\s+before))",
}


def _amount(label, text):
    rx = rf"(?im)^\s*{label}(?=[\s:(])[^\n]*?(\d[\d,]*\.\d{{2}})(?!\d)[^\d\n]*$"
    m = re.search(rx, text)
    return Decimal(m.group(1).replace(",", "")) if m else None


def _date(text):
    m = re.search(r"(?im)^\s*(?:invoice date|txn date|date)\s*[:\-]?\s*(.+?)\s*$", text)
    if not m:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(m.group(1), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _vendor(text):
    m = re.search(r"(?im)^\s*(?:from|merchant)\s*:\s*(.+?)\s*$", text)
    if m:
        return m.group(1)
    for line in text.splitlines():
        if line.strip() and line.strip().lower() not in SKIP_FIRST_LINES:
            return line.strip().title() if line.strip().isupper() else line.strip()
    return None


def extract(text):
    bn = BN_RE.search(text)
    return {
        "vendor": _vendor(text),
        "date": _date(text),
        "registration_number": f"{bn.group(1)}RT{bn.group(2)}" if bn else None,
        "subtotal": _amount(LABELS["subtotal"], text),
        "hst": _amount(LABELS["hst"], text),
        "total": _amount(LABELS["total"], text),
    }
