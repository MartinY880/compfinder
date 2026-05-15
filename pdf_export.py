"""
PDF Export – Appraisal Rebuttal Report
======================================
Generates a professional PDF comparing subject property against selected
comparable sales to support a value dispute.
"""

import io
from datetime import datetime

import pandas as pd
from fpdf import FPDF


# ── Color palette ─────────────────────────────────────────────────────
_DARK_BG = (15, 23, 42)
_CARD_BG = (30, 41, 59)
_BORDER = (51, 65, 85)
_TEXT = (226, 232, 240)
_TEXT_DIM = (148, 163, 184)
_ACCENT = (56, 189, 248)
_GREEN = (74, 222, 128)
_RED = (248, 113, 113)
_YELLOW = (251, 191, 36)
_WHITE = (255, 255, 255)

_TIER_COLORS = {"Green": _GREEN, "Yellow": _YELLOW, "Red": _RED}


def _fmt_currency(val):
    if pd.isna(val) or val is None:
        return "N/A"
    return f"${val:,.0f}"


def _fmt_num(val, decimals=0):
    if pd.isna(val) or val is None:
        return "N/A"
    if decimals == 0:
        return f"{val:,.0f}"
    return f"{val:,.{decimals}f}"


class _ReportPDF(FPDF):
    """Custom PDF with dark-themed header/footer."""

    def __init__(self, subject_address: str):
        super().__init__(orientation="L", unit="mm", format="letter")
        self._subject_address = subject_address
        self.set_auto_page_break(auto=True, margin=18)
        # Use a Unicode-capable font
        self.add_font("DejaVu", "", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", uni=True)
        self.add_font("DejaVu", "B", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", uni=True)

    def header(self):
        self.set_fill_color(*_DARK_BG)
        self.rect(0, 0, self.w, self.h, "F")
        # Header bar
        self.set_fill_color(*_CARD_BG)
        self.rect(0, 0, self.w, 16, "F")
        self.set_font("DejaVu", "B", 12)
        self.set_text_color(*_ACCENT)
        self.set_xy(8, 4)
        self.cell(0, 8, "Comparable Sales Analysis — Appraisal Rebuttal", ln=False)
        self.set_font("DejaVu", "", 8)
        self.set_text_color(*_TEXT_DIM)
        self.set_xy(self.w - 80, 4)
        self.cell(70, 8, datetime.now().strftime("%B %d, %Y"), align="R", ln=False)
        self.ln(16)

    def footer(self):
        self.set_y(-14)
        self.set_font("DejaVu", "", 7)
        self.set_text_color(*_TEXT_DIM)
        self.cell(0, 8, f"Comp Finder Report  |  {self._subject_address}  |  Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title: str):
        self.set_font("DejaVu", "B", 11)
        self.set_text_color(*_WHITE)
        self.set_fill_color(*_CARD_BG)
        self.cell(0, 9, f"  {title}", ln=True, fill=True)
        self.ln(2)

    def key_value(self, label: str, value: str, w_label=45, w_value=50):
        self.set_font("DejaVu", "", 8)
        self.set_text_color(*_TEXT_DIM)
        self.cell(w_label, 5, label, ln=False)
        self.set_text_color(*_TEXT)
        self.set_font("DejaVu", "B", 8)
        self.cell(w_value, 5, str(value), ln=True)


def generate_rebuttal_pdf(
    subject: dict,
    comps_df: pd.DataFrame,
    appraised_value: float | None = None,
) -> bytes:
    """
    Build an appraisal rebuttal PDF and return it as bytes.

    Parameters
    ----------
    subject : dict
        Subject property data (address, beds, baths, sqft, etc.)
    comps_df : pd.DataFrame
        DataFrame of selected comparable properties.
    appraised_value : float or None
        The appraised value being disputed (for the summary narrative).
    """
    address_str = f"{subject.get('address', '')}, {subject.get('city', '')}, {subject.get('state', '')} {subject.get('zipcode', '')}"
    pdf = _ReportPDF(address_str)
    pdf.alias_nb_pages()
    pdf.add_page()

    # ── Subject Property ──────────────────────────────────────────────
    pdf.section_title("Subject Property")

    col_w = 95
    x_start = pdf.get_x()
    y_start = pdf.get_y()

    # Left column
    pdf.key_value("Address:", address_str, w_label=30, w_value=60)
    pdf.key_value("Property Type:", str(subject.get("property_type", "N/A")), w_label=30, w_value=60)
    pdf.key_value("Bedrooms:", str(subject.get("bedrooms", "N/A")), w_label=30, w_value=60)
    pdf.key_value("Bathrooms:", str(subject.get("bathrooms", "N/A")), w_label=30, w_value=60)
    pdf.key_value("Sq Ft:", _fmt_num(subject.get("sqft")), w_label=30, w_value=60)
    pdf.key_value("Lot Size:", _fmt_num(subject.get("lot_size")), w_label=30, w_value=60)
    pdf.key_value("Year Built:", str(subject.get("year_built", "N/A")), w_label=30, w_value=60)
    if subject.get("avm_value"):
        pdf.key_value("AVM Value:", _fmt_currency(subject.get("avm_value")), w_label=30, w_value=60)
    if appraised_value:
        pdf.key_value("Appraised Value:", _fmt_currency(appraised_value), w_label=30, w_value=60)

    pdf.ln(4)

    # ── Appraisal Dispute Summary ─────────────────────────────────────
    if appraised_value and not comps_df.empty and "sale_price" in comps_df.columns:
        avg_price = comps_df["sale_price"].mean()
        median_price = comps_df["sale_price"].median()
        min_price = comps_df["sale_price"].min()
        max_price = comps_df["sale_price"].max()
        num_above = int((comps_df["sale_price"] > appraised_value).sum())

        pdf.section_title("Appraisal Dispute Summary")
        pdf.set_font("DejaVu", "", 9)
        pdf.set_text_color(*_TEXT)

        narrative = (
            f"The subject property located at {address_str} received an appraised value of "
            f"{_fmt_currency(appraised_value)}. Based on our analysis of {len(comps_df)} comparable "
            f"sales in the surrounding area that closely match the subject property's characteristics, "
            f"we believe this valuation is unsupported by current market data."
        )
        pdf.multi_cell(0, 5, narrative)
        pdf.ln(3)

        narrative2 = (
            f"The selected comparables show an average sale price of {_fmt_currency(avg_price)} "
            f"and a median sale price of {_fmt_currency(median_price)}, with a range from "
            f"{_fmt_currency(min_price)} to {_fmt_currency(max_price)}. "
            f"{num_above} of {len(comps_df)} comparable properties sold above the appraised value."
        )
        pdf.multi_cell(0, 5, narrative2)
        pdf.ln(2)

        if avg_price > appraised_value:
            diff = avg_price - appraised_value
            pct = (diff / appraised_value) * 100
            pdf.set_font("DejaVu", "B", 9)
            pdf.set_text_color(*_GREEN)
            pdf.multi_cell(
                0, 5,
                f"The average comparable sale price exceeds the appraised value by "
                f"{_fmt_currency(diff)} ({pct:.1f}%), suggesting the appraisal undervalues "
                f"the subject property.",
            )
            pdf.set_text_color(*_TEXT)
        pdf.ln(4)

    # ── Market Summary Stats ──────────────────────────────────────────
    if not comps_df.empty:
        pdf.section_title("Comparable Sales — Market Summary")

        stats = [
            ("Comps Selected", str(len(comps_df))),
            ("Avg Sale Price", _fmt_currency(comps_df["sale_price"].mean())),
            ("Median Sale Price", _fmt_currency(comps_df["sale_price"].median())),
            ("Min Sale Price", _fmt_currency(comps_df["sale_price"].min())),
            ("Max Sale Price", _fmt_currency(comps_df["sale_price"].max())),
        ]
        if "price_per_sqft" in comps_df.columns and comps_df["price_per_sqft"].notna().any():
            stats.append(("Avg $/SqFt", _fmt_currency(comps_df["price_per_sqft"].mean())))
            stats.append(("Median $/SqFt", _fmt_currency(comps_df["price_per_sqft"].median())))
        if "distance_miles" in comps_df.columns:
            stats.append(("Avg Distance", f"{comps_df['distance_miles'].mean():.2f} mi"))
        if "similarity" in comps_df.columns:
            stats.append(("Avg Similarity", f"{comps_df['similarity'].mean():.1f}%"))

        for label, val in stats:
            pdf.key_value(label + ":", val, w_label=40, w_value=50)
        pdf.ln(4)

    # ── Comparable Properties Detail Table ────────────────────────────
    pdf.section_title("Comparable Properties — Detail")

    table_cols = [
        ("Address", "address", 52),
        ("City", "city", 28),
        ("ZIP", "zipcode", 16),
        ("Dist", "distance_miles", 14),
        ("Beds", "bedrooms", 12),
        ("Baths", "bathrooms", 12),
        ("SqFt", "sqft", 18),
        ("Lot", "lot_size", 18),
        ("Yr Built", "year_built", 16),
        ("Sale Date", "sale_date", 22),
        ("Sale Price", "sale_price", 22),
        ("$/SqFt", "price_per_sqft", 16),
        ("Sim %", "similarity", 14),
        ("Match", "match_tier", 14),
    ]
    # Filter to columns that exist
    table_cols = [(h, k, w) for h, k, w in table_cols if k in comps_df.columns]

    # Header row
    pdf.set_font("DejaVu", "B", 7)
    pdf.set_fill_color(15, 23, 42)
    pdf.set_text_color(*_ACCENT)
    for header, _, width in table_cols:
        pdf.cell(width, 6, header, border=0, fill=True)
    pdf.ln()

    # Draw header underline
    pdf.set_draw_color(*_BORDER)
    pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + sum(w for _, _, w in table_cols), pdf.get_y())

    # Data rows
    pdf.set_font("DejaVu", "", 7)
    for idx, (_, row) in enumerate(comps_df.iterrows()):
        tier = row.get("match_tier", "")
        tier_color = _TIER_COLORS.get(tier, _TEXT)

        # Alternate row background
        if idx % 2 == 0:
            pdf.set_fill_color(30, 41, 59)
        else:
            pdf.set_fill_color(20, 30, 48)

        for header, key, width in table_cols:
            val = row.get(key)
            if key == "sale_price":
                txt = _fmt_currency(val)
            elif key == "price_per_sqft":
                txt = _fmt_currency(val)
            elif key == "distance_miles":
                txt = _fmt_num(val, 2)
            elif key == "similarity":
                txt = _fmt_num(val, 0)
            elif key in ("sqft", "lot_size"):
                txt = _fmt_num(val)
            elif key == "match_tier":
                txt = str(val) if val else "N/A"
                pdf.set_text_color(*tier_color)
                pdf.cell(width, 5.5, txt, border=0, fill=True)
                pdf.set_text_color(*_TEXT)
                continue
            else:
                txt = str(val) if pd.notna(val) and val is not None else "N/A"

            pdf.set_text_color(*_TEXT)
            pdf.cell(width, 5.5, txt, border=0, fill=True)
        pdf.ln()

    pdf.ln(4)

    # ── Individual Comp Breakdowns ────────────────────────────────────
    pdf.section_title("Individual Comparable Breakdowns")

    for idx, (_, row) in enumerate(comps_df.iterrows()):
        if pdf.get_y() > 160:
            pdf.add_page()

        tier = row.get("match_tier", "")
        tier_color = _TIER_COLORS.get(tier, _TEXT)
        addr = str(row.get("address", "N/A"))
        city = str(row.get("city", ""))
        zipcode = str(row.get("zipcode", ""))

        # Comp header
        pdf.set_font("DejaVu", "B", 9)
        pdf.set_text_color(*tier_color)
        pdf.cell(0, 6, f"Comp #{idx + 1}: {addr}, {city} {zipcode}", ln=True)

        # Details in two columns
        pdf.set_text_color(*_TEXT)
        details = [
            ("Sale Price", _fmt_currency(row.get("sale_price"))),
            ("Sale Date", str(row.get("sale_date", "N/A"))),
            ("Distance", f"{_fmt_num(row.get('distance_miles'), 2)} mi"),
            ("Similarity", f"{_fmt_num(row.get('similarity'), 0)}%"),
            ("Beds / Baths", f"{row.get('bedrooms', 'N/A')} / {row.get('bathrooms', 'N/A')}"),
            ("SqFt", _fmt_num(row.get("sqft"))),
            ("Lot Size", _fmt_num(row.get("lot_size"))),
            ("Year Built", str(row.get("year_built", "N/A"))),
            ("$/SqFt", _fmt_currency(row.get("price_per_sqft"))),
            ("Match Tier", tier),
        ]

        col = 0
        for label, val in details:
            if col == 0:
                x_save = pdf.get_x()
            pdf.key_value(label + ":", val, w_label=28, w_value=40)
            col += 1

        # Price comparison to appraised value
        if appraised_value and pd.notna(row.get("sale_price")):
            diff = row["sale_price"] - appraised_value
            if diff > 0:
                pdf.set_font("DejaVu", "B", 8)
                pdf.set_text_color(*_GREEN)
                pdf.cell(0, 5, f"  ↑ {_fmt_currency(diff)} above appraised value", ln=True)
            elif diff < 0:
                pdf.set_font("DejaVu", "", 8)
                pdf.set_text_color(*_RED)
                pdf.cell(0, 5, f"  ↓ {_fmt_currency(abs(diff))} below appraised value", ln=True)
            pdf.set_text_color(*_TEXT)

        pdf.ln(3)

    # ── Disclaimer ────────────────────────────────────────────────────
    pdf.ln(4)
    pdf.set_font("DejaVu", "", 7)
    pdf.set_text_color(*_TEXT_DIM)
    pdf.multi_cell(
        0, 4,
        "Disclaimer: This report is generated for informational purposes only and does not constitute "
        "a formal appraisal. Comparable sales data is sourced from public records and third-party providers. "
        "All values should be independently verified. This analysis is intended to supplement the appraisal "
        "review process and support a request for reconsideration of value.",
    )

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
