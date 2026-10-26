"""One command: generate synthetic receipts + bank statement, process, print summary and evaluation."""
import json
import sys
from pathlib import Path

from hst.evaluate import evaluate
from hst.generate import generate
from hst.pipeline import process_folder, summarize, write_outputs

ROOT = Path(__file__).parent
DATA, OUT = ROOT / "data", ROOT / "output"


def main(n=60, seed=7):
    truth = generate(DATA, n=n, seed=seed)
    receipts, bank = process_folder(DATA)
    summary = summarize(receipts, bank)
    write_outputs(receipts, bank, summary, OUT)
    report = evaluate(receipts, bank, truth)
    (OUT / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("SUMMARY\n" + json.dumps(summary, indent=2))
    print("\nEVALUATION\n" + json.dumps(report, indent=2))


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:3]))
