"""
pdf.py - turns a finished notice into a printable PDF (A4).

What the PDF contains:
  - the notice (in the chosen language), with a dashed "Sign here" box at the end
  - if the language is not English: an English copy after it, also with its own "Sign here" box
  - on EVERY notice page: "Page x of n" and a small "Initials" box at the bottom
  - a LAST page "How to sign and send" (keep it, don't send it)

The small grey numbers 1-4 on the PDF match the signing steps shown on screen:
  1 = sign here,  2 = initials on every page,  3 = place and date,  4 = attach documents

Main function:  build_pdf(draft, warnings) -> PDF bytes
"""
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app import prompts

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# Which font family each language needs (names match the files from setup_fonts.py)
SCRIPT_OF = {
    "en-IN": "latin", "hi-IN": "devanagari", "mr-IN": "devanagari", "bn-IN": "bengali",
    "ta-IN": "tamil", "te-IN": "telugu", "kn-IN": "kannada", "ml-IN": "malayalam",
    "gu-IN": "gujarati", "pa-IN": "gurmukhi", "od-IN": "oriya",
}

GREY = (150, 150, 150)
LINE_H = 6.2          # height of one line of body text, in millimetres


class NoticePDF(FPDF):
    """
    Our own kind of PDF. It is FPDF plus a custom footer.
    (A "class" is a blueprint; "NoticePDF(FPDF)" means: start from FPDF and change a few things.)
    """

    def __init__(self, total_pages: int | None):
        super().__init__(format="A4", unit="mm")
        self.total_pages = total_pages        # known on the 2nd pass (see build_pdf)
        self.show_initials = True             # turned off for the checklist page
        self.set_margins(left=25, top=20, right=25)
        self.set_auto_page_break(auto=True, margin=30)   # leave room for the footer
        for script in ("latin",) + tuple(set(SCRIPT_OF.values()) - {"latin"}):
            regular, bold = FONTS_DIR / f"{script}-Regular.ttf", FONTS_DIR / f"{script}-Bold.ttf"
            if regular.exists():
                self.add_font(script, "", str(regular))
                self.add_font(script, "B", str(bold if bold.exists() else regular))
        if not (FONTS_DIR / "latin-Regular.ttf").exists():
            raise RuntimeError("Fonts are missing. Run:  python -m app.setup_fonts")
        self.set_text_shaping(True)           # join Indian-script letters correctly (uses uharfbuzz)
        self.set_fallback_fonts(["latin"])    # English letters/numbers inside Hindi text use the Latin font

    def footer(self):
        """FPDF calls this automatically at the bottom of every page."""
        self.set_y(-20)
        self.set_font("latin", "", 8.5)
        self.set_text_color(*GREY)
        total = self.total_pages or "?"
        self.cell(60, 6, f"Page {self.page_no()} of {total}")
        if self.show_initials:
            # small dashed box on the right where the sender initials every page
            x, y = self.w - self.r_margin - 34, self.get_y() - 3
            self.set_draw_color(*GREY)
            self.set_dash_pattern(dash=1, gap=1)
            self.rect(x, y, 34, 11)
            self.set_dash_pattern()
            self.set_xy(x, y + 11)
            self.cell(34, 4, "2  Initials", align="C")
        self.set_text_color(0, 0, 0)


