"""Command line.

  python -m invoice_ocr generate                 # 40 fictitious invoices + ground truth in data/
  python -m invoice_ocr process FILE... [--engine rapidocr]
  python -m invoice_ocr watch --inbox in --outbox out --rejects rejects [--engine rapidocr]
  python -m invoice_ocr evaluate --engine rapidocr [--device cpu] [--limit N]
  python -m invoice_ocr rescore results/x.json   # rules changed, OCR rows reused
  python -m invoice_ocr stress                   # simulated misreads -> results/stress.json
  python -m invoice_ocr report                   # results/*.json -> results.md
"""
import argparse
import logging
import sys
from datetime import date

from . import engines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="invoice_ocr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--out", default="data")
    p = sub.add_parser("process")
    p.add_argument("files", nargs="+")
    p.add_argument("--engine", default="rapidocr", choices=engines.NAMES)
    p.add_argument("--device", default="auto")
    p.add_argument("--xml", action="store_true", help="print the XML")
    w = sub.add_parser("watch")
    w.add_argument("--inbox", default="watch/inbox")
    w.add_argument("--outbox", default="watch/outbox")
    w.add_argument("--rejects", default="watch/rejects")
    w.add_argument("--engine", default="rapidocr", choices=engines.NAMES)
    w.add_argument("--device", default="auto")
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--once", action="store_true")
    e = sub.add_parser("evaluate")
    e.add_argument("--engine", default="rapidocr", choices=engines.NAMES)
    e.add_argument("--device", default="auto")
    e.add_argument("--limit", type=int)
    e.add_argument("--data", default="data")
    rs = sub.add_parser("rescore", help="re-apply the rules to stored OCR rows")
    rs.add_argument("files", nargs="+")
    sub.add_parser("stress", help="simulated misreads on the text-layer run")
    sub.add_parser("report")
    a = ap.parse_args(argv)

    if a.cmd == "generate":
        from . import synth
        docs = synth.generate(a.out)
        print(f"{len(docs)} invoices written to {a.out}/")
    elif a.cmd == "process":
        from . import pipeline
        eng = engines.get(a.engine, a.device)
        for f in a.files:
            res = pipeline.process(f, eng, date.today())
            print(f"{f}: {res.status}", *(f"  - {i}" for i in res.issues), sep="\n")
            if res.error:
                print("  error:", res.error)
            if a.xml and res.xml:
                print(res.xml.decode())
    elif a.cmd == "watch":
        from . import watch
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        watch.run(a.inbox, a.outbox, a.rejects, engines.get(a.engine, a.device), a.interval, a.once)
    elif a.cmd == "evaluate":
        from . import evaluate
        print("written", evaluate.run(a.engine, a.data, device=a.device, limit=a.limit))
    elif a.cmd == "rescore":
        from . import evaluate
        for f in a.files:
            print("rescored", evaluate.rescore(f))
    elif a.cmd == "stress":
        from . import stress
        print("written", stress.run())
    elif a.cmd == "report":
        from . import report
        print("written", report.write())
    return 0


if __name__ == "__main__":
    sys.exit(main())
