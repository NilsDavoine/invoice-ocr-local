import json
from datetime import date
from decimal import Decimal

import pytest

from invoice_ocr import checks, ubl
from invoice_ocr.engines.simple import TextLayer
from invoice_ocr.evaluate import FIELDS, _expected, _got
from invoice_ocr.extract import extract

AS_OF = date(2026, 11, 10)


def test_extract_coherent_rows(coherent_rows):
    ex = extract(coherent_rows)
    assert ex.supplier_name == "Fictiva Bureautique S.a r.l."
    assert ex.supplier_vat == "LU12345613"
    assert ex.invoice_number == "F-2026-0101"
    assert (ex.issue_date, ex.due_date) == (date(2026, 3, 12), date(2026, 4, 11))
    assert (ex.net_total, ex.tax_total, ex.gross_total) == (Decimal("1000.00"), Decimal("142.00"), Decimal("1142.00"))
    assert [r["rate"] for r in ex.vat_breakdown] == [17, 3]
    assert checks.check(ex, AS_OF) == []


def test_ocr_without_spaces_still_extracts():
    rows = ["Testwerk GmbH", "USt-IdNr.DE399687455", "RechnungNr.2026002-A vom 30.09.2026",
            "Zahlbar bis 30.10.2026", "Summenetto  100,00", "MwSt.17%auf100,00  17,00", "Gesamtbetrag  117,00"]
    ex = extract(rows)
    assert ex.invoice_number == "2026002-A" and ex.issue_date == date(2026, 9, 30)
    assert ex.net_total == Decimal("100.00") and ex.gross_total == Decimal("117.00")
    assert ex.vat_breakdown == [{"rate": 17, "base": Decimal("100.00"), "tax": Decimal("17.00")}]


def test_value_in_a_table_cell():
    rows = ["Dummy Garage", "Facture n° | RE2026/300", "77, rue du Prototype"]
    assert extract(rows).invoice_number == "RE2026/300"


def test_line_items_are_not_taken_as_vat_breakdown():
    rows = ["Toner  2  50,00 €  17 %  100,00 €", "Total HT 100,00 €", "TVA 17 % sur 100,00 € 17,00 €"]
    assert extract(rows).vat_breakdown == [{"rate": 17, "base": Decimal("100.00"), "tax": Decimal("17.00")}]


def test_rules_read_every_generated_pdf_exactly(small_dataset):
    """Upper bound: with a perfect reading (PDF text layer) every field is right."""
    truth = json.loads((small_dataset / "truth.json").read_text(encoding="utf-8"))
    for t in truth:
        ex = extract(TextLayer().read(small_dataset / "pdf" / f"{t['id']}.pdf").rows())
        got, exp = _got(ex), _expected(t)
        assert {f: got[f] for f in FIELDS} == exp, t["id"]
        assert checks.status(checks.check(ex, AS_OF)) == t["expected_status"], t["id"]


@pytest.mark.parametrize("mutate, code", [
    (lambda r: r.__setitem__(8, "Total TTC  1 152,00 €"), "TOTALS_MISMATCH"),
    (lambda r: r.__setitem__(8, "Total TTC  1 142,01 €"), "TOTALS_MISMATCH"),   # one cent is enough
    (lambda r: r.__setitem__(5, "TVA 20 % sur 800,00 €  136,00 €"), "RATE_NOT_LU"),
    (lambda r: r.__setitem__(5, "TVA 17 % sur 800,00 €  163,00 €"), "RATE_TAX"),
    (lambda r: r.__setitem__(4, "Total HT  1 008,00 €"), "BREAKDOWN_NET"),
    (lambda r: r.__setitem__(2, "N° TVA : LU12345614  Date : 12/03/2026"), "VAT_ID_INVALID"),
    (lambda r: r.__setitem__(3, "Échéance : 01/03/2026"), "DUE_BEFORE_ISSUE"),
    (lambda r: r.__setitem__(3, "Échéance : 11/12/2026"), "DUE_TOO_LATE"),
    (lambda r: r.__setitem__(2, "N° TVA : LU12345613  Date : 12/03/2027"), "DATE_IN_FUTURE"),
    (lambda r: r.__setitem__(2, "N° TVA : LU12345613  Date : 12/03/2024"), "DATE_TOO_OLD"),
    (lambda r: r.__setitem__(1, "12, rue des Exemples"), "MISSING_FIELD"),
])
def test_each_check_fires(coherent_rows, mutate, code):
    mutate(coherent_rows)
    codes = {i.code for i in checks.check(extract(coherent_rows), AS_OF)}
    assert code in codes


def test_xml_roundtrip_and_schema(coherent_rows):
    ex = extract(coherent_rows)
    xml = ubl.build(ex, "OK", [])
    assert ubl.validate(xml) == []
    back, status = ubl.read(xml)
    assert status == "OK"
    assert _got(back) == _got(ex)


def test_schema_requires_a_well_formed_status(coherent_rows):
    ex = extract(coherent_rows)
    ok = ubl.build(ex, "OK", []).decode()
    assert ubl.validate(ok.replace("#AAI#STATUS=OK", "#AAI#STATUS=TO_VERIFY").encode())   # no reason
    assert ubl.validate(ok.replace("#AAI#STATUS=OK", "#AAI#STATUS=OK;TOTALS_MISMATCH").encode())
    no_note = ok.replace("<cbc:Note>#AAI#STATUS=OK</cbc:Note>", "")
    assert ubl.validate(no_note.encode())
    assert ubl.validate(ok.replace("1142.00", "1142.001").encode())   # 2 decimals only
