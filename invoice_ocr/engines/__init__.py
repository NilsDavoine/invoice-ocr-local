"""Engine registry. Heavy imports (torch) happen only when the VLM engine is used."""


def get(name, device="auto"):
    if name == "textlayer":
        from .simple import TextLayer
        return TextLayer()
    if name == "rapidocr":
        from .simple import RapidOCR
        return RapidOCR()
    if name == "xiaomi":
        from .vlm import XiaomiOCR
        return XiaomiOCR(device=device)
    raise ValueError(f"unknown engine {name!r} (textlayer | rapidocr | xiaomi)")


NAMES = ("textlayer", "rapidocr", "xiaomi")
