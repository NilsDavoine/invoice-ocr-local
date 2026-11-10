"""RapidOCR end to end on one generated page: the light OCR engine, about 15 MB of weights.

Skipped when the recogniser cannot be downloaded, unless REQUIRE_RAPIDOCR=1 (set in CI)."""
import os
from datetime import date

import pytest

from invoice_ocr import pipeline, synth


@pytest.fixture(scope="module")
def engine():
    from invoice_ocr.engines.simple import RapidOCR
    eng = RapidOCR()
    try:
        eng._load()
    except Exception as e:  # no network, no cache
        if os.environ.get("REQUIRE_RAPIDOCR") == "1":
            raise
        pytest.skip(f"RapidOCR not available: {type(e).__name__}")
    return eng


def test_rapidocr_reads_a_clean_page_and_exports_it(engine, tmp_path):
    doc = synth.build_truth(n=3)[2]          # INV-02: coherent, not an anomaly
    synth._render_pdf(doc, tmp_path / "p.pdf")
    synth.render_page(tmp_path / "p.pdf").save(tmp_path / "p.png")
    res = pipeline.process(tmp_path / "p.png", engine, date(2026, 11, 10))
    ex = res.extraction
    assert ex.gross_total == doc["gross_total"] and ex.supplier_vat == doc["supplier_vat"]
    assert res.status == "OK"
