"""Watched-folder mode: inbox -> outbox (OK) or rejects (TO_VERIFY, unreadable)."""
import logging
import time
from datetime import date
from pathlib import Path

from . import pages, pipeline

log = logging.getLogger("invoice_ocr.watch")


def _candidates(inbox):
    return sorted(p for p in Path(inbox).iterdir() if p.is_file() and not p.name.startswith("."))


def run(inbox, outbox, rejects, engine, interval=5.0, once=False, as_of=None):
    """Process files once their size is stable across two scans. `once`: process what is
    there now and return the list of (file name, status)."""
    for d in (inbox, outbox, rejects):
        Path(d).mkdir(parents=True, exist_ok=True)
    seen = {}
    done = []
    while True:
        for p in _candidates(inbox):
            size = p.stat().st_size
            if not once and seen.get(p) != size:   # still being copied?
                seen[p] = size
                continue
            seen.pop(p, None)
            if p.suffix.lower() not in pages.SUPPORTED:
                res = pipeline.Result(str(p), engine.name, status="REJECTED",
                                      error=f"unsupported file type {p.suffix}")
            else:
                res = pipeline.process(p, engine, as_of or date.today())
            pipeline.export(res, outbox, rejects, as_of or date.today())
            log.info("%s -> %s %s", p.name, res.status, ", ".join(i.code for i in res.issues))
            done.append((p.name, res.status))
        if once:
            return done
        time.sleep(interval)
