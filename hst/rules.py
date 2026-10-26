"""Load the readable rules file and answer category questions."""
import json
from decimal import Decimal
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules.json")


def load_rules(path=RULES_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def category_for(name, rules):
    """Map a vendor name or bank description to a category using keywords. None if unknown."""
    low = name.lower()
    for keyword, category in rules["vendor_keywords"].items():
        if keyword in low:
            return category
    return None


def rate(rules):
    return Decimal(str(rules["hst_rate"]))


def itc_pct(category, rules):
    return Decimal(str(rules["categories"][category]["itc_pct"]))


def back_calculate(total, rules):
    """HST included in a tax-inclusive total: total x rate / (1 + rate), i.e. 13/113 in Ontario."""
    r = rate(rules)
    return (total * r / (1 + r)).quantize(Decimal("0.01"))
