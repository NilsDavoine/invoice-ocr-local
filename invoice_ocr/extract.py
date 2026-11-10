"""Rules that turn OCR rows into invoice fields (FR / EN / DE labels).

Deliberately simple and explainable: a label, then a value on the same row. No
arithmetic is used to *guess* a value; arithmetic belongs to the checks, so that a
misread amount is caught instead of being silently repaired.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from . import vat
from .textnorm import AMOUNT_RE, amounts_in, find_date, fold

LABELS = {
    "number": ["facture n", "invoice no", "invoice number", "invoice #", "rechnung nr",
               "rechnungsnummer", "rechnungs-nr"],
    "issue_date": ["date de facture", "invoice date", "rechnungsdatum", "date", "datum"],
    "due_date": ["date d'echeance", "echeance", "payable avant le", "a payer avant", "due date",
                 "payable by", "payment due", "fallig am", "zahlbar bis"],
    "delivery": ["date de livraison", "delivery date", "lieferdatum"],
    "net": ["total ht", "montant ht", "sous-total", "subtotal", "net total", "total excl",
            "summe netto", "nettobetrag", "zwischensumme"],
    "tax_total": ["total tva", "total vat", "vat total", "summe mwst", "mwst gesamt"],
    "gross": ["total ttc", "montant ttc", "net a payer", "total due", "amount due", "total incl",
              "gesamtbetrag", "bruttobetrag", "rechnungsbetrag", "zu zahlen"],
    "customer": ["client", "customer", "kunde", "bill to", "facture a", "rechnungsempfanger"],
}
NOT_A_NAME = {"facture", "invoice", "rechnung", "sample"}
VAT_DIGITS = {"LU": 8, "BE": 10, "FR": 11, "DE": 9}
RATE_RE = re.compile(r"(?<!\d)(?<!\d[.,])(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
VAT_RE = re.compile(r"(?<![A-Z])(LU|BE|FR|DE)\s*[:.]?\s*((?:\d[\s.]?){8,11})")
NUMBER_RE = re.compile(r"[A-Z0-9][A-Z0-9/_.\-]*[A-Z0-9]", re.I)


def _strip_accents(s):
    s = unicodedata.normalize("NFKD", s.replace("ß", "ss"))
    return "".join(c for c in s if not unicodedata.combining(c)).replace("\t", "  ").strip()  # keep "  " separators


def _label_rx(label):
    """'total ht' -> tolerant of missing/extra spaces, as OCR often merges words."""
    chars = [re.escape(c) for c in label.replace(" ", "")]
    return re.compile(r"(?<![a-z])" + r"\s*".join(chars), re.I)


_RX = {k: [_label_rx(lab) for lab in v] for k, v in LABELS.items()}


def _find_label(row, key):
    """End position of the first label of `key` in `row`, or None."""
    best = None
    for rx in _RX[key]:
        m = rx.search(row)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m.end())
    return best[1] if best else None


@dataclass
class Extraction:
    supplier_name: str = None
    supplier_vat: str = None
    invoice_number: str = None
    issue_date: object = None
    due_date: object = None
    net_total: Decimal = None
    tax_total: Decimal = None
    gross_total: Decimal = None
    vat_breakdown: list = field(default_factory=list)  # [{"rate", "base", "tax"}]
    tax_total_printed: bool = False
    evidence: dict = field(default_factory=dict)        # field -> row it came from


def _supplier_name(rows):
    for row in rows[:6]:
        for seg in re.split(r"\s{2,}|\s\|\s", row):
            s = seg.strip()
            if len(s) < 3 or not re.search(r"[A-Za-z]{2}", s) or fold(s) in NOT_A_NAME:
                continue
            return s
    return None


def _supplier_vat(rows):
    for row in rows:
        if _find_label(row, "customer") is not None:
            continue
        for m in VAT_RE.finditer(row.upper()):
            country = m.group(1)
            digits = re.sub(r"\D", "", m.group(2))[:VAT_DIGITS[country]]
            if len(digits) == VAT_DIGITS[country]:
                return country + digits, row
    return None, None


def _invoice_number(rows):
    for i, row in enumerate(rows):
        end = _find_label(row, "number")
        if end is None:
            continue
        rest = re.sub(r"^\s*[°º'’\"o]?\.?\s*[:#|]?\s*", "", row[end:])   # "|": a table cell
        m = NUMBER_RE.match(rest)
        if not m and i + 1 < len(rows) and not _has_any_label(rows[i + 1]):
            m = NUMBER_RE.match(rows[i + 1].strip())
        if m and any(ch.isdigit() for ch in m.group()):
            number = m.group()
            # "Facture n° X du 12/03/2026" read without the space: "Xdu"
            glued = re.match(r"(.+\d)(du|of|vom)$", number, re.I)
            if glued and re.match(r"\s*\d", rest[m.end():]):
                number = glued.group(1)
            return number, row, i
    return None, None, None


def _has_any_label(row):
    return any(_find_label(row, k) is not None for k in ("number", "issue_date", "due_date", "delivery"))


def _date_for(rows, key, exclude=()):
    for i, row in enumerate(rows):
        end = _find_label(row, key)
        if end is None or any(_find_label(row, x) is not None for x in exclude):
            continue
        d = find_date(row[end:])
        if d is None and i + 1 < len(rows) and not _has_any_label(rows[i + 1]):
            d = find_date(rows[i + 1])   # value printed under its label
        if d:
            return d, row
    return None, None


def _amount_for(rows, key):
    for row in reversed(rows):              # totals sit at the bottom of the page
        end = _find_label(row, key)
        if end is None:
            continue
        found = amounts_in(row[end:])
        if found:
            return found[-1], row
    return None, None


def _breakdown(rows):
    """Rows with a rate and at least two amounts after it: rate, taxable base, VAT.
    Line items have only one amount after their rate, so they are not taken."""
    out, seen = [], set()
    for row in rows:
        for m in RATE_RE.finditer(row):
            after = [a for a in AMOUNT_RE.finditer(row[m.end():])]
            if len(after) < 2:
                continue
            rate = Decimal(m.group(1).replace(",", "."))
            rate = int(rate) if rate == rate.to_integral() else rate
            if rate in seen:
                break
            seen.add(rate)
            vals = amounts_in(row[m.end():])
            out.append({"rate": rate, "base": vals[0], "tax": vals[-1]})
            break
    return out


def extract(rows) -> Extraction:
    rows = [_strip_accents(r) for r in rows if r and r.strip()]
    rows = [r for r in rows if fold(r) != "sample"]
    ex = Extraction()
    ex.supplier_name = _supplier_name(rows)
    ex.supplier_vat, ex.evidence["supplier_vat"] = _supplier_vat(rows)
    ex.invoice_number, ex.evidence["invoice_number"], num_row = _invoice_number(rows)
    ex.due_date, ex.evidence["due_date"] = _date_for(rows, "due_date")
    ex.issue_date, ex.evidence["issue_date"] = _date_for(rows, "issue_date", exclude=("due_date", "delivery"))
    if ex.issue_date is None and num_row is not None:   # "Invoice No. X of 12 March 2026"
        ex.issue_date = find_date(rows[num_row])
        ex.evidence["issue_date"] = rows[num_row]
    ex.net_total, ex.evidence["net_total"] = _amount_for(rows, "net")
    ex.gross_total, ex.evidence["gross_total"] = _amount_for(rows, "gross")
    ex.vat_breakdown = _breakdown(rows)
    tax, ex.evidence["tax_total"] = _amount_for(rows, "tax_total")
    if tax is not None:
        ex.tax_total, ex.tax_total_printed = tax, True
    elif ex.vat_breakdown:   # not printed: the sum of the breakdown is the only source
        ex.tax_total = sum((r["tax"] for r in ex.vat_breakdown), Decimal("0"))
    if ex.supplier_vat:
        ex.supplier_vat = vat.normalise(ex.supplier_vat)
    return ex
