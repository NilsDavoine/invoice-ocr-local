import pytest

from invoice_ocr import synth
from invoice_ocr.engines.base import Line, Reading


class FakeEngine:
    """Returns fixed rows, so the pipeline can be tested without any model."""
    name = "fake"
    device = "cpu"

    def __init__(self, rows):
        self.rows = rows

    def read(self, path):
        r = Reading(self.name, self.device)
        r.lines = [Line(t, (0, 10 * k, 1, 10 * k + 8)) for k, t in enumerate(self.rows)]
        r.seconds_per_page = [0.0]
        return r


COHERENT = [
    "Fictiva Bureautique S.à r.l.  FACTURE",
    "12, rue des Exemples  Facture n° : F-2026-0101",
    "N° TVA : LU12345613  Date : 12/03/2026",
    "Échéance : 11/04/2026",
    "Total HT  1 000,00 €",
    "TVA 17 % sur 800,00 €  136,00 €",
    "TVA 3 % sur 200,00 €  6,00 €",
    "Total TVA  142,00 €",
    "Total TTC  1 142,00 €",
]


@pytest.fixture
def coherent_rows():
    return list(COHERENT)


@pytest.fixture(scope="session")
def small_dataset(tmp_path_factory):
    """The first 8 generated invoices (they include anomaly INV-07)."""
    out = tmp_path_factory.mktemp("data")
    synth.generate(out, n=8)
    return out
