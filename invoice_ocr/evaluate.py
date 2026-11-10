"""Run one engine over the generated dataset and store raw per-invoice results as JSON.

The report (report.py) is computed from these files only; no number is typed by hand.
"""
import json
import platform
import re
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import engines, pipeline
from .textnorm import fold

AS_OF = date(2026, 11, 10)   # fixed "today", so that date checks are reproducible
FIELDS = ("supplier_name", "supplier_vat", "invoice_number", "issue_date", "due_date",
          "net_total", "tax_total", "gross_total", "vat_breakdown")


def _name(s):
    return re.sub(r"[\W_]", "", fold(s or ""))


def _got(ex):
    if ex is None:
        return {f: None for f in FIELDS}
    return {
        "supplier_name": _name(ex.supplier_name) or None,
        "supplier_vat": ex.supplier_vat,
        "invoice_number": (ex.invoice_number or "").upper() or None,
        "issue_date": str(ex.issue_date) if ex.issue_date else None,
        "due_date": str(ex.due_date) if ex.due_date else None,
        "net_total": str(ex.net_total) if ex.net_total is not None else None,
        "tax_total": str(ex.tax_total) if ex.tax_total is not None else None,
        "gross_total": str(ex.gross_total) if ex.gross_total is not None else None,
        "vat_breakdown": sorted([str(Decimal(r["rate"])), str(r["base"]), str(r["tax"])]
                                for r in ex.vat_breakdown),
    }


def _expected(t):
    return {
        "supplier_name": _name(t["supplier_name"]),
        "supplier_vat": t["supplier_vat"],
        "invoice_number": t["number"].upper(),
        "issue_date": t["issue_date"], "due_date": t["due_date"],
        "net_total": t["net_total"], "tax_total": t["tax_total"], "gross_total": t["gross_total"],
        "vat_breakdown": sorted([str(Decimal(r["rate"])), r["base"], r["tax"]] for r in t["vat_rows"]),
    }


def run(engine_name, data_dir="data", out_dir="results", device="auto", limit=None, ids=None):
    data = Path(data_dir)
    truth = json.loads((data / "truth.json").read_text(encoding="utf-8"))
    if ids:
        truth = [t for t in truth if t["id"] in ids]
    if limit:
        truth = truth[:limit]
    eng = engines.get(engine_name, device)
    records = []
    started = time.perf_counter()
    for t in truth:
        src = data / ("pdf" if engine_name == "textlayer" else "images") / \
            f"{t['id']}.{'pdf' if engine_name == 'textlayer' else 'png'}"
        res = pipeline.process(src, eng, AS_OF)
        got, exp = _got(res.extraction), _expected(t)
        correct = {f: got[f] == exp[f] for f in FIELDS}
        records.append({
            "id": t["id"], "lang": t["lang"], "template": t["template"],
            "scanned": t["scanned"] and engine_name != "textlayer",
            "anomaly": t["anomaly"], "expected_status": t["expected_status"],
            "status": res.status, "issues": sorted({i.code for i in res.issues}), "error": res.error,
            "correct": correct, "all_correct": all(correct.values()),
            "wrong": {f: [got[f], exp[f]] for f in FIELDS if not correct[f]},
            "seconds_per_page": [round(s, 3) for s in res.seconds_per_page],
            "rows": res.rows,
        })
        print(f"{t['id']} {res.status:9s} fields ok {sum(correct.values())}/{len(FIELDS)} "
              f"{res.seconds_total:6.1f}s {','.join(records[-1]['issues'])}", flush=True)
    meta = {
        "engine": engine_name, "device": getattr(eng, "device", None),
        "precision": getattr(eng, "precision", None), "fallback": getattr(eng, "fallback", ""),
        "load_seconds": getattr(eng, "load_seconds", None), "n": len(records),
        "subset": bool(limit or ids), "as_of": str(AS_OF),
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "wall_seconds": round(time.perf_counter() - started, 1),
        "machine": f"{platform.processor() or platform.machine()}, {_cpu()}", "gpu": _gpu(eng),
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dev = (meta["device"] or "cpu").split("/")[0]
    path = out / f"{engine_name}_{dev}{'_subset' if meta['subset'] else ''}.json"
    path.write_text(json.dumps({"meta": meta, "records": records}, indent=1), encoding="utf-8")
    return path


def rescore(json_path, data_dir="data"):
    """Re-run extraction, checks and XML on the OCR rows stored in a results file.
    Reading times are kept; used when only the rules changed, not the OCR."""
    from . import checks, ubl
    from .extract import extract
    path = Path(json_path)
    run_data = json.loads(path.read_text(encoding="utf-8"))
    truth = {t["id"]: t for t in json.loads((Path(data_dir) / "truth.json").read_text(encoding="utf-8"))}
    for r in run_data["records"]:
        if r["status"] == "REJECTED" and not r["rows"]:
            continue
        ex = extract(r["rows"])
        issues = checks.check(ex, AS_OF)
        status = checks.status(issues)
        try:
            pipeline.gate(ubl.build(ex, status, issues), AS_OF)
        except pipeline.GateError as e:
            status, r["error"] = "REJECTED", str(e)
        got, exp = _got(ex), _expected(truth[r["id"]])
        r["correct"] = {f: got[f] == exp[f] for f in FIELDS}
        r["all_correct"] = all(r["correct"].values())
        r["wrong"] = {f: [got[f], exp[f]] for f in FIELDS if not r["correct"][f]}
        r["status"], r["issues"] = status, sorted({i.code for i in issues})
    run_data["meta"]["rescored_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(run_data, indent=1), encoding="utf-8")
    return path


def _cpu():
    try:
        for line in open("/proc/cpuinfo", encoding="utf-8"):
            if line.startswith("model name"):
                import os
                return f"{line.split(':', 1)[1].strip()} ({os.cpu_count()} threads)"
    except OSError:
        pass
    return platform.machine()


def _gpu(eng):
    if not str(getattr(eng, "device", "")).startswith("cuda"):
        return None
    import torch
    return torch.cuda.get_device_name(0)
