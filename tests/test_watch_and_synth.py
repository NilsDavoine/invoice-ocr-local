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


def test_holdout_set_is_separate_and_has_the_unseen_layout():
    tune = synth.build_truth()
    held = synth.build_truth(synth.HOLDOUT_SEED, synth.N_HOLDOUT, holdout=True)
    assert len(held) == 30 and all(d["id"].startswith("TST-") for d in held)
    assert "D" in {d["template"] for d in held} and "D" not in {d["template"] for d in tune}
    assert not {d["supplier_name"] for d in held} & {d["supplier_name"] for d in tune}
    assert not {d["number"] for d in held} & {d["number"] for d in tune}
    assert sum(d["expected_status"] == "TO_VERIFY" for d in held) == 3
    assert 10 <= sum(d["scanned"] for d in held) <= 20


def test_layout_d_prints_what_the_truth_says(tmp_path):
    held = synth.build_truth(synth.HOLDOUT_SEED, synth.N_HOLDOUT, holdout=True)
    doc = next(d for d in held if d["template"] == "D")
    synth._render_pdf(doc, tmp_path / "d.pdf")
    text = "\n".join(TextLayer().read(tmp_path / "d.pdf").rows())
    assert doc["number"] in text and doc["supplier_vat"] in text and "SAMPLE" in text
