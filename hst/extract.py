"""Read vendor, date, registration number, subtotal, HST and total from receipt text.

Tolerates common OCR noise (leading junk characters, 5/S and 0/O swaps in labels, trailing times).
Anything it cannot read stays None, and the assessment sends that to review.
"""
import re
from datetime import datetime
from decimal import Decimal

DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%b %d, %Y", "%b %d %Y"]
SKIP_FIRST_LINES = {"invoice", "receipt", "tax invoice", "customer copy"}
BN_RE = re.compile(r"\b(\d{9})\s*R\s*T\s*([0-9A-Za-z]{3,6})\b")
OCR_DIGITS = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1", "i": "1"})
LABELS = {
    "subtotal": r"(?:sub\s?t[o0]tal|net|amount\s+before\s+tax)",
    "hst": r"(?:h\s?[s5]\s?t|h\.s\.t\.|gst\s?/\s?hst)",
    "total": r"(?:t[o0]tal(?:\s+due)?|amount\s+due|amount\s+paid|amount(?!\s+before))",
}


def _amount(label, text):
    rx = rf"(?im)^[^\w\n]*{label}(?=[\s:(])[^\n]*?(\d[\d,]*\.\d{{2}})(?!\d)[^\d\n]*$"
    m = re.search(rx, text)
    return Decimal(m.group(1).replace(",", "")) if m else None


def _parse_date(raw):
    raw = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", raw.strip())
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        # An OCR slip like 2825 must not become a "valid" date.
        if 2015 <= parsed.year <= datetime.now().year + 1:
            return parsed.isoformat()
    return None


def _date(text):
    m = re.search(r"(?im)^[^\w\n]*(?:invoice date|txn date|date)\s*[:\-]?\s*(.+?)\s*$", text)
    if m and _parse_date(m.group(1)):
        return _parse_date(m.group(1))
    iso = re.search(r"\b(20\d\d-\d\d-\d\d)\b", text)
    return _parse_date(iso.group(1)) if iso else None


def _vendor(text):
    m = re.search(r"(?im)^[^\w\n]*(?:from|merchant)\s*:\s*(.+?)\s*$", text)
    if m:
        return m.group(1)
    for line in text.splitlines():
        s = line.strip(" |_-*")
        if len(s) >= 3 and re.search(r"[A-Za-z]{3}", s) and s.lower() not in SKIP_FIRST_LINES:
            return s.title() if s.isupper() else s
    return None


def vendor_candidates(text, limit=6):
    """First few plausible text lines. Photos often have junk above the real merchant name."""
    out = []
    for line in text.splitlines():
        s = line.strip(" |_-*")
        if len(s) >= 3 and re.search(r"[A-Za-z]{3}", s):
            out.append(s.title() if s.isupper() else s)
        if len(out) >= limit:
            break
    return out


def _bn(m):
    """9-digit business number + RT account. OCR often garbles the account part (RT0001 -> RTQ001), so the
    account digits are cleaned best-effort; the 9 digits are what matters for the claim."""
    if not m:
        return None
    account = re.sub(r"\D", "", m.group(2).translate(OCR_DIGITS))
    return f"{m.group(1)}RT{account}" if account else None


def extract(text):
    bn = BN_RE.search(text)
    return {
        "vendor": _vendor(text),
        "date": _date(text),
        "registration_number": _bn(bn),
        "subtotal": _amount(LABELS["subtotal"], text),
        "hst": _amount(LABELS["hst"], text),
        "total": _amount(LABELS["total"], text),
    }
