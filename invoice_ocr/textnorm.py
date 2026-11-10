"""Amounts, dates and text normalisation shared by the generator and the extractor."""
import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def fold(s: str) -> str:
    """Lower case, no accents, single spaces: for label matching and name comparison."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.replace("ß", "ss").lower()).strip()


# --- amounts -------------------------------------------------------------------------

# 1 234,56 | 1.234,56 | 1,234.56 | 1234.56 — exactly two decimals, never a percentage,
# never the "12.03" of a date such as 12.03.2026. A VLM sometimes adds a space after the
# thousands separator ("4. 854,80"): tolerated.
AMOUNT_RE = re.compile(
    r"(?<![\d.,])-?(?:\d{1,3}(?:[   .,']\s?\d{3})+|\d+)[.,]\d{2}(?![.,]?\d)(?!\s?%)"
)


def parse_amount(token: str) -> Decimal:
    t = re.sub(r"[^\d.,\-]", "", token)
    neg = t.startswith("-")
    digits = re.sub(r"\D", "", t)
    value = Decimal(digits[:-2] + "." + digits[-2:])
    return -value if neg else value


def amounts_in(text: str) -> list:
    return [parse_amount(m.group()) for m in AMOUNT_RE.finditer(text)]


def format_amount(value: Decimal, lang: str) -> str:
    q = money(value)
    whole, dec = f"{q:.2f}".split(".")
    groups = f"{int(whole):,}"
    if lang == "fr":
        return groups.replace(",", " ") + "," + dec + " €"
    if lang == "de":
        return groups.replace(",", ".") + "," + dec + " €"
    return "€" + groups + "." + dec


# --- dates ---------------------------------------------------------------------------

MONTHS = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"],
}
_MONTH_LOOKUP = {}
for _names in MONTHS.values():
    for _i, _n in enumerate(_names, 1):
        _MONTH_LOOKUP[fold(_n)] = _i
        _MONTH_LOOKUP[fold(_n)[:3]] = _i
_MONTH_LOOKUP["marz"] = 3  # März without the umlaut dots, as OCR often reads it
_MONTH_WORD = "|".join(sorted(_MONTH_LOOKUP, key=len, reverse=True))

_DATE_PATTERNS = [
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), lambda m: (m[1], m[2], m[3])),
    (re.compile(r"(?<!\d)(\d{1,2})[./-]\s?(\d{1,2})[./-]\s?(\d{4})(?!\d)"), lambda m: (m[3], m[2], m[1])),
    (re.compile(rf"(?<!\d)(\d{{1,2}})(?:er)?\.?\s*({_MONTH_WORD})\.?\s*(\d{{4}})"),
     lambda m: (m[3], _MONTH_LOOKUP[m[2]], m[1])),
    (re.compile(rf"\b({_MONTH_WORD})\.?\s*(\d{{1,2}}),?\s*(\d{{4}})"),
     lambda m: (m[3], _MONTH_LOOKUP[m[1]], m[2])),
]


def find_date(text: str):
    """First date in `text` (day-first for numeric dates), or None."""
    t = fold(text)
    best = None
    for rx, parts in _DATE_PATTERNS:
        m = rx.search(t)
        if m:
            try:
                y, mo, d = (int(p) for p in parts(m))
                found = (m.start(), date(y, mo, d))
            except ValueError:
                continue
            if best is None or found[0] < best[0]:
                best = found
    return best[1] if best else None


def format_date(d: date, lang: str, style: int) -> str:
    if style == 0:
        return {"fr": d.strftime("%d/%m/%Y"), "de": d.strftime("%d.%m.%Y"), "en": d.isoformat()}[lang]
    month = MONTHS[lang][d.month - 1]
    if lang == "fr":
        return f"{d.day} {month} {d.year}"
    if lang == "de":
        return f"{d.day}. {month} {d.year}"
    return f"{d.day} {month[:3]} {d.year}"
