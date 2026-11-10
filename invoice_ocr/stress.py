"""Simulated misreads: how many reading errors do the checks catch?

The OCR engines rarely misread the digits of these clean synthetic pages, so the
checks are barely exercised by them. Here, starting from a perfect reading (the PDF
text layer), one digit of one row is replaced, preferring the confusions OCR actually
makes (3/8, 1/7, 5/6, 0/8...). Whenever that changes an extracted field, we record
whether the invoice was still exported OK (a silent error) or flagged.
"""
import json
import random
from pathlib import Path

from . import checks
from .evaluate import AS_OF, FIELDS, _expected, _got
from .extract import extract

CONFUSIONS = {"0": "86", "1": "74", "2": "7", "3": "85", "4": "1", "5": "63", "6": "58",
              "7": "12", "8": "306", "9": "4"}
PER_INVOICE = 50
SEED = 11


def run(textlayer_json="results/textlayer_cpu.json", data_dir="data", out="results/stress.json"):
    recs = json.loads(Path(textlayer_json).read_text(encoding="utf-8"))["records"]
    truth = {t["id"]: t for t in json.loads((Path(data_dir) / "truth.json").read_text(encoding="utf-8"))}
    rng = random.Random(SEED)
    per_field = {f: {"changed": 0, "flagged": 0} for f in FIELDS}
    trials = changed = flagged = 0
    silent_examples = []
    for r in recs:
        if truth[r["id"]]["anomaly"]:
            continue
        rows, exp = r["rows"], _expected(truth[r["id"]])
        spots = [(i, p) for i, row in enumerate(rows) for p, ch in enumerate(row) if ch.isdigit()]
        for _ in range(PER_INVOICE):
            i, p = rng.choice(spots)
            old = rows[i][p]
            new = rng.choice(CONFUSIONS[old]) if rng.random() < 0.7 else \
                rng.choice([d for d in "0123456789" if d != old])
            mutated = list(rows)
            mutated[i] = rows[i][:p] + new + rows[i][p + 1:]
            ex = extract(mutated)
            got = _got(ex)
            diff = [f for f in FIELDS if got[f] != exp[f]]
            trials += 1
            if not diff:
                continue                     # the digit was in a line item, an address...
            changed += 1
            is_flagged = bool(checks.check(ex, AS_OF))
            flagged += is_flagged
            for f in diff:
                per_field[f]["changed"] += 1
                per_field[f]["flagged"] += is_flagged
            if not is_flagged and len(silent_examples) < 12:
                silent_examples.append({"id": r["id"], "fields": diff, "row": mutated[i]})
    result = {"trials": trials, "changed_a_field": changed, "flagged": flagged,
              "per_field": per_field, "silent_examples": silent_examples,
              "per_invoice": PER_INVOICE, "seed": SEED}
    Path(out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
