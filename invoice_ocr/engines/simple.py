"""Two light engines.

textlayer : no OCR at all, reads the text layer of a born-digital PDF. It is the
            upper bound of the extraction rules (what they score when reading is perfect).
rapidocr  : PP-OCR detection + recognition exported to ONNX (rapidocr-onnxruntime),
            ~15 MB of weights, CPU only. The light fallback.
"""
import time

import numpy as np

from .. import pages
from .base import Line, Reading

REC_REPO = "SWHL/RapidOCR"
REC_FILE = "PP-OCRv3/en_PP-OCRv3_rec_infer.onnx"
REC_REVISION = "1cfba2e90fc938db55889873735088de210cc173"


class TextLayer:
    name = "textlayer"
    device = "cpu"

    def read(self, path):
        import pypdfium2 as pdfium
        if not str(path).lower().endswith(".pdf"):
            raise ValueError("textlayer reads PDFs only")
        r = Reading(self.name, self.device)
        pdf = pdfium.PdfDocument(str(path))
        try:
            for i in range(len(pdf)):
                t0 = time.perf_counter()
                text = pdf[i].get_textpage().get_text_range()
                # content-stream order; one fake band per line keeps that order in rows()
                for k, line in enumerate(text.splitlines()):
                    y = (i * 10_000 + k) * 10
                    r.lines.append(Line(line, (0, y, 1, y + 8)))
                r.seconds_per_page.append(time.perf_counter() - t0)
        finally:
            pdf.close()
        return r


class RapidOCR:
    name = "rapidocr"
    device = "cpu"

    def __init__(self, threads=4):
        self.threads = threads
        self._engine = None

    def _load(self):
        if self._engine is None:
            from huggingface_hub import hf_hub_download
            from rapidocr_onnxruntime import RapidOCR as _R
            # The bundled recogniser is the Chinese one, which drops the spaces of Latin
            # text ("Summenetto"); the English PP-OCRv3 recogniser keeps them.
            rec = hf_hub_download(REC_REPO, REC_FILE, revision=REC_REVISION)
            self._engine = _R(rec_model_path=rec, intra_op_num_threads=self.threads,
                              inter_op_num_threads=1)
        return self._engine

    def read(self, path):
        eng = self._load()
        r = Reading(self.name, self.device)
        for img in pages.load(path):
            t0 = time.perf_counter()
            img, _ = pages.deskew(img)
            result, _ = eng(np.asarray(img))
            for box, text, score in result or []:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                r.lines.append(Line(text, (min(xs), min(ys), max(xs), max(ys))))
            r.seconds_per_page.append(time.perf_counter() - t0)
        return r