# ================================================================ drawing helpers
def _para(pdf: NoticePDF, font: str, text: str, size: float = 11, bold: bool = False, gap: float = 2.5, align: str = "L"):
    """Write one paragraph that wraps across lines, then leave a small gap."""
    if not text:
        return
    pdf.set_font(font, "B" if bold else "", size)
    pdf.multi_cell(0, LINE_H, text, align=align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if gap > 0:              # careful: fpdf's ln(0) would jump a whole line, so skip it
        pdf.ln(gap)


def _numbered(pdf: NoticePDF, font: str, number: int, text: str, align: str):
    """A numbered paragraph with a hanging indent:  '1.  That ...' """
    pdf.set_font(font, "", 11)
    pdf.cell(9, LINE_H, f"{number}.")
    pdf.multi_cell(0, LINE_H, text, align=align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def _signature_block(pdf: NoticePDF, font: str, n: dict, labels: dict):
    """'Yours faithfully,' + dashed 'Sign here' box + name + place/date lines."""
    if pdf.will_page_break(64):          # keep the whole block together on one page
        pdf.add_page()
    pdf.ln(3)
    _para(pdf, font, labels.get("closing", "Yours faithfully,"), gap=2)
    x, y = pdf.l_margin, pdf.get_y()
    pdf.set_draw_color(*GREY)
    pdf.set_dash_pattern(dash=1.5, gap=1.2)
    pdf.rect(x, y, 70, 24)                                   # the signature box
    pdf.set_dash_pattern()
    pdf.set_xy(x + 2, y + 18)
    pdf.set_font("latin", "", 8.5)
    pdf.set_text_color(*GREY)
    pdf.cell(66, 5, "1  Sign here (blue ink)")
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(x, y + 26)
    _para(pdf, font, n.get("from_name", ""), bold=True, gap=1)
    place = n.get("place") or "________________"
    _para(pdf, font, f'{labels.get("place", "Place")}: {place}', gap=1)
    _para(pdf, font, f'{labels.get("date", "Date")}: ________________', gap=1)
    pdf.set_font("latin", "", 8.5)
    pdf.set_text_color(*GREY)
    pdf.cell(0, 5, "3  Write the place and the date of signing", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)


def _notice_pages(pdf: NoticePDF, n: dict, language: str, heading_note: str = ""):
    """Draw one complete notice (starting on a new page)."""
    font = SCRIPT_OF.get(language, "latin")
    if not (FONTS_DIR / f"{font}-Regular.ttf").exists():
        font = "latin"
    labels = {**prompts.LABELS, **(n.get("labels") or {})}
    align = "J" if font == "latin" else "L"        # justify English; left-align Indian scripts

    pdf.add_page()
    pdf.show_initials = True
    # ---- heading
    pdf.set_font(font, "B", 16)
    pdf.cell(0, 9, labels["title"], align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if heading_note:
        pdf.set_font(font, "", 10)
        pdf.cell(0, 6, f"({heading_note})", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(font, "", 10)
    pdf.cell(0, 6, n.get("mode", ""), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    pdf.cell(0, 6, f'{labels["date"]}: {n.get("date", "")}', align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    # ---- From / To
    _para(pdf, font, labels["from"] + ":", bold=True, gap=0)
    _para(pdf, font, n.get("from_name", ""), gap=0)
    _para(pdf, font, n.get("from_address", ""), gap=4)
    _para(pdf, font, labels["to"] + ":", bold=True, gap=0)
    _para(pdf, font, n.get("to_name", ""), gap=0)
    _para(pdf, font, n.get("to_role", ""), gap=0)
    _para(pdf, font, n.get("to_address", ""), gap=4)

    # ---- Subject + body
    _para(pdf, font, f'{labels["subject"]}: {n.get("subject", "")}', bold=True, gap=4)
    _para(pdf, font, labels["salutation"], gap=2)
    _para(pdf, font, n.get("opening", ""), align=align, gap=3)
    paragraphs = [p for p in list(n.get("paragraphs") or []) + [n.get("legal_paragraph", "")] if p]
    for i, p in enumerate(paragraphs, 1):
        _numbered(pdf, font, i, p, align)
    pdf.ln(1)
    _para(pdf, font, n.get("demand", ""), align=align, gap=3)
    _para(pdf, font, n.get("consequence", ""), align=align, gap=3)

    # ---- Annexures
    if n.get("annexures"):
        _para(pdf, font, labels["annexures"] + ":", bold=True, gap=1)
        for i, a in enumerate(n["annexures"], 1):
            _para(pdf, font, f"{i}. {a}", gap=0.5)
        pdf.set_font("latin", "", 8.5)
        pdf.set_text_color(*GREY)
        pdf.cell(0, 5, "4  Attach self-attested copies; write 'True copy' and sign on each",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)

    _signature_block(pdf, font, n, labels)


def _checklist_page(pdf: NoticePDF, deadline: int | None, warnings: list[str], how_to_send: str | None):
    """Last page: how to sign and send. This page is for the sender only."""
    pdf.add_page()
    pdf.show_initials = False
    pdf.set_font("latin", "B", 15)
    pdf.cell(0, 9, "How to sign and send your document", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("latin", "B", 10)            # minimal colours: bold black instead of red
    pdf.cell(0, 6, "Keep this page for yourself. Do NOT send it.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)
    # the general steps, with the last one ("how to send") replaced by this document type's own step
    steps = prompts.SIGN_GUIDE[:-1] + [how_to_send or prompts.SIGN_GUIDE[-1]]
    if deadline:
        steps.append(f"Wait {deadline} days from the day they receive it. If there is no reply, keep your proof "
                     "of sending safe and consider the next step (a reminder, a higher authority, or legal action).")
    for i, step in enumerate(steps, 1):
        _numbered(pdf, "latin", i, step, "L")
    if warnings:
        pdf.ln(3)
        _para(pdf, "latin", "Important:", bold=True, gap=1)
        for w in warnings:
            _para(pdf, "latin", "- " + w, gap=1)
    pdf.ln(6)
    pdf.set_font("latin", "", 9)
    pdf.set_text_color(*GREY)
    pdf.multi_cell(0, 5, "NOTIVOX helps you draft a notice from the facts you gave. It is not legal advice. "
                         "Read the notice carefully before signing, and consult an advocate if you are unsure.",
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)


# ================================================================ main function
def _render(draft: dict, warnings: list[str], total_pages: int | None, how_to_send: str | None = None) -> NoticePDF:
    pdf = NoticePDF(total_pages)
    language = draft.get("language") or "en-IN"
    if draft.get("local") and language != "en-IN":
        _notice_pages(pdf, draft["local"], language)
        _notice_pages(pdf, draft["notice"], "en-IN",
                      heading_note=(draft["local"].get("labels") or {}).get("english_copy", "English copy"))
    else:
        _notice_pages(pdf, draft["notice"], "en-IN")
    _checklist_page(pdf, draft["notice"].get("deadline_days"), warnings, how_to_send)
    return pdf


def build_pdf(draft: dict, warnings: list[str] | None = None, how_to_send: str | None = None) -> bytes:
    """
    Make the PDF and return it as bytes (ready to send to the browser as a download).
    We draw it twice: the 1st time only to count the pages, the 2nd time with
    "Page x of n" filled in correctly.
    how_to_send = the document type's own "how to send" step (e.g. "submit it at the police station...").
    """
    warnings = warnings or []
    total = _render(draft, warnings, None, how_to_send).pages_count
    return bytes(_render(draft, warnings, total, how_to_send).output())


# ================================================================ self-test (no API keys needed)
# Run:  python -m app.pdf      -> creates sample_en.pdf and sample_hi.pdf
if __name__ == "__main__":
    english = {
        "date": "19 November 2026", "mode": "By Registered Post A.D. / Speed Post",
        "from_name": "Priya Nair", "from_address": "Flat 12, Sunrise Residency, Aundh, Pune 411007",
        "to_name": "Mr. Suresh Malhotra", "to_role": "Owner, Flat B-204",
        "to_address": "B-204, Green Park Apartments, Baner, Pune 411045",
        "subject": "Legal notice for refund of security deposit of Rs. 30,000/-",
        "opening": "I, Priya Nair, residing at Flat 12, Sunrise Residency, Aundh, Pune 411007, do hereby serve upon you the following legal notice:",
        "paragraphs": [
            "That I occupied Flat B-204, Green Park Apartments, Baner, Pune under a Leave and Licence Agreement with you and paid you a refundable security deposit of Rs. 30,000/- by UPI.",
            "That I vacated the said flat and handed over the keys to you on 15 September 2026, leaving the premises in good condition.",
            "That despite repeated requests, you have failed to refund the security deposit, and the Agreement contains no clause permitting any deduction for painting.",
        ],
        "legal_paragraph": "That your aforesaid acts and omissions are contrary to the terms of the Rent / Leave and Licence Agreement between the parties and Section 73 of the Indian Contract Act, 1872, and you are liable for the same.",
        "demand": "I therefore call upon you to refund the security deposit of Rs. 30,000/- within 15 days of receipt of this notice.",
        "consequence": "Failing which, I shall be constrained to initiate appropriate legal proceedings against you, at your risk as to costs and consequences.",
        "annexures": ["Copy of the Leave and Licence Agreement", "Copy of the UPI payment receipt for the deposit"],
        "deadline_days": 15, "place": "Pune", "labels": dict(prompts.LABELS),
    }
    hindi = {
        **english,
        "mode": "रजिस्टर्ड डाक ए.डी. / स्पीड पोस्ट द्वारा",
        "subject": "30,000 रुपये की सुरक्षा जमा राशि की वापसी हेतु कानूनी नोटिस",
        "opening": "मैं, Priya Nair, निवासी Flat 12, Sunrise Residency, Aundh, Pune 411007, आपको निम्नलिखित कानूनी नोटिस देती हूँ:",
        "paragraphs": [
            "कि मैंने आपके साथ लीव एंड लाइसेंस समझौते के तहत फ्लैट B-204 में निवास किया और आपको UPI द्वारा 30,000 रुपये की वापसी योग्य सुरक्षा जमा राशि दी।",
            "कि मैंने 15 सितंबर 2026 को फ्लैट खाली कर दिया और चाबियाँ आपको सौंप दीं।",
            "कि बार-बार अनुरोध के बावजूद आपने सुरक्षा जमा राशि वापस नहीं की है।",
        ],
        "legal_paragraph": "कि आपके उपरोक्त कार्य भारतीय संविदा अधिनियम, 1872 की धारा 73 के विपरीत हैं।",
        "demand": "अतः मैं आपसे इस नोटिस की प्राप्ति के 15 दिनों के भीतर 30,000 रुपये वापस करने की माँग करती हूँ।",
        "consequence": "अन्यथा मुझे आपके विरुद्ध उचित कानूनी कार्यवाही करने के लिए बाध्य होना पड़ेगा।",
        "annexures": ["लीव एंड लाइसेंस समझौते की प्रति", "UPI भुगतान रसीद की प्रति"],
        "labels": {"title": "कानूनी नोटिस", "from": "प्रेषक", "to": "प्राप्तकर्ता", "subject": "विषय",
                   "date": "दिनांक", "place": "स्थान", "salutation": "महोदय/महोदया,",
                   "annexures": "संलग्नक", "closing": "भवदीय,", "english_copy": "अंग्रेज़ी प्रति"},
    }
    with open("sample_en.pdf", "wb") as f:
        f.write(build_pdf({"notice": english, "local": None, "language": "en-IN"}))
    with open("sample_hi.pdf", "wb") as f:
        f.write(build_pdf({"notice": english, "local": hindi, "language": "hi-IN"},
                          ["Keep the postal receipt safe."]))
    print("Created sample_en.pdf and sample_hi.pdf - open them to check.")
