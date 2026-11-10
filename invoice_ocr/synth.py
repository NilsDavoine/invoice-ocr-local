"""Deterministic generator of fictitious invoices (FR / EN / DE) with ground truth.

Every company, address and number is invented. Each page carries a "SAMPLE"
watermark and the bank line is an obviously fake placeholder, never an IBAN.

Output (in `out_dir`):
  pdf/INV-xx.pdf     the clean PDF (has a text layer)
  images/INV-xx.png  the page as an image; about half are "scanned"
                     (slight rotation, blur, noise, JPEG artefacts)
  truth.json         what each document *prints*, plus the expected status
"""
import io
import json
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from . import vat
from .textnorm import format_amount, format_date, money

SEED = 2026
N_INVOICES = 40
DPI = 200

SUPPLIERS = [  # invented names; VAT country decides which checksum is used
    ("Fictiva Bureautique S.à r.l.", "LU", "12, rue des Exemples", "L-0001 Exempleville"),
    ("Nordlicht Musterdruck GmbH", "DE", "Beispielstraße 7", "D-00001 Musterstadt"),
    ("Placeholder Consulting S.A.", "LU", "3, avenue du Test", "L-0002 Testange"),
    ("Atelier Démo Nettoyage SARL", "FR", "8, place Fictive", "F-00001 Villedémo"),
    ("Mockup Logistics BV", "BE", "Voorbeeldlaan 21", "B-0001 Proefstad"),
    ("Imaginaire Traiteur S.à r.l.", "LU", "45, route Inventée", "L-0003 Songeville"),
    ("Testwerk Elektro GmbH", "DE", "Prüfweg 3", "D-00002 Probedorf"),
    ("Sandbox IT Services S.A.", "LU", "1, boulevard Bac-à-sable", "L-0004 Sablon"),
    ("Exemplum Formation ASBL", "BE", "Rue Modèle 5", "B-0002 Modèleville"),
    ("Dummy Garage S.à r.l.", "LU", "77, rue du Prototype", "L-0005 Maquette"),
]
CUSTOMER = ("Démo Client S.A.", "9, rue du Client Fictif", "L-0099 Clientville")

ITEMS = {  # (fr, en, de), usual Luxembourg VAT rate
    "office": (("Ramettes de papier A4", "A4 paper reams", "Kopierpapier A4"), 17),
    "consult": (("Conseil, jour-homme", "Consulting, man-day", "Beratung, Personentag"), 17),
    "toner": (("Cartouche de toner", "Toner cartridge", "Tonerkartusche"), 17),
    "clean": (("Nettoyage des bureaux", "Office cleaning", "Büroreinigung"), 17),
    "wine": (("Vin pour réception", "Reception wine", "Wein für Empfang"), 14),
    "heating": (("Fioul de chauffage", "Heating oil", "Heizöl"), 14),
    "gas": (("Fourniture de gaz", "Gas supply", "Gaslieferung"), 8),
    "training": (("Formation en ligne", "Online training", "Online-Schulung"), 8),
    "books": (("Ouvrages professionnels", "Professional books", "Fachbücher"), 3),
    "food": (("Plateaux repas", "Lunch trays", "Mittagsplatten"), 3),
    "press": (("Abonnement presse", "Newspaper subscription", "Zeitungsabonnement"), 3),
}

