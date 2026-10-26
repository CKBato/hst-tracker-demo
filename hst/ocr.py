"""Turn a receipt photo into text for extract(). Two switchable engines.

- tesseract: local, free, private. Needs the Tesseract program installed (Pillow optional, improves quality).
- claude:    Anthropic vision API. Needs ANTHROPIC_API_KEY. Receipt images are sent to Anthropic.

Both return {"text", "confidence", "engine"}. confidence is 0-100 or None when the engine gives none.
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
CLAUDE_MODEL = os.environ.get("HST_CLAUDE_MODEL", "claude-sonnet-5-5")
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

CLAUDE_PROMPT = """Read this Canadian receipt or invoice photo. Reply with ONLY plain text, one item per line, in exactly this format and omitting any line you cannot read clearly (never guess a value):
Merchant: <business name>
Tax Reg #: <9 digits followed by RT and 4 digits, if printed>
Date: <YYYY-MM-DD>
Subtotal <amount before tax, if printed>
HST <amount of HST/GST charged, if printed>
Total <total paid>
Use plain numbers with a decimal point and no currency symbols. If this is not a receipt, reply with: NOT A RECEIPT"""


def find_tesseract():
    return os.environ.get("HST_TESSERACT") or shutil.which("tesseract") or next(
        (p for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                     r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe") if Path(p).exists()), None)


def engines_available():
    return {"tesseract": find_tesseract() is not None, "claude": bool(os.environ.get("ANTHROPIC_API_KEY"))}


def _prepare(path, tmp):
    """Orient, grayscale, boost contrast, upscale small images. Falls back to the original without Pillow."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return str(path)
    img = ImageOps.exif_transpose(Image.open(path)).convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    if img.width < 1400:
        scale = 1400 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    out = Path(tmp) / "prepared.png"
    img.save(out)
    return str(out)


def parse_tsv(tsv):
    """Rebuild lines and a mean word confidence from tesseract TSV output."""
    lines, confs = {}, []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or not cols[11].strip():
            continue
        conf = float(cols[10])
        if conf < 0:
            continue
        confs.append(conf)
        lines.setdefault((cols[2], cols[3], cols[4]), []).append(cols[11].strip())
    text = "\n".join(" ".join(words) for words in lines.values())
    return text + "\n", (sum(confs) / len(confs) if confs else None)


def read_tesseract(path):
    exe = find_tesseract()
    if not exe:
        raise RuntimeError("Tesseract is not installed (winget install UB-Mannheim.TesseractOCR).")
    with tempfile.TemporaryDirectory() as tmp:
        src = _prepare(path, tmp)
        proc = subprocess.run([exe, src, "stdout", "--psm", "4", "-l", "eng", "tsv"],
                              capture_output=True, timeout=90, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"Tesseract failed: {proc.stderr.strip()[:200]}")
    text, conf = parse_tsv(proc.stdout)
    return {"text": text, "confidence": conf, "engine": "tesseract"}


def _http_post(url, headers, body, timeout=90):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def read_claude(path, post=_http_post):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    ext = Path(path).suffix.lower()
    media = MEDIA_TYPES.get(ext)
    if not media:
        raise RuntimeError(f"Claude engine supports jpg, png and webp, not {ext}.")
    payload = {
        "model": CLAUDE_MODEL, "max_tokens": 400,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media,
                                         "data": base64.b64encode(Path(path).read_bytes()).decode()}},
            {"type": "text", "text": CLAUDE_PROMPT}]}],
    }
    resp = post(CLAUDE_URL, {"x-api-key": key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"}, json.dumps(payload).encode("utf-8"))
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text").strip()
    if not text or text.upper().startswith("NOT A RECEIPT"):
        text = ""
    return {"text": text + "\n", "confidence": None, "engine": "claude"}


def read_image(path, engine="tesseract"):
    if Path(path).suffix.lower() not in IMAGE_EXTS:
        raise RuntimeError(f"Unsupported image type: {Path(path).suffix} (convert HEIC to JPG).")
    if engine == "claude":
        return read_claude(path)
    if engine == "tesseract":
        return read_tesseract(path)
    raise RuntimeError(f"Unknown engine: {engine}")
