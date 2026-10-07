from io import BytesIO
from pathlib import Path

import qrcode
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .ticket_urls import ticket_verification_url


_FONT_REGISTERED = False


def _register_ticket_fonts():
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    root = Path(__file__).resolve().parent.parent
    regular_candidates = (
        root / "assets" / "fonts" / "DejaVuSans.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    bold_candidates = (
        root / "assets" / "fonts" / "DejaVuSans-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    )
    regular = next((path for path in regular_candidates if path.is_file()), None)
    bold = next((path for path in bold_candidates if path.is_file()), None)
    if not regular:
        raise RuntimeError(
            "A Cyrillic TrueType font is required to generate ticket PDFs. "
            "Install fonts-dejavu-core on Linux or provide assets/fonts/DejaVuSans.ttf."
        )
    pdfmetrics.registerFont(TTFont("TicketSans", str(regular)))
    pdfmetrics.registerFont(TTFont("TicketSans-Bold", str(bold or regular)))
    _FONT_REGISTERED = True


def _fit(text, font, size, width, floor=8):
    while size > floor and pdfmetrics.stringWidth(text, font, size) > width:
        size -= 0.5
    return size


def _draw_artwork_ticket(pdf, request, ticket, page_width, page_height, artwork_name):
    artwork = Path(__file__).resolve().parent.parent / "assets" / artwork_name
    if not artwork.is_file():
        return False

    image_width, image_height = 2008, 877
    pdf.drawImage(str(artwork), 0, 0, width=page_width, height=page_height, mask="auto")

    verify_url = ticket_verification_url(request, ticket)
    qr_buffer = BytesIO()
    qrcode.make(verify_url, box_size=8, border=1).save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    # The two supplied designs share the QR field's size; the VIP field sits 4 px lower.
    qr_left, qr_top, qr_size = 275, (286 if artwork_name == "vip-ticket-bg.png" else 282), 313
    pdf.drawImage(
        ImageReader(qr_buffer),
        qr_left / image_width * page_width,
        page_height - (qr_top + qr_size) / image_height * page_height,
        width=qr_size / image_width * page_width,
        height=qr_size / image_height * page_height,
        preserveAspectRatio=True,
        mask="auto",
    )

    # The tall white field beside the vertical number marker holds this ticket's sequence.
    number_left, number_top, number_width, number_height = 143, 366, 79, 144
    center_x = (number_left + number_width / 2) / image_width * page_width
    center_y = page_height - (number_top + number_height / 2) / image_height * page_height
    pdf.saveState()
    pdf.translate(center_x, center_y)
    pdf.rotate(-90)
    pdf.setFillColor(colors.HexColor("#1d1d1d"))
    pdf.setFont("TicketSans-Bold", 17)
    pdf.drawCentredString(0, -5, f"{ticket.pk:02d}")
    pdf.restoreState()
    return True


def _draw_legacy_ticket(pdf, request, order, ticket, page_width, page_height):
    pdf.setFillColor(colors.HexColor("#f7f3ee"))
    pdf.rect(0, 0, page_width, page_height, fill=1, stroke=0)
    pdf.setStrokeColor(colors.HexColor("#d5c8c0"))
    pdf.setLineWidth(0.7)
    pdf.roundRect(4 * mm, 4 * mm, page_width - 8 * mm, page_height - 8 * mm, 2 * mm, fill=0, stroke=1)

    pdf.setFillColor(colors.HexColor("#210b10"))
    pdf.roundRect(4 * mm, page_height - 26 * mm, page_width - 8 * mm, 22 * mm, 2 * mm, fill=1, stroke=0)
    pdf.rect(4 * mm, page_height - 26 * mm, page_width - 8 * mm, 2 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor("#f08a90"))
    pdf.setFont("TicketSans-Bold", 7.5)
    pdf.drawString(12 * mm, page_height - 13 * mm, "SAMARKAND LIVE  /  OFFICIAL E-TICKET")
    pdf.setFillColor(colors.white)
    pdf.setFont("TicketSans", 8)
    pdf.drawRightString(page_width - 12 * mm, page_height - 13 * mm, "SILK ROAD SAMARKAND")

    left = 13 * mm
    main_width = 127 * mm
    artist = order.concert.artist_name
    title_size = _fit(artist, "TicketSans-Bold", 24, main_width, 15)
    pdf.setFillColor(colors.HexColor("#210b10"))
    pdf.setFont("TicketSans-Bold", title_size)
    pdf.drawString(left, page_height - 42 * mm, artist)

    pdf.setFillColor(colors.HexColor("#bf1729"))
    pdf.setFont("TicketSans-Bold", 10)
    pdf.drawString(left, page_height - 51 * mm, ticket.order_item.ticket_type.title.upper())

    pdf.setFillColor(colors.HexColor("#6e6260"))
    pdf.setFont("TicketSans", 8.5)
    pdf.drawString(left, page_height - 63 * mm, f"{order.concert.event_date:%d.%m.%Y}  |  {order.concert.venue}")
    pdf.drawString(left, page_height - 72 * mm, f"Телефон: {order.phone}"[:90])

    divider_x = 157 * mm
    pdf.setStrokeColor(colors.HexColor("#c9b8b2"))
    pdf.setDash(2, 2)
    pdf.line(divider_x, 9 * mm, divider_x, page_height - 31 * mm)
    pdf.setDash()

    pdf.setFillColor(colors.HexColor("#210b10"))
    pdf.setFont("TicketSans-Bold", 9)
    pdf.drawCentredString(183 * mm, page_height - 33 * mm, f"№ {ticket.pk:02d}")
    verify_url = ticket_verification_url(request, ticket)
    qr_buffer = BytesIO()
    qrcode.make(verify_url, box_size=5, border=1).save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    pdf.drawImage(ImageReader(qr_buffer), 168 * mm, page_height - 69 * mm, width=30 * mm, height=30 * mm, preserveAspectRatio=True, mask="auto")
    pdf.setFillColor(colors.HexColor("#6e6260"))
    pdf.setFont("TicketSans", 7)
    pdf.drawCentredString(183 * mm, 11 * mm, order.reference)


def build_order_tickets_pdf(request, order):
    """Build one compact, printable PDF page for every issued ticket in an order."""
    _register_ticket_fonts()
    output = BytesIO()
    page_width = 210 * mm
    page_height = page_width * 877 / 2008
    pdf = canvas.Canvas(output, pagesize=(page_width, page_height), pageCompression=1)
    pdf.setTitle(f"Билеты на концерт - {order.reference}")
    pdf.setAuthor("Samarkand Live")
    tickets = list(order.tickets.select_related("order_item__ticket_type").order_by("sequence"))

    for ticket in tickets:
        artwork_name = (
            "vip-ticket-bg.png"
            if ticket.order_item.ticket_type.code == "vip"
            else "standard-ticket-bg.png"
        )
        if not _draw_artwork_ticket(pdf, request, ticket, page_width, page_height, artwork_name):
            _draw_legacy_ticket(pdf, request, order, ticket, page_width, page_height)
        pdf.showPage()

    pdf.save()
    return output.getvalue()