LABELS = {
    "fr": dict(title="FACTURE", number="Facture n°", date="Date", due="Échéance",
               delivery="Date de livraison", bill_to="Facturé à", vat_id="N° TVA",
               cust_vat="N° TVA client", desc="Désignation", qty="Qté", unit="Prix unit. HT",
               rate="TVA", amount="Montant HT", net="Total HT", vat_line="TVA {r} % sur {b}",
               tax_total="Total TVA", gross="Total TTC", summary="Récapitulatif TVA",
               s_rate="Taux", s_base="Base HT", s_tax="Montant TVA", of="du", pay_by="Payable avant le",
               bank="Banque : XX00 SAMPLE-NOT-AN-IBAN", footer="Facture fictive générée pour des tests"),
    "en": dict(title="INVOICE", number="Invoice No.", date="Invoice date", due="Due date",
               delivery="Delivery date", bill_to="Bill to", vat_id="VAT No.",
               cust_vat="Customer VAT No.", desc="Description", qty="Qty", unit="Unit price",
               rate="VAT", amount="Amount", net="Subtotal", vat_line="VAT {r}% on {b}",
               tax_total="Total VAT", gross="Total due", summary="VAT summary",
               s_rate="Rate", s_base="Taxable amount", s_tax="VAT amount", of="of", pay_by="Payable by",
               bank="Bank: XX00 SAMPLE-NOT-AN-IBAN", footer="Fictitious invoice generated for testing"),
    "de": dict(title="RECHNUNG", number="Rechnung Nr.", date="Rechnungsdatum", due="Fällig am",
               delivery="Lieferdatum", bill_to="Rechnungsempfänger", vat_id="USt-IdNr.",
               cust_vat="USt-IdNr. Kunde", desc="Bezeichnung", qty="Menge", unit="Einzelpreis",
               rate="MwSt.", amount="Betrag netto", net="Summe netto", vat_line="MwSt. {r} % auf {b}",
               tax_total="Summe MwSt.", gross="Gesamtbetrag", summary="MwSt.-Aufstellung",
               s_rate="Satz", s_base="Bemessungsgrundlage", s_tax="MwSt.-Betrag", of="vom",
               pay_by="Zahlbar bis", bank="Bank: XX00 SAMPLE-NOT-AN-IBAN",
               footer="Fiktive Rechnung, für Tests erzeugt"),
}
LANG_INDEX = {"fr": 0, "en": 1, "de": 2}

# Deliberately inconsistent documents: the pipeline must flag them, whatever the OCR says.
ANOMALIES = {7: "gross_mismatch", 15: "foreign_rate", 23: "bad_vat_id", 31: "due_before_issue"}


def _invoice_number(rng, i):
    return rng.choice([f"F-2026-{100 + i:04d}", f"INV-{20500 + 7 * i}", f"RE2026/{300 + i}",
                       f"{2026}{i:03d}-A"])


def build_truth(seed=SEED, n=N_INVOICES):
    rng = random.Random(seed)
    suppliers = [(name, vat.make(c, rng), street, city) for name, c, street, city in SUPPLIERS]
    customer_vat = vat.make("LU", rng)
    docs = []
    for i in range(n):
        lang = ("fr", "en", "de")[i % 3]
        name, vat_id, street, city = suppliers[rng.randrange(len(suppliers))]
        issue = date(2026, 1, 5) + timedelta(days=rng.randrange(0, 290))
        due = issue + timedelta(days=rng.choice([15, 30, 30, 45, 60]))
        lines = []
        for key in rng.sample(sorted(ITEMS), rng.randint(1, 5)):
            labels, rate = ITEMS[key]
            qty = rng.choice([1, 1, 2, 3, 5, 10, 12])
            unit = money(rng.uniform(4, 950))
            lines.append({"desc": labels[LANG_INDEX[lang]], "qty": qty, "unit": unit, "rate": rate,
                          "amount": money(unit * qty)})
        anomaly = ANOMALIES.get(i)
        if anomaly == "foreign_rate":
            lines[0]["rate"] = 20
        if anomaly == "bad_vat_id":
            vat_id = vat_id[:-1] + str((int(vat_id[-1]) + 1) % 10)
        if anomaly == "due_before_issue":
            due = issue - timedelta(days=10)
        breakdown = {}
        for ln in lines:
            breakdown.setdefault(ln["rate"], Decimal("0"))
            breakdown[ln["rate"]] += ln["amount"]
        vat_rows = [{"rate": r, "base": money(b), "tax": money(b * r / 100)}
                    for r, b in sorted(breakdown.items(), reverse=True)]
        net = sum((r["base"] for r in vat_rows), Decimal("0"))
        tax = sum((r["tax"] for r in vat_rows), Decimal("0"))
        gross = net + tax + (Decimal("10.00") if anomaly == "gross_mismatch" else 0)
        docs.append({
            "id": f"INV-{i:02d}", "lang": lang, "template": rng.choice("ABC"),
            "scanned": rng.random() < 0.5, "date_style": rng.randrange(2),
            "number": _invoice_number(rng, i), "supplier_name": name, "supplier_vat": vat_id,
            "supplier_street": street, "supplier_city": city, "customer_vat": customer_vat,
            "issue_date": issue, "due_date": due, "delivery_date": issue - timedelta(days=rng.randrange(1, 9)),
            "lines": lines, "vat_rows": vat_rows, "net_total": net, "tax_total": tax,
            "gross_total": gross, "anomaly": anomaly,
            "expected_status": "TO_VERIFY" if anomaly else "OK",
        })
    return docs


