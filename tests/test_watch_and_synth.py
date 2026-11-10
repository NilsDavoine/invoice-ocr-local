import hashlib
import json
import shutil
from datetime import date

from invoice_ocr import synth, ubl, watch
from invoice_ocr.engines.simple import TextLayer


def test_generator_is_deterministic():
    a = json.dumps([synth._json(d) for d in synth.build_truth()], sort_keys=True)
    b = json.dumps([synth._json(d) for d in synth.build_truth()], sort_keys=True)
    assert a == b
    truth = synth.build_truth()
    assert len(truth) == 40
    assert {d["lang"] for d in truth} == {"fr", "en", "de"}
    assert sum(d["expected_status"] == "TO_VERIFY" for d in truth) == 4


def test_generated_pages_carry_the_watermark_and_no_iban(small_dataset):
    text = TextLayer().read(small_dataset / "pdf" / "INV-00.pdf").rows()
    assert "SAMPLE" in text
    assert any("SAMPLE-NOT-AN-IBAN" in r for r in text)


def test_watched_folder(small_dataset, tmp_path):
    inbox, outbox, rejects = tmp_path / "in", tmp_path / "out", tmp_path / "rejects"
    inbox.mkdir()
    shutil.copy(small_dataset / "pdf" / "INV-00.pdf", inbox)          # coherent
    shutil.copy(small_dataset / "pdf" / "INV-07.pdf", inbox)          # wrong total, on purpose
    (inbox / "notes.txt").write_text("not an invoice")
    done = dict(watch.run(inbox, outbox, rejects, TextLayer(), once=True, as_of=date(2026, 11, 10)))
    assert done == {"INV-00.pdf": "OK", "INV-07.pdf": "TO_VERIFY", "notes.txt": "REJECTED"}
    assert sorted(p.name for p in outbox.iterdir()) == ["INV-00.pdf", "INV-00.xml"]
    assert ubl.read((rejects / "INV-07.xml").read_bytes())[1] == "TO_VERIFY"
    assert (rejects / "notes.txt").exists() and (rejects / "notes.reasons.txt").exists()
    assert not any(inbox.iterdir())
