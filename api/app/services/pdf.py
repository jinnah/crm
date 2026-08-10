"""Server-side PDF generation for quotes, invoices and receipts.

ReportLab with built-in (bundled) fonts only — nothing is fetched remotely
during generation. Customer text is drawn as escaped text, never interpreted
as HTML or template code. Output is real selectable text. The caller hashes
and stores the exact bytes as the immutable version; a later re-render never
replaces a sent version.
"""

import io
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models import CommunicationSettings

_NAVY = colors.HexColor("#1B2A41")
_TEAL = colors.HexColor("#0E7490")
_GRAY = colors.HexColor("#5B6472")
_LIGHT = colors.HexColor("#EEF1F4")

_BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13)
_SMALL = ParagraphStyle("small", parent=_BASE, fontSize=8, leading=11, textColor=_GRAY)
_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=_NAVY)
_H2 = ParagraphStyle("h2", parent=_BASE, fontName="Helvetica-Bold", textColor=_NAVY)
_RIGHT = ParagraphStyle("right", parent=_BASE, alignment=2)
_STATE = ParagraphStyle(
    "state",
    fontName="Helvetica-Bold",
    fontSize=14,
    leading=18,
    textColor=colors.HexColor("#B42318"),
)

_TITLES = {"quote": "QUOTE", "invoice": "INVOICE", "receipt": "RECEIPT"}


def _money(amount_minor: int, currency: str) -> str:
    major, minor = divmod(abs(int(amount_minor)), 100)
    sign = "-" if amount_minor < 0 else ""
    return f"{sign}{major:,}.{minor:02d} {currency}"


def _quantity(quantity_milli: int) -> str:
    value = quantity_milli / 1000
    return f"{value:g}"


