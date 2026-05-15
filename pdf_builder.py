"""
pdf_builder.py
Turn Claude's structured JSON into a final ROV PDF.
  1) Fill the blank ROV form's AcroForm fields (via pdfrw, more reliable)
  2) Generate a rebuttal narrative page (via reportlab)
  3) Concatenate into one PDF
"""

from io import BytesIO
from pdfrw import PdfReader, PdfWriter, PdfDict, PdfName, PdfString
from pypdf import PdfReader as PyPdfReader, PdfWriter as PyPdfWriter
from pypdf.generic import NameObject, BooleanObject
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.enums import TA_JUSTIFY


def fill_rov_form(blank_form_path: str, field_values: dict) -> bytes:
    """Fill AcroForm fields using pdfrw. Returns filled PDF as bytes."""
    template = PdfReader(blank_form_path)

    if template.Root.AcroForm:
        template.Root.AcroForm.update(PdfDict(NeedAppearances=PdfName('true')))

    for page in template.pages:
        if not page.Annots:
            continue
        for annot in page.Annots:
            if annot.Subtype != PdfName('Widget') or not annot.T:
                continue
            field_name = annot.T.to_unicode()
            if field_name in field_values:
                val = field_values[field_name]
                value = str(val) if val is not None else ""
                # Prevent overflow: truncate the reason field to fit the text box
                if field_name == "reason" and len(value) > 1000:
                    value = value[:997] + "..."
                annot.update(PdfDict(V=PdfString.encode(value)))
                annot.update(PdfDict(AP=''))  # viewer regenerates appearance

    buf = BytesIO()
    PdfWriter(buf, trailer=template).write()
    return buf.getvalue()


def build_rebuttal_page(rebuttal_paragraphs: list) -> bytes:
    """rebuttal_paragraphs: [{"comp_number":int, "address":str, "paragraph":str}]"""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.75*inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=12, spaceAfter=14)
    addr_style = ParagraphStyle("Address", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=10, spaceAfter=2)
    body_style = ParagraphStyle("Body", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10, leading=13,
        alignment=TA_JUSTIFY, spaceAfter=10)

    story = [Paragraph("Additional Comparable Sales — Supporting Rationale", title_style)]
    for item in rebuttal_paragraphs:
        story.append(Paragraph(item["address"], addr_style))
        story.append(Paragraph(item["paragraph"], body_style))

    doc.build(story)
    return buf.getvalue()


def concat_pdfs(filled_form_bytes: bytes, rebuttal_bytes: bytes, output_path: str):
    writer = PyPdfWriter()
    form_reader = PyPdfReader(BytesIO(filled_form_bytes))
    for page in form_reader.pages:
        writer.add_page(page)
    rebuttal_reader = PyPdfReader(BytesIO(rebuttal_bytes))
    for page in rebuttal_reader.pages:
        writer.add_page(page)

    if "/AcroForm" in form_reader.trailer["/Root"]:
        writer._root_object[NameObject("/AcroForm")] = form_reader.trailer["/Root"]["/AcroForm"]
        writer._root_object["/AcroForm"].update(
            {NameObject("/NeedAppearances"): BooleanObject(True)}
        )

    with open(output_path, "wb") as f:
        writer.write(f)


def build_rov_pdf(blank_form_path: str, agent_output: dict, output_path: str) -> str:
    """
    Main entry point for Streamlit.

    agent_output shape:
      {
        "form_fields":           { "comp1_address": "...", ... },
        "rebuttal_paragraphs":   [ {"comp_number":1, "address":"...", "paragraph":"..."}, ... ]
      }
    """
    filled = fill_rov_form(blank_form_path, agent_output["form_fields"])
    rebuttal = build_rebuttal_page(agent_output["rebuttal_paragraphs"])
    concat_pdfs(filled, rebuttal, output_path)
    return output_path
