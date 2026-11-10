"""EU VAT identification numbers: format and checksum for LU, BE, FR and DE.

Only the published check-digit rules are implemented. A number that passes is
*well-formed*, not necessarily *registered* (that would need the VIES service).
"""
import re

LU_RATES = (17, 14, 8, 3)  # Luxembourg: standard, intermediate, reduced, super-reduced


def normalise(raw: str) -> str:
    return re.sub(r"[\s.\-]", "", raw or "").upper()


def _lu(d: str) -> bool:
    return len(d) == 8 and int(d[:6]) % 89 == int(d[6:])


def _be(d: str) -> bool:
    return len(d) == 10 and d[0] in "01" and 97 - int(d[:8]) % 97 == int(d[8:])


def _fr(d: str) -> bool:
    return len(d) == 11 and (12 + 3 * (int(d[2:]) % 97)) % 97 == int(d[:2])


def _de(d: str) -> bool:
    if len(d) != 9:
        return False
    product = 10
    for c in d[:8]:
        s = (int(c) + product) % 10 or 10
        product = (2 * s) % 11
    check = 11 - product
    return (0 if check == 10 else check) == int(d[8])


_RULES = {"LU": _lu, "BE": _be, "FR": _fr, "DE": _de}


def is_valid(vat_id: str) -> bool:
    v = normalise(vat_id)
    country, digits = v[:2], v[2:]
    rule = _RULES.get(country)
    return bool(rule and digits.isdigit() and rule(digits))


def make(country: str, rng) -> str:
    """Random, well-formed number for `country` (rng: random.Random). Fictitious."""
    if country == "LU":
        body = rng.randrange(100000, 999999)
        return f"LU{body:06d}{body % 89:02d}"
    if country == "BE":
        first8 = f"0{rng.randrange(2_000_000, 9_999_999):07d}"
        return f"BE{first8}{97 - int(first8) % 97:02d}"
    if country == "FR":
        siren = rng.randrange(100_000_000, 999_999_999)
        return f"FR{(12 + 3 * (siren % 97)) % 97:02d}{siren:09d}"
    if country == "DE":
        while True:
            body = f"{rng.randrange(10_000_000, 99_999_999):08d}"
            for last in "0123456789":
                if _de(body + last):
                    return "DE" + body + last
    raise ValueError(country)