def _date(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%B %d, %Y")
    except ValueError:
        return ""


def _p(text: str, style: ParagraphStyle = _BASE) -> Paragraph:
    # Escape everything customer-controlled; newlines become line breaks.
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def render_commercial_pdf(
    payload: dict,
    settings_row: CommunicationSettings,
    *,
    state_label: str | None = None,
) -> bytes:
    """Render one snapshot payload to PDF bytes. `state_label` marks VOIDED
    or SUPERSEDED copies; the originally issued bytes never carry one."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{_TITLES.get(payload['kind'], 'DOCUMENT')} {payload.get('number', '')}",
        author=payload["business"]["name"] or "Service CRM",
    )
    story: list = []

    business = payload["business"]
    header_left: list = []
    if settings_row.logo_bytes:
        try:
            logo = Image(io.BytesIO(settings_row.logo_bytes))
            scale = min(1.0, (18 * mm) / logo.imageHeight, (60 * mm) / logo.imageWidth)
            logo.drawWidth = logo.imageWidth * scale
            logo.drawHeight = logo.imageHeight * scale
            header_left.append(logo)
        except OSError:
            pass
    header_left.append(_p(business["name"], _H2))
    contact_bits = [
        business.get("address", ""),
        business.get("phone", ""),
        business.get("email", ""),
    ]
    for bit in contact_bits:
        if bit:
            header_left.append(_p(bit, _SMALL))
    if business.get("registration_id"):
        header_left.append(_p(f"Reg/Tax ID: {business['registration_id']}", _SMALL))

    title_cell: list = [
        Paragraph(_TITLES.get(payload["kind"], "DOCUMENT"), _H1),
        _p(payload.get("number") or "", _H2),
    ]
    if state_label:
        title_cell.append(Paragraph(escape(state_label), _STATE))
    header = Table(
        [[header_left, title_cell]],
        colWidths=[110 * mm, 64 * mm],
        style=TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ]
        ),
    )
    story.append(header)
    story.append(Spacer(1, 6 * mm))

    customer = payload["customer"]
    job = payload["job"]
    meta_rows = [[_p("For", _H2), _p("Job", _H2), _p("Dates", _H2)]]
    customer_lines = [customer["name"], customer.get("email") or "", customer.get("phone") or ""]
    job_lines = [
        f"{job['number']}" + (f" — {job['title']}" if job.get("title") else ""),
        job.get("service_address") or "",
    ]
    date_lines: list[str] = []
    if payload.get("issued_at"):
        date_lines.append(f"Issued: {_date(payload['issued_at'])}")
    if payload["kind"] == "quote" and payload.get("valid_until"):
        date_lines.append(f"Valid until: {_date(payload['valid_until'])}")
    if payload["kind"] == "invoice" and payload.get("due_at"):
        date_lines.append(f"Due: {_date(payload['due_at'])}")
    if payload["kind"] == "receipt" and payload.get("payment"):
        date_lines.append(f"Paid: {_date(payload['payment']['paid_on'])}")
    meta_rows.append(
        [
            [_p(line) for line in customer_lines if line],
            [_p(line) for line in job_lines if line],
            [_p(line) for line in date_lines],
        ]
    )
    story.append(
        Table(
            meta_rows,
            colWidths=[62 * mm, 62 * mm, 50 * mm],
            style=TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, _LIGHT),
                ]
            ),
        )
    )
    story.append(Spacer(1, 6 * mm))

    currency = payload["currency"]
    if payload["kind"] == "receipt" and payload.get("payment"):
        payment = payload["payment"]
        rows = [
            [_p("Payment for", _H2), _p("Method", _H2), Paragraph("Amount", _RIGHT)],
            [
                _p(f"Invoice {payment['invoice_number']}"),
                _p(payment["method"].replace("_", " ").capitalize()),
                Paragraph(escape(_money(payment["amount_minor"], currency)), _RIGHT),
            ],
        ]
        if payment.get("reference"):
            rows.append([_p("Reference"), _p(payment["reference"]), _p("")])
        rows.append(
            [
                _p("Invoice total"),
                _p(""),
                Paragraph(escape(_money(payment["invoice_total_minor"], currency)), _RIGHT),
            ]
        )
        rows.append(
            [
                _p("Remaining balance"),
                _p(""),
                Paragraph(escape(_money(payment["remaining_minor"], currency)), _RIGHT),
            ]
        )
        story.append(
            Table(
                rows,
                colWidths=[70 * mm, 52 * mm, 52 * mm],
                style=_line_table_style(len(rows)),
            )
        )
    else:
        line_rows = [
            [
                _p("Description", _H2),
                Paragraph("Qty", _RIGHT),
                Paragraph("Unit price", _RIGHT),
                Paragraph("Line total", _RIGHT),
            ]
        ]
        for line in payload["lines"]:
            description = line["description"]
            extras = []
            if line.get("discount_bp"):
                extras.append(f"{line['discount_bp'] / 100:g}% off")
            if line.get("tax_rate_bp"):
                extras.append(f"tax {line['tax_rate_bp'] / 100:g}%")
            if extras:
                description += f" ({', '.join(extras)})"
            quantity = _quantity(line["quantity_milli"])
            if line.get("unit"):
                quantity += f" {line['unit']}"
            line_rows.append(
                [
                    _p(description),
                    Paragraph(escape(quantity), _RIGHT),
                    Paragraph(escape(_money(line["unit_price_minor"], currency)), _RIGHT),
                    Paragraph(escape(_money(line["line_total_minor"], currency)), _RIGHT),
                ]
            )
        story.append(
            Table(
                line_rows,
                colWidths=[86 * mm, 24 * mm, 32 * mm, 32 * mm],
                repeatRows=1,
                style=_line_table_style(len(line_rows)),
            )
        )

        totals = payload["totals"]
        total_rows = [["Subtotal", _money(totals["subtotal_minor"], currency)]]
        if totals["discount_total_minor"]:
            total_rows.append(["Discount", "-" + _money(totals["discount_total_minor"], currency)])
        if totals["tax_total_minor"]:
            total_rows.append(["Tax", _money(totals["tax_total_minor"], currency)])
        total_rows.append(["Total", _money(totals["total_minor"], currency)])
        story.append(Spacer(1, 3 * mm))
        story.append(
            Table(
                [
                    [
                        "",
                        _p(label, _H2 if label == "Total" else _BASE),
                        Paragraph(
                            escape(value),
                            _RIGHT
                            if label != "Total"
                            else ParagraphStyle(
                                "tr", parent=_RIGHT, fontName="Helvetica-Bold", textColor=_TEAL
                            ),
                        ),
                    ]
                    for label, value in total_rows
                ],
                colWidths=[86 * mm, 56 * mm, 32 * mm],
                style=TableStyle(
                    [
                        ("LINEABOVE", (1, -1), (-1, -1), 0.75, _NAVY),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ]
                ),
            )
        )

    for heading, key in (("Notes", "customer_notes"), ("Terms", "terms")):
        text = payload.get(key) or ""
        if text.strip():
            story.append(Spacer(1, 5 * mm))
            story.append(_p(heading, _H2))
            story.append(_p(text))

    doc.build(story)
    return buffer.getvalue()


def _line_table_style(row_count: int) -> TableStyle:
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, _NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for row in range(1, row_count):
        if row % 2 == 0:
            style.append(("BACKGROUND", (0, row), (-1, row), _LIGHT))
    return TableStyle(style)