# --- PDF rendering -------------------------------------------------------------------

def _render_pdf(doc, path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    L = LABELS[doc["lang"]]
    lang = doc["lang"]
    fa = lambda v: format_amount(v, lang)  # noqa: E731
    fd = lambda d: format_date(d, lang, doc["date_style"])  # noqa: E731
    W, H = A4
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle(f"Sample invoice {doc['id']}")

    # watermark first, so that the text is printed over it
    c.saveState()
    c.setFillColorRGB(0.9, 0.9, 0.9)
    c.setFont("Helvetica-Bold", 120)
    c.translate(W / 2, H / 2)
    c.rotate(35)
    c.drawCentredString(0, -40, "SAMPLE")
    c.restoreState()

    t = doc["template"]
    x0, y = 50, H - 60
    # supplier header
    c.setFont("Helvetica-Bold", 17)
    if t == "C":
        c.drawCentredString(W / 2, y, doc["supplier_name"])
    else:
        c.drawString(x0, y, doc["supplier_name"])
    c.setFont("Helvetica", 9.5)
    hdr = [doc["supplier_street"], doc["supplier_city"], f"{L['vat_id']} : {doc['supplier_vat']}"]
    for k, line in enumerate(hdr):
        if t == "C":
            c.drawCentredString(W / 2, y - 16 - 12 * k, line)
        else:
            c.drawString(x0, y - 18 - 12 * k, line)

    # document metadata
    if t == "A":
        c.setFont("Helvetica-Bold", 20)
        c.drawRightString(W - 50, y, L["title"])
        c.setFont("Helvetica", 10)
        meta = [(L["number"], doc["number"]), (L["date"], fd(doc["issue_date"])),
                (L["due"], fd(doc["due_date"]))]
        for k, (lab, val) in enumerate(meta):
            c.drawRightString(W - 50, y - 24 - 14 * k, f"{lab} : {val}")
    elif t == "B":
        c.setFont("Helvetica-Bold", 20)
        c.drawString(x0, y - 70, L["title"])
        c.setFont("Helvetica", 10)
        meta = [(L["number"], doc["number"]), (L["date"], fd(doc["issue_date"])),
                (L["delivery"], fd(doc["delivery_date"])), (L["due"], fd(doc["due_date"]))]
        for k, (lab, val) in enumerate(meta):
            c.drawString(330, y - 10 - 15 * k, lab)
            c.drawRightString(W - 50, y - 10 - 15 * k, val)
    else:
        c.setFont("Helvetica-Bold", 13)
        c.drawString(x0, y - 70, f"{L['number']} {doc['number']} {L['of']} {fd(doc['issue_date'])}")
        c.setFont("Helvetica", 10)
        c.drawString(x0, y - 86, f"{L['pay_by']} {fd(doc['due_date'])}")

    # customer block
    y = H - 200
    c.setFont("Helvetica-Bold", 10)
    c.drawString(330 if t != "C" else x0, y, L["bill_to"])
    c.setFont("Helvetica", 10)
    for k, line in enumerate([CUSTOMER[0], CUSTOMER[1], CUSTOMER[2],
                              f"{L['cust_vat']} : {doc['customer_vat']}"]):
        c.drawString(330 if t != "C" else x0, y - 14 * (k + 1), line)

    # line items
    y = H - 300
    cols = [x0, 300, 350, 440, W - 50]
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(cols[0], y, L["desc"])
    c.drawRightString(cols[1] + 20, y, L["qty"])
    c.drawRightString(cols[2] + 60, y, L["unit"])
    c.drawRightString(cols[3] + 20, y, L["rate"])
    c.drawRightString(cols[4], y, L["amount"])
    c.line(x0, y - 4, W - 50, y - 4)
    c.setFont("Helvetica", 9.5)
    for ln in doc["lines"]:
        y -= 17
        c.drawString(cols[0], y, ln["desc"])
        c.drawRightString(cols[1] + 20, y, str(ln["qty"]))
        c.drawRightString(cols[2] + 60, y, fa(ln["unit"]))
        c.drawRightString(cols[3] + 20, y, f"{ln['rate']} %")
        c.drawRightString(cols[4], y, fa(ln["amount"]))
    c.line(x0, y - 6, W - 50, y - 6)

    # totals
    y -= 40
    xl, xr = 300, W - 50
    if t == "B":  # separate VAT summary table, no "total VAT" line
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(x0, y, L["summary"])
        y -= 15
        c.drawString(x0, y, L["s_rate"])
        c.drawRightString(260, y, L["s_base"])
        c.drawRightString(360, y, L["s_tax"])
        c.setFont("Helvetica", 9.5)
        for row in doc["vat_rows"]:
            y -= 14
            c.drawString(x0, y, f"{row['rate']} %")
            c.drawRightString(260, y, fa(row["base"]))
            c.drawRightString(360, y, fa(row["tax"]))
        y -= 30
        rows = [(L["net"], doc["net_total"], False), (L["gross"], doc["gross_total"], True)]
    else:
        rows = [(L["net"], doc["net_total"], False)]
        rows += [(L["vat_line"].format(r=row["rate"], b=fa(row["base"])), row["tax"], False)
                 for row in doc["vat_rows"]]
        if t == "A":
            rows.append((L["tax_total"], doc["tax_total"], False))
        rows.append((L["gross"], doc["gross_total"], True))
    for lab, val, bold in rows:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 10)
        c.drawString(xl, y, lab)
        c.drawRightString(xr, y, fa(val))
        y -= 17

    c.setFont("Helvetica", 8)
    c.drawString(x0, 60, L["bank"])
    c.drawString(x0, 48, f"{doc['supplier_name']} - {L['vat_id']} {doc['supplier_vat']} - {L['footer']}")
    c.showPage()
    c.save()


