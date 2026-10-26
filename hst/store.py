"""Small file-based store so captured receipts survive restarts.

store/images/<id>.<ext>   original photos (CRA expects receipt records to be kept; back these up)
store/ledger.json         [{id, image, text, confidence, engine, added, source, original_name, sha256}], decisions, bank CSV
Everything else (status, HST, credits, bank matches) is recomputed from this on demand, so a rules change
or bug fix applies to old receipts too.
"""
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .assess import assess_receipt
from .bank import match_bank, parse_bank_csv
from .pipeline import summarize
from .rules import load_rules


class Store:
    def __init__(self, root, rules=None):
        self.root = Path(root)
        self.images = self.root / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "ledger.json"
        self.rules = rules or load_rules()
        self.lock = threading.RLock()
        self.data = {"receipts": [], "decisions": {}, "bank_csv": "", "next_id": 1}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add_receipt(self, text, confidence, engine, source, original_name=None, image_bytes=None, ext=".jpg"):
        """Returns (id, is_new). An identical photo (same bytes) is not stored twice."""
        digest = hashlib.sha256(image_bytes).hexdigest() if image_bytes else None
        with self.lock:
            if digest:
                for r in self.data["receipts"]:
                    if r.get("sha256") == digest:
                        return r["id"], False
            rid = f"r{self.data['next_id']:04d}"
            self.data["next_id"] += 1
            image = None
            if image_bytes:
                image = f"{rid}{ext}"
                (self.images / image).write_bytes(image_bytes)
            self.data["receipts"].append({
                "id": rid, "image": image, "text": text, "confidence": confidence, "engine": engine,
                "added": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": source,
                "original_name": original_name, "sha256": digest})
            self._save()
            return rid, True

    def has_image(self, image_bytes):
        digest = hashlib.sha256(image_bytes).hexdigest()
        with self.lock:
            return any(r.get("sha256") == digest for r in self.data["receipts"])

    def set_bank_csv(self, text):
        rows, skipped = parse_bank_csv(text)
        with self.lock:
            self.data["bank_csv"] = text
            self._save()
        return len(rows), skipped

    def decide(self, rid, action):
        with self.lock:
            self.data["decisions"][rid] = action
            self._save()

    def clear(self):
        with self.lock:
            for f in self.images.glob("*"):
                f.unlink()
            self.data = {"receipts": [], "decisions": {}, "bank_csv": "", "next_id": 1}
            self._save()

    def image_path(self, rid):
        with self.lock:
            for r in self.data["receipts"]:
                if r["id"] == rid and r["image"]:
                    return self.images / r["image"]
        return None

    def assess_one(self, rid):
        return next((r for r in self.snapshot()["receipts"] if r["id"] == rid), None)

    def snapshot(self):
        """Assess everything in order. Returns receipts, bank lines, decisions and the summary."""
        with self.lock:
            raw = [dict(r) for r in self.data["receipts"]]
            decisions = dict(self.data["decisions"])
            bank_csv = self.data["bank_csv"]
        seen, receipts = set(), []
        for r in raw:
            res = assess_receipt(r["text"], self.rules, seen, r.get("confidence"))
            res.update(file=r["id"], id=r["id"], text=r["text"], image=r["image"], engine=r["engine"],
                       confidence=r.get("confidence"), source=r["source"], original_name=r["original_name"],
                       added=r["added"])
            receipts.append(res)
        bank = match_bank(parse_bank_csv(bank_csv)[0], receipts, self.rules) if bank_csv else []
        return {"receipts": receipts, "bank": bank, "decisions": decisions,
                "summary": summarize(receipts, bank, decisions)}
