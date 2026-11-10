"""Mutation test of the guard.

For each mutation: copy the file aside, break it on purpose, run the test suite and
expect it to FAIL; then restore the file from the backup copy (never from git) and
expect the suite to pass again. Writes results/mutation.json.

    python -m tools.mutate
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MUTATIONS = [
    ("totals check switched off", "invoice_ocr/checks.py",
     "abs(net + tax - gross) > TOL:", "abs(net + tax - gross) > TOL and False:"),
    ("status ignores the issues", "invoice_ocr/checks.py",
     'return "TO_VERIFY" if issues else "OK"', 'return "OK"'),
    ("gate trusts the declared status", "invoice_ocr/pipeline.py",
     'if declared == "OK" and issues:', 'if declared == "OK" and issues and False:'),
    ("export skips the gate", "invoice_ocr/pipeline.py",
     'ok = gate(res.xml, as_of) == "OK"', 'ok = True'),
    ("schema accepts any status note", "invoice_ocr/schema/ubl-subset-cbc.xsd",
     '<xs:pattern value="#AAI#STATUS=(OK|TO_VERIFY;[A-Z_]+(,[A-Z_]+)*)"/>', '<xs:pattern value=".*"/>'),
]


def run_tests():
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    last = (p.stdout.strip().splitlines() or ["?"])[-1]
    return p.returncode, last


def main():
    code, line = run_tests()
    if code != 0:
        sys.exit(f"suite must be green before mutating: {line}")
    results = []
    backup_dir = Path(tempfile.mkdtemp(prefix="mutation-backup-"))
    for name, rel, old, new in MUTATIONS:
        target = ROOT / rel
        backup = backup_dir / target.name
        shutil.copy2(target, backup)
        src = target.read_text(encoding="utf-8")
        if src.count(old) != 1:
            sys.exit(f"{name}: pattern not found exactly once in {rel}")
        try:
            target.write_text(src.replace(old, new), encoding="utf-8")
            code_mut, line_mut = run_tests()
        finally:
            shutil.copy2(backup, target)          # restore from the copy, not from git
        code_back, line_back = run_tests()
        ok = code_mut != 0 and code_back == 0
        results.append({"name": name, "file": rel, "with_mutation": f"{'RED' if code_mut else 'GREEN'}: {line_mut}",
                        "after_restore": f"{'GREEN' if code_back == 0 else 'RED'}: {line_back}", "ok": ok})
        print(f"[{'ok' if ok else 'FAIL'}] {name}: mutated -> {line_mut} | restored -> {line_back}")
    shutil.rmtree(backup_dir)
    out = ROOT / "results" / "mutation.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"run_at": datetime.now().isoformat(timespec="seconds"),
                               "mutations": results}, indent=1), encoding="utf-8")
    sys.exit(0 if all(r["ok"] for r in results) else 1)


if __name__ == "__main__":
    main()
