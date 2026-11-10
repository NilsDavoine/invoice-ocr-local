"""Write, validate and read back the UBL 2.1 / EN 16931 header subset."""
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from lxml import etree

NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
CUSTOMIZATION = "urn:cen.eu:en16931:2017"
SCHEMA = Path(__file__).parent / "schema" / "ubl-subset-invoice.xsd"
CURRENCY = "EUR"


def _cbc(parent, tag, text, **attrs):
    el = etree.SubElement(parent, f"{{{NS['cbc']}}}{tag}", **attrs)
    el.text = str(text)
    return el


def _cac(parent, tag):
    return etree.SubElement(parent, f"{{{NS['cac']}}}{tag}")


def status_note(status, issues):
    if status == "OK":
        return "#AAI#STATUS=OK"
    codes = sorted({i.code for i in issues}) or ["UNSPECIFIED"]
    return "#AAI#STATUS=TO_VERIFY;" + ",".join(codes)


def build(ex, status, issues) -> bytes:
    root = etree.Element(f"{{{NS['inv']}}}Invoice", nsmap={None: NS["inv"], "cac": NS["cac"], "cbc": NS["cbc"]})
    _cbc(root, "CustomizationID", CUSTOMIZATION)
    if ex.invoice_number:
        _cbc(root, "ID", ex.invoice_number)
    if ex.issue_date:
        _cbc(root, "IssueDate", ex.issue_date.isoformat())
    if ex.due_date:
        _cbc(root, "DueDate", ex.due_date.isoformat())
    _cbc(root, "InvoiceTypeCode", "380")
    _cbc(root, "Note", status_note(status, issues))
    _cbc(root, "DocumentCurrencyCode", CURRENCY)

    party = _cac(_cac(root, "AccountingSupplierParty"), "Party")
    if ex.supplier_vat:
        pts = _cac(party, "PartyTaxScheme")
        _cbc(pts, "CompanyID", ex.supplier_vat)
        _cbc(_cac(pts, "TaxScheme"), "ID", "VAT")
    if ex.supplier_name:
        _cbc(_cac(party, "PartyLegalEntity"), "RegistrationName", ex.supplier_name)

    if ex.tax_total is not None:
        tt = _cac(root, "TaxTotal")
        _cbc(tt, "TaxAmount", f"{ex.tax_total:.2f}", currencyID=CURRENCY)
        for r in ex.vat_breakdown:
            st = _cac(tt, "TaxSubtotal")
            _cbc(st, "TaxableAmount", f"{r['base']:.2f}", currencyID=CURRENCY)
            _cbc(st, "TaxAmount", f"{r['tax']:.2f}", currencyID=CURRENCY)
            cat = _cac(st, "TaxCategory")
            _cbc(cat, "ID", "S")              # standard-rated (UNCL 5305)
            _cbc(cat, "Percent", r["rate"])
            _cbc(_cac(cat, "TaxScheme"), "ID", "VAT")

    lmt = _cac(root, "LegalMonetaryTotal")
    if ex.net_total is not None:
        _cbc(lmt, "LineExtensionAmount", f"{ex.net_total:.2f}", currencyID=CURRENCY)
        _cbc(lmt, "TaxExclusiveAmount", f"{ex.net_total:.2f}", currencyID=CURRENCY)
    if ex.gross_total is not None:
        _cbc(lmt, "TaxInclusiveAmount", f"{ex.gross_total:.2f}", currencyID=CURRENCY)
        _cbc(lmt, "PayableAmount", f"{ex.gross_total:.2f}", currencyID=CURRENCY)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


@lru_cache(maxsize=1)
def _schema():
    return etree.XMLSchema(etree.parse(str(SCHEMA)))


def validate(xml: bytes):
    """List of schema errors (empty when valid)."""
    try:
        doc = etree.fromstring(xml)
    except etree.XMLSyntaxError as e:
        return [str(e)]
    s = _schema()
    return [] if s.validate(doc) else [f"line {e.line}: {e.message}" for e in s.error_log]


def read(xml: bytes):
    """Back from XML to the fields the checks need, plus the declared status."""
    from .extract import Extraction
    doc = etree.fromstring(xml)
    t = lambda path: doc.findtext(path, namespaces=NS)  # noqa: E731
    dec = lambda s: Decimal(s) if s is not None else None  # noqa: E731
    day = lambda s: date.fromisoformat(s) if s else None  # noqa: E731
    ex = Extraction(
        supplier_name=t("cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName"),
        supplier_vat=t("cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID"),
        invoice_number=t("cbc:ID"), issue_date=day(t("cbc:IssueDate")), due_date=day(t("cbc:DueDate")),
        net_total=dec(t("cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount")),
        tax_total=dec(t("cac:TaxTotal/cbc:TaxAmount")),
        gross_total=dec(t("cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount")),
    )
    for st in doc.findall("cac:TaxTotal/cac:TaxSubtotal", NS):
        rate = Decimal(st.findtext("cac:TaxCategory/cbc:Percent", namespaces=NS))
        ex.vat_breakdown.append({"rate": int(rate) if rate == rate.to_integral() else rate,
                                 "base": Decimal(st.findtext("cbc:TaxableAmount", namespaces=NS)),
                                 "tax": Decimal(st.findtext("cbc:TaxAmount", namespaces=NS))})
    note = t("cbc:Note") or ""
    status = "OK" if note == "#AAI#STATUS=OK" else ("TO_VERIFY" if "STATUS=TO_VERIFY" in note else None)
    return ex, status
