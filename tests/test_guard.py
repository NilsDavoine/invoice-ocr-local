"""The guard: an incoherent invoice never leaves as XML without the TO_VERIFY status.

Mutation-tested by tools/mutate.py (see results.md).
"""
import random
from datetime import date

import pytest

from invoice_ocr import checks, pipeline, ubl
from invoice_ocr.extract import extract
from invoice_ocr.pipeline import GateError, gate

from .conftest import FakeEngine

AS_OF = date(2026, 11, 10)


def _run(rows, tmp_path):
    src = tmp_path / "in" / "doc.png"
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(b"not read by the fake engine")
    res = pipeline.process(src, FakeEngine(rows), AS_OF)
    pipeline.export(res, tmp_path / "out", tmp_path / "rejects", AS_OF)
    return res


def test_coherent_invoice_is_exported_ok(coherent_rows, tmp_path):
    res = _run(coherent_rows, tmp_path)
    assert res.status == "OK"
    assert ubl.read((tmp_path / "out" / "doc.xml").read_bytes())[1] == "OK"
    assert not (tmp_path / "rejects").exists() or not any((tmp_path / "rejects").iterdir())


def test_wrong_total_goes_to_rejects_as_to_verify(coherent_rows, tmp_path):
    coherent_rows[8] = "Total TTC  1 152,00 €"       # 1000 + 142 != 1152
    res = _run(coherent_rows, tmp_path)
    assert res.status == "TO_VERIFY"
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())
    xml = (tmp_path / "rejects" / "doc.xml").read_bytes()
    assert ubl.read(xml)[1] == "TO_VERIFY"
    assert b"TOTALS_MISMATCH" in xml
    assert "TOTALS_MISMATCH" in (tmp_path / "rejects" / "doc.reasons.txt").read_text()


def test_gate_refuses_ok_status_on_incoherent_content(coherent_rows):
    coherent_rows[8] = "Total TTC  1 152,00 €"
    forged = ubl.build(extract(coherent_rows), "OK", [])   # a bug upstream claims OK
    with pytest.raises(GateError):
        gate(forged, AS_OF)


def test_export_never_writes_a_forged_ok(coherent_rows, tmp_path):
    coherent_rows[8] = "Total TTC  1 152,00 €"
    src = tmp_path / "doc.png"
    src.write_bytes(b"x")
    res = pipeline.Result(str(src), "fake", status="OK", issues=[],
                          extraction=extract(coherent_rows))
    res.xml = ubl.build(res.extraction, "OK", [])
    assert pipeline.export(res, tmp_path / "out", tmp_path / "rejects", AS_OF) is None
    assert res.status == "REJECTED"
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_random_misreads_never_leave_as_ok(coherent_rows, tmp_path):
    """Simulated OCR errors: one digit of one amount changed at random. Whatever the
    change, the result is either still coherent or flagged; never exported OK when the
    content fails a check."""
    rng = random.Random(7)
    amount_rows = [4, 5, 6, 7, 8]
    for k in range(300):
        rows = list(coherent_rows)
        i = rng.choice(amount_rows)
        digits = [p for p, ch in enumerate(rows[i]) if ch.isdigit()]
        p = rng.choice(digits)
        rows[i] = rows[i][:p] + str((int(rows[i][p]) + rng.randint(1, 9)) % 10) + rows[i][p + 1:]
        d = tmp_path / str(k)
        d.mkdir()
        res = _run(rows, d)
        incoherent = bool(checks.check(extract(rows), AS_OF))
        if incoherent:
            assert res.status != "OK", rows
            assert not (d / "out").exists() or not any((d / "out").iterdir()), rows
