"""One file in, one decision out: layout -> reading -> fields -> checks -> XML -> gate."""
import shutil
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import checks, ubl
from .extract import extract


class GateError(RuntimeError):
    """Raised when an XML is about to leave with a status its content does not support."""


def gate(xml: bytes, as_of: date):
    """Last line of defence, independent from the code that chose the status.

    The XML is validated against the schema, read back, and checked again. An "OK"
    status on content that fails any check is refused: an incoherent invoice can only
    leave as TO_VERIFY.
    """
    errors = ubl.validate(xml)
    if errors:
        raise GateError("schema: " + "; ".join(errors[:3]))
    ex, declared = ubl.read(xml)
    if declared is None:
        raise GateError("no extraction status in the XML")
    issues = checks.check(ex, as_of)
    if declared == "OK" and issues:
        raise GateError("status OK on incoherent content: " + "; ".join(map(str, issues)))
    return declared


@dataclass
class Result:
    source: str
    engine: str
    status: str = None                 # OK | TO_VERIFY | REJECTED
    issues: list = field(default_factory=list)
    extraction: object = None
    xml: bytes = None
    rows: list = field(default_factory=list)
    seconds_per_page: list = field(default_factory=list)
    seconds_total: float = 0.0
    error: str = None


def process(path, engine, as_of: date = None) -> Result:
    as_of = as_of or date.today()
    t0 = time.perf_counter()
    res = Result(str(path), engine.name)
    try:
        reading = engine.read(path)
    except Exception as e:  # unreadable file: rejected, never exported
        res.status, res.error = "REJECTED", f"{type(e).__name__}: {e}"
        res.seconds_total = time.perf_counter() - t0
        return res
    res.rows, res.seconds_per_page = reading.rows(), reading.seconds_per_page
    res.extraction = extract(res.rows)
    res.issues = checks.check(res.extraction, as_of)
    res.status = checks.status(res.issues)
    res.xml = ubl.build(res.extraction, res.status, res.issues)
    try:
        gate(res.xml, as_of)
    except GateError as e:
        res.status, res.error, res.xml = "REJECTED", str(e), None
    res.seconds_total = time.perf_counter() - t0
    return res


def export(res: Result, out_dir, rejects_dir, as_of: date = None, move_source=True):
    """OK -> out_dir (XML + source). Everything else -> rejects_dir, with the reasons.

    Returns the path of the XML written, or None.
    """
    as_of = as_of or date.today()
    src = Path(res.source)
    xml_path = None
    ok = False
    if res.status == "OK" and res.xml is not None:
        try:
            ok = gate(res.xml, as_of) == "OK"
        except GateError as e:          # never write an XML the gate refused
            res.status, res.error, res.xml = "REJECTED", str(e), None
    dest = Path(out_dir) if ok else Path(rejects_dir)
    dest.mkdir(parents=True, exist_ok=True)
    if res.xml is not None:
        xml_path = dest / (src.stem + ".xml")
        xml_path.write_bytes(res.xml)
    if dest == Path(rejects_dir):
        lines = [f"status: {res.status}"] + [str(i) for i in res.issues]
        if res.error:
            lines.append(f"error: {res.error}")
        (dest / (src.stem + ".reasons.txt")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if move_source and src.exists():
        shutil.move(str(src), str(dest / src.name))
    return xml_path
