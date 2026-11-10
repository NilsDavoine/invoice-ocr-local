"""Coherence checks. Any issue sends the invoice to "TO_VERIFY".

They catch two different things with the same arithmetic: OCR misreads (a 3 read
as an 8 breaks HT + TVA = TTC) and documents that are wrong in the first place.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from . import vat
from .textnorm import money

# Sums are exact, as in the EN 16931 business rules BR-CO-14 (VAT total = sum of the
# VAT breakdown) and BR-CO-15 (total with VAT = total without VAT + VAT total).
TOL = Decimal("0")
TOL_RATE = Decimal("0.02")          # base x rate: invoices often round per line
MAX_AGE = timedelta(days=550)       # ~18 months
MAX_TERM = timedelta(days=120)
REQUIRED = ("supplier_name", "supplier_vat", "invoice_number", "issue_date", "due_date",
            "net_total", "tax_total", "gross_total")


@dataclass(frozen=True)
class Issue:
    code: str
    detail: str

    def __str__(self):
        return f"{self.code}: {self.detail}"


def check(inv, as_of: date):
    """`inv`: an Extraction (or any object with the same attributes)."""
    issues = []
    add = lambda code, detail: issues.append(Issue(code, detail))  # noqa: E731

    for name in REQUIRED:
        if getattr(inv, name) in (None, ""):
            add("MISSING_FIELD", name)
    if not inv.vat_breakdown:
        add("MISSING_FIELD", "vat_breakdown")

    net, tax, gross = inv.net_total, inv.tax_total, inv.gross_total
    if None not in (net, tax, gross) and abs(net + tax - gross) > TOL:
        add("TOTALS_MISMATCH", f"{net} + {tax} != {gross}")

    if inv.vat_breakdown:
        bases = sum((r["base"] for r in inv.vat_breakdown), Decimal("0"))
        taxes = sum((r["tax"] for r in inv.vat_breakdown), Decimal("0"))
        if net is not None and abs(bases - net) > TOL:
            add("BREAKDOWN_NET", f"sum of bases {bases} != net {net}")
        if tax is not None and abs(taxes - tax) > TOL:
            add("BREAKDOWN_TAX", f"sum of VAT {taxes} != total VAT {tax}")
        for r in inv.vat_breakdown:
            if r["rate"] not in vat.LU_RATES:
                add("RATE_NOT_LU", f"{r['rate']} % is not a Luxembourg VAT rate")
            elif abs(money(r["base"] * Decimal(r["rate"]) / 100) - r["tax"]) > TOL_RATE:
                add("RATE_TAX", f"{r['rate']} % of {r['base']} != {r['tax']}")

    if inv.supplier_vat and not vat.is_valid(inv.supplier_vat):
        add("VAT_ID_INVALID", inv.supplier_vat)

    issue, due = inv.issue_date, inv.due_date
    if issue:
        if issue > as_of:
            add("DATE_IN_FUTURE", str(issue))
        elif as_of - issue > MAX_AGE:
            add("DATE_TOO_OLD", str(issue))
    if issue and due:
        if due < issue:
            add("DUE_BEFORE_ISSUE", f"{due} < {issue}")
        elif due - issue > MAX_TERM:
            add("DUE_TOO_LATE", f"{(due - issue).days} days")
    return issues


def status(issues):
    return "TO_VERIFY" if issues else "OK"
