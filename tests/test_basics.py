import random
from datetime import date
from decimal import Decimal

import pytest

from invoice_ocr import vat
from invoice_ocr.textnorm import amounts_in, find_date, format_amount, parse_amount


@pytest.mark.parametrize("country", ["LU", "BE", "FR", "DE"])
def test_generated_vat_ids_are_valid_and_one_digit_breaks_them(country):
    rng = random.Random(1)
    for _ in range(200):
        v = vat.make(country, rng)
        assert vat.is_valid(v), v
        broken = v[:-1] + str((int(v[-1]) + 1) % 10)
        assert not vat.is_valid(broken), broken


def test_vat_format_rules():
    assert vat.is_valid("LU 1234 5613")          # spaces tolerated
    assert not vat.is_valid("LU1234561")         # too short
    assert not vat.is_valid("XX12345613")        # unknown country
    assert not vat.is_valid("")


@pytest.mark.parametrize("lang", ["fr", "en", "de"])
def test_amount_roundtrip(lang):
    for v in ["0.05", "7.10", "1234.56", "1234567.89"]:
        assert amounts_in(format_amount(Decimal(v), lang)) == [Decimal(v)]


def test_amounts_ignore_rates_and_dates():
    assert amounts_in("TVA 17,00 % sur 1 000,00 €  170,00 €") == [Decimal("1000.00"), Decimal("170.00")]
    assert amounts_in("Datum 12.03.2026") == []
    assert parse_amount("€1,234.56") == Decimal("1234.56")
    assert parse_amount("1.234,56 €") == Decimal("1234.56")


@pytest.mark.parametrize("text", ["12/03/2026", "12.03.2026", "2026-03-12", "12 mars 2026",
                                  "12. März 2026", "12 Mar 2026", "March 12, 2026", "12. Marz 2026"])
def test_dates(text):
    assert find_date(f"Date : {text}") == date(2026, 3, 12)


def test_impossible_date_is_none():
    assert find_date("31/02/2026") is None