# --- "scanner" -----------------------------------------------------------------------

def render_page(pdf_path, dpi=DPI):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(pdf_path))
    img = pdf[0].render(scale=dpi / 72).to_pil().convert("RGB")
    pdf.close()
    return img


def scan(img, seed):
    rng = np.random.default_rng(seed)
    img = img.rotate(float(rng.uniform(-2.0, 2.0)), resample=Image.BICUBIC, expand=False,
                     fillcolor=(255, 255, 255))
    img = img.filter(ImageFilter.GaussianBlur(float(rng.uniform(0.5, 1.1))))
    a = np.asarray(img).astype(np.float32)
    a = a * float(rng.uniform(0.85, 0.97)) + float(rng.uniform(5, 25))   # grey paper
    a += rng.normal(0, float(rng.uniform(5, 11)), a.shape[:2])[..., None]
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=int(rng.integers(55, 80)))
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def _json(doc):
    out = {}
    for k, v in doc.items():
        if isinstance(v, (Decimal, date)):
            out[k] = str(v)
        elif k == "lines":
            out[k] = [{kk: (str(vv) if isinstance(vv, Decimal) else vv) for kk, vv in ln.items()} for ln in v]
        elif k == "vat_rows":
            out[k] = [{"rate": r["rate"], "base": str(r["base"]), "tax": str(r["tax"])} for r in v]
        else:
            out[k] = v
    return out


def generate(out_dir, seed=SEED, n=N_INVOICES):
    out = Path(out_dir)
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    (out / "images").mkdir(parents=True, exist_ok=True)
    docs = build_truth(seed, n)
    for k, doc in enumerate(docs):
        pdf_path = out / "pdf" / f"{doc['id']}.pdf"
        _render_pdf(doc, pdf_path)
        img = render_page(pdf_path)
        if doc["scanned"]:
            img = scan(img, seed * 1000 + k)
        img.save(out / "images" / f"{doc['id']}.png", optimize=False)
    (out / "truth.json").write_text(json.dumps([_json(d) for d in docs], ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    return docs
