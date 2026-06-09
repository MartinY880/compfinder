"""
escalation_pdf_builder.py
Builds the UWM Appraisal Deficiency Escalation PDF using reportlab.

Layout mirrors the standard escalation document format:
  - Centered bold title
  - Header block as "Label: Value" lines (label bold inline)
  - Named sections with bold header + body paragraph
"""

from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def _s(val) -> str:
    """Safe escaped string: None → '', bool → Yes/No, else str."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "Yes" if val else "No"
    return escape(str(val))


def build_escalation_pdf(analysis: dict, output_path: str) -> str:
    """
    Build the escalation PDF from Claude's analysis dict.
    Writes to output_path and returns it.

    analysis keys (from escalation_agent.py):
      loan_number, property_address, borrower_name, co_borrower_name,
      appraiser_name, effective_date, appraisal_type, reported_value,
      escalation_reason, deficiency_summary, fha_mpr_concern,
      adjustment_support_concern, comparable_selection_concern,
      requested_lender_determination
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=1.0 * inch,
        rightMargin=1.0 * inch,
        topMargin=1.0 * inch,
        bottomMargin=1.0 * inch,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "EscTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        spaceAfter=16,
        alignment=TA_CENTER,
    )
    header_line_style = ParagraphStyle(
        "EscHeaderLine",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=16,
        spaceAfter=2,
    )
    section_header_style = ParagraphStyle(
        "EscSectionHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        spaceBefore=14,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "EscBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=15,
        spaceAfter=4,
        alignment=TA_JUSTIFY,
    )

    today = date.today().strftime("%m/%d/%Y")

    story = []

    # ── Title ─────────────────────────────────────────────────────────────
    story.append(Paragraph("APPRAISAL DEFICIENCY ESCALATION", title_style))

    # ── Header block ──────────────────────────────────────────────────────
    def header_line(label: str, value: str) -> Paragraph:
        return Paragraph(f"<b>{escape(label)}:</b> {value}", header_line_style)

    story.append(header_line("Date of Submission", today))
    story.append(header_line("Requester Title", "Coordinator"))
    story.append(header_line("Input Coordinator", _s(analysis.get("input_coordinator"))))
    story.append(header_line("Company Name", "MortgagePros"))
    story.append(header_line("Borrower", _s(analysis.get("borrower_name"))))
    story.append(header_line("Subject Property", _s(analysis.get("property_address"))))
    story.append(header_line("Loan Number", _s(analysis.get("loan_number"))))
    story.append(header_line("Appraisal Type", _s(analysis.get("appraisal_type"))))
    story.append(header_line("Appraiser", _s(analysis.get("appraiser_name"))))
    story.append(header_line("Effective Date", _s(analysis.get("effective_date"))))
    story.append(header_line("Reported Opinion of Value", _s(analysis.get("reported_value"))))

    story.append(Spacer(1, 0.2 * inch))

    # ── Narrative sections ─────────────────────────────────────────────────
    sections = [
        ("Escalation Reason", "escalation_reason"),
        ("Deficiency Summary", "deficiency_summary"),
        ("FHA/MPR Concern", "fha_mpr_concern"),
        ("Adjustment Support Concern", "adjustment_support_concern"),
        ("Comparable Selection Concern", "comparable_selection_concern"),
    ]

    for section_label, key in sections:
        text = _s(analysis.get(key, ""))
        story.append(Paragraph(f"{section_label}:", section_header_style))
        story.append(Paragraph(text or "N/A", body_style))

    story.append(Paragraph("Requested Lender Determination:", section_header_style))
    story.append(Paragraph("Based on the issues above, the appraisal should be deemed deficient.", body_style))

    doc.build(story)

    with open(output_path, "wb") as f:
        f.write(buf.getvalue())

    return output_path
