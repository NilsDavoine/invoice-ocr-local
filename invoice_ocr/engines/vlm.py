"""Layout detection + a small vision-language OCR model, the pair used by the real tool.

1. PP-DocLayoutV3 (PaddlePaddle, loaded through Transformers) finds the regions of the
   page: titles, paragraphs, tables...
2. Each region is cropped and read by Xiaomi-OCR-0 (0.8 B parameters, a Qwen3.5-VL
   fine-tune). Tables are asked for in OTSL and flattened to "cell | cell" rows.
3. Ink that no region covers (isolated totals are sometimes missed by the detector)
   is cut into extra regions and read too.

GPU: fp16 (a GTX 1650 has no bf16); if fp16 reads a known probe wrong, fp32, then CPU.
CPU: bf16. Everything stays on the machine; weights come from the Hugging Face cache.
"""
import re
import time

from .. import pages
from .base import Reading, split_block

OCR_MODEL, OCR_REVISION = "SeerRay-Lab/Xiaomi-OCR-0", "bbd8cf8d21f256b3644b44cb860f3a7cecb93c30"
LAYOUT_MODEL, LAYOUT_REVISION = "PaddlePaddle/PP-DocLayoutV3_safetensors", "e4f489dc536556fc1be02b973ba06756e0d4a2ac"
PROMPT_TEXT = "Extract the text in the image."
PROMPT_TABLE = "Parse the table in the image into OTSL."
SKIP = {"image", "chart", "seal", "number", "header_image", "footer_image"}


def otsl_to_rows(otsl: str) -> str:
    if "<fcel>" not in otsl and "<nl>" not in otsl:
        return otsl
    rows = []
    for raw in otsl.split("<nl>"):
        cells = [c.strip() for c in re.split(r"<(?:fcel|ecel|lcel|ucel|xcel|ched|rhed|srow)>", raw)]
        cells = [c for c in cells if c]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _dedupe(boxes, thr=0.7):
    kept = []
    for b in sorted(boxes, key=lambda b: -b["score"]):
        x0, y0, x1, y1 = b["box"]
        area = max(1.0, (x1 - x0) * (y1 - y0))
        clash = False
        for k in kept:
            kx0, ky0, kx1, ky1 = k["box"]
            inter = max(0, min(x1, kx1) - max(x0, kx0)) * max(0, min(y1, ky1) - max(y0, ky0))
            if inter / area > thr or inter / max(1.0, (kx1 - kx0) * (ky1 - ky0)) > thr:
                clash = True
                break
        if not clash:
            kept.append(b)
    return kept


def uncovered_ink(img, boxes, margin=6):
    """Text the layout model left outside every region, as extra 'text' regions."""
    import cv2
    import numpy as np
    ink = (np.asarray(img.convert("L")) < 150).astype(np.uint8)
    for b in boxes:
        x0, y0, x1, y1 = (int(v) for v in b["box"])
        ink[max(0, y0 - margin):y1 + margin, max(0, x0 - margin):x1 + margin] = 0
    blob = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (61, 15)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(blob)
    out = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        if ink[y:y + h, x:x + w].sum() < 150 or h > 400:   # dust, or the watermark
            continue
        out.append({"score": 0.0, "label": "text", "box": [float(x), float(y), float(x + w), float(y + h)]})
    return out


class XiaomiOCR:
    name = "xiaomi-ocr-0+pp-doclayoutv3"

    def __init__(self, device="auto", threads=4, max_new_tokens=768, threshold=0.3):
        self.requested = device
        self.threads, self.max_new_tokens, self.threshold = threads, max_new_tokens, threshold
        self.device = self.precision = None
        self.fallback = ""
        self.load_seconds = None
        self._loaded = False

    # -- loading -----------------------------------------------------------------
    def _load(self):
        if self._loaded:
            return
        t0 = time.perf_counter()
        import torch
        from transformers import (AutoImageProcessor, AutoModelForImageTextToText,
                                  AutoModelForObjectDetection, AutoProcessor)
        torch.set_num_threads(self.threads)
        self.torch = torch
        dev = self.requested
        if dev == "auto":
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        tries = [("cuda", torch.float16), ("cuda", torch.float32), ("cpu", torch.bfloat16)] \
            if dev == "cuda" else [("cpu", torch.bfloat16)]
        self.proc = AutoProcessor.from_pretrained(OCR_MODEL, revision=OCR_REVISION)
        self.layout_proc = AutoImageProcessor.from_pretrained(LAYOUT_MODEL, revision=LAYOUT_REVISION)
        for d, dtype in tries:
            try:
                self.model = None
                if d == "cuda":
                    torch.cuda.empty_cache()
                self.model = AutoModelForImageTextToText.from_pretrained(
                    OCR_MODEL, revision=OCR_REVISION, dtype=dtype).eval().to(d)
                self.device, self.precision = d, str(dtype).replace("torch.", "")
                if d == "cpu" or self._probe_ok():
                    break
                self.fallback += f"{self.precision} on GPU read the probe wrong; "
            except torch.cuda.OutOfMemoryError:
                self.fallback += f"{dtype} on GPU: out of memory; "
        self.layout = AutoModelForObjectDetection.from_pretrained(
            LAYOUT_MODEL, revision=LAYOUT_REVISION).eval().to(self.device)
        self.load_seconds = round(time.perf_counter() - t0, 1)
        self._loaded = True

    def _probe_ok(self):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (420, 90), "white")
        ImageDraw.Draw(img).text((15, 20), "TOTAL 12345", font=ImageFont.load_default(size=40), fill="black")
        return "12345" in self._generate(img, PROMPT_TEXT)

    # -- inference ---------------------------------------------------------------
    def _regions(self, img):
        with self.torch.inference_mode():
            out = self.layout(**self.layout_proc(images=img, return_tensors="pt").to(self.device))
        if self.device != "cpu":
            for k in list(out.keys()):
                if hasattr(out[k], "cpu"):
                    out[k] = out[k].float().cpu()
        r = self.layout_proc.post_process_object_detection(
            out, target_sizes=[img.size[::-1]], threshold=self.threshold)[0]
        boxes = []
        for score, lab, box in zip(r["scores"], r["labels"], r["boxes"]):
            label = self.layout.config.id2label[lab.item()]
            if label not in SKIP:
                boxes.append({"score": score.item(), "label": label, "box": [float(v) for v in box.tolist()]})
        return _dedupe(boxes)

    def _generate(self, img, prompt):
        msgs = [{"role": "user", "content": [{"type": "image", "image": img}, {"type": "text", "text": prompt}]}]
        inp = self.proc.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True,
                                            return_dict=True, return_tensors="pt").to(self.device)
        with self.torch.inference_mode():
            out = self.model.generate(**inp, max_new_tokens=self.max_new_tokens, do_sample=False)
        return self.proc.batch_decode(out[:, inp["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()

    def read(self, path):
        self._load()
        r = Reading(self.name, f"{self.device}/{self.precision}")
        for img in pages.load(path):
            t0 = time.perf_counter()
            img, _ = pages.deskew(img)
            W, H = img.size
            regions = self._regions(img)
            regions += uncovered_ink(img, regions)
            for reg in regions:
                x0, y0, x1, y1 = reg["box"]
                m = 6
                box = (max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m))
                table = reg["label"] == "table"
                text = self._generate(img.crop(box), PROMPT_TABLE if table else PROMPT_TEXT)
                if table:
                    text = otsl_to_rows(text)
                r.lines.extend(split_block(text, box, reg["label"]))
            r.seconds_per_page.append(time.perf_counter() - t0)
        return r
