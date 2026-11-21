"""Print an account-scoped copy of one immutable report version."""
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import ImageDraw
from reportlab.graphics.shapes import Drawing, PolyLine
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FONT_DIR = Path(__file__).resolve().parents[2] / "assets/fonts"
INK = colors.HexColor("#191919")
MUTED = colors.HexColor("#505050")
LINE = colors.HexColor("#b3b3b3")
WIDTH = A4[0] - 84
REGULAR, BOLD, LABEL = "Spectral", "SpectralSemibold", "BeVietnam"
LABELS = {
    "VISIBLE_CHANGE": "Có khác biệt cần kiểm tra",
    "NO_VISIBLE_CHANGE": "Chưa thấy thay đổi vượt ngưỡng",
    "INCONCLUSIVE": "Chưa đủ bằng chứng",
}
MATCHES = {
    "MATCHED_SUFFICIENT": "Hai ảnh có đủ chi tiết để đối chiếu.",
    "MATCHED_INSUFFICIENT": "Đã tìm được cùng vùng nhưng ảnh chưa đủ rõ.",
    "NO_RELIABLE_MATCH": "Chưa tìm được ảnh gửi tương ứng đủ tin cậy.",
}


def local_time(value):
    if not value:
        return "Chưa ghi nhận"
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    return stamp.astimezone(timezone(timedelta(hours=7))).strftime("%d/%m/%Y · %H:%M")


def paragraph(value, size=11, *, bold=False, label=False, center=False, color=INK, before=0, after=0, keep=False):
    style = ParagraphStyle("text", fontName=LABEL if label else BOLD if bold else REGULAR, fontSize=size,
                           leading=size * 1.32, alignment=TA_CENTER if center else TA_LEFT, textColor=color, spaceAfter=after,
                           spaceBefore=before, keepWithNext=keep, splitLongWords=True)
    return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)


def section(title, *, keep=True):
    return paragraph(title, 14, bold=True, before=10, after=4, keep=keep)


def grid(rows, widths, *, columns=False, centered=False):
    table = Table(rows, colWidths=widths, hAlign="LEFT", splitByRow=0)
    rules = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, 0), (-1, 0), .45, LINE),
        ("LINEBELOW", (0, -1), (-1, -1), .45, LINE),
    ]
    if columns:
        rules.append(("INNERGRID", (0, 0), (-1, -1), .35, LINE))
    if centered:
        rules.append(("ALIGN", (0, 0), (-1, -1), "CENTER"))
    table.setStyle(TableStyle(rules))
    return table


def field(label, value):
    return [paragraph(label, 8, label=True, color=MUTED), paragraph(value, 11.5, bold=True)]


def photo(image, regions):
    if image is None:
        return paragraph("Chưa có ảnh tương ứng", 10, center=True, color=MUTED)
    preview = image.copy()
    preview.thumbnail((1050, 800))
    draw = ImageDraw.Draw(preview)
    for region in regions:
        points = [(round(x * (preview.width - 1) / 10000),
                   round(y * (preview.height - 1) / 10000)) for x, y in region]
        loop = points + points[:1]
        draw.line(loop, fill="white", width=max(4, preview.width // 125))
        draw.line(loop, fill="#171717", width=max(2, preview.width // 220))
    stream = BytesIO()
    preview.save(stream, format="JPEG", quality=91)
    scale = min((WIDTH / 2 - 18) / preview.width, 148 / preview.height)
    return Image(stream, preview.width * scale, preview.height * scale, hAlign="CENTER")


def signature_column(label, review):
    items = [paragraph(label, 11, bold=True, center=True, after=3)]
    signature = review.get("signature") if review else None
    if signature:
        if signature["method"] == "DRAWN":
            width, height = 99, 33
            mark = Drawing(width, height)
            mark.hAlign = "CENTER"
            for stroke in signature["strokes"]:
                points = [v for x, y in stroke for v in (width*x/10000, height*(1-y/10000))]
                mark.add(PolyLine(points, strokeColor=INK, strokeWidth=1.1, strokeLineCap=1))
            items.append(mark)
        else:
            items.append(Spacer(1, 33))
        items.append(paragraph(signature["name"], 12, bold=True, center=True))
        method = "Ký bằng họ tên" if signature["method"] == "TYPED" else "Ký tay trực tuyến"
        items.append(paragraph(f"{method} · {local_time(signature['signed_at'])}", 8, label=True, center=True, color=MUTED))
    else:
        items.extend([Spacer(1, 33), paragraph("Chưa ký", 11, center=True)])
        if review:
            items.append(paragraph(f"Đã kiểm tra · {local_time(review['created_at'])}", 8, center=True, color=MUTED))
    return items


def render_report(document, canonical_hash, pictures, confirmations, chain, *, sender, recipient, sample):
    for font, path in [(REGULAR, "spectral/Spectral-Regular.ttf"), (BOLD, "spectral/Spectral-SemiBold.ttf"),
                       (LABEL, "BeVietnamPro-Regular.ttf")]:
        if font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font, str(FONT_DIR / path)))

    match = document["match"]
    reference, query = match.get("reference_asset_id"), match["query_asset_id"]
    observations = document["observations"]
    regions = [o["query_polygon"] for o in observations if o["state"] == "VISIBLE_CHANGE"
               and o.get("query_polygon") and query in o["evidence_asset_ids"]]
    names = {query: "Ảnh B"}
    if reference:
        names[reference] = "Ảnh A"
    for asset in document["evidence_asset_ids"]:
        if asset not in names:
            names[asset] = f"Ảnh hồ sơ {len(names) + 1}"
    reviews = {item["role"]: item for item in confirmations}
    version = document["version"]
    story = [
        paragraph("Biên bản tình trạng kiện hàng", 23, bold=True, center=True, before=4, after=3),
        paragraph(f"Phiên bản {version}   ·   Lập ngày {local_time(document['created_at'])} (giờ Việt Nam)",
                  9, center=True, label=True, color=MUTED, after=14),
        grid([[field("Mã kiện / mã đơn", document["reference"]), field("Tên hàng", document["title"])],
              [field("Bên gửi", sender), field("Người nhận theo đơn", recipient)]],
             [WIDTH / 2] * 2, columns=True),
    ]
    if sample:
        story.append(paragraph(sample, 8, color=MUTED, before=3))
    story.extend([section("Ảnh đối chiếu"), grid([
        [paragraph("Ảnh A · Trước khi gửi", 10.5, bold=True, center=True), paragraph("Ảnh B · Lúc nhận kiện", 10.5, bold=True, center=True)],
        [photo(pictures.get(reference), []), photo(pictures.get(query), regions)],
    ], [WIDTH / 2] * 2, centered=True),
        paragraph(MATCHES[match["status"]] + (" Đường đen viền trắng trên ảnh B đánh dấu vùng cần kiểm tra." if regions else ""),
                  9, color=MUTED, before=4), section("Ghi nhận tình trạng")])
    if not observations:
        story.append(paragraph("Phiên bản này chưa có nhận xét."))
    for i, item in enumerate(observations, 1):
        state = LABELS[item["state"]]
        story.append(paragraph(f"{i}. {state}" if len(observations) > 1 else state, 11, bold=True, keep=True, before=3))
        story.append(paragraph(item["text"]))
        source = "Người kiểm tra" if item["author_type"] == "HUMAN" else "Bộ đối chiếu ảnh"
        story.append(paragraph(source + "   ·   " + ", ".join(names[k] for k in item["evidence_asset_ids"]), 8, color=MUTED))
    if document.get("user_notes"):
        # Reserve the heading and two lines, while letting longer notes span pages.
        story.extend([CondPageBreak(64), section("Ghi chú của hai bên", keep=False), paragraph(document["user_notes"])])
    story.extend([paragraph(document["scope"], 9, color=MUTED, before=5), section("Xác nhận của hai bên"),
                  paragraph(f"{len(confirmations)}/2 bên đã kiểm tra nội dung phiên bản {version}.", 9, after=5, keep=True),
                  grid([[signature_column("Bên gửi", reviews.get("MERCHANT")),
                         signature_column("Bên nhận", reviews.get("BUYER"))]], [WIDTH / 2] * 2, columns=True, centered=True)])
    story.extend([PageBreak(), paragraph("Phụ lục đối soát", 22, bold=True, center=True, after=3),
                  paragraph(f"{document['reference']}   /   Phiên bản {version}", 9, label=True, center=True, color=MUTED, after=12),
                  section("Toàn vẹn biên bản"),
                  paragraph("Dùng mã SHA-256 dưới đây để đối chiếu với tệp JSON của cùng phiên bản.", 10, after=5),
                  grid([[field("SHA-256 của nội dung JSON chuẩn hóa", canonical_hash)]], [WIDTH]),
                  section("Danh mục ảnh trong hồ sơ")])
    for asset, label in sorted(names.items(), key=lambda pair: pair[1]):
        story.append(grid([[paragraph(label, 9, bold=True), paragraph(asset, 9)]], [115, WIDTH-115]))
    story.extend([section("Phương pháp đối chiếu"), paragraph(MATCHES[match["status"]]),
                  paragraph(f"{document['model_version']}   /   {document['inference_kind']}", 9, color=MUTED)])
    if match.get("reason_codes"):
        story.append(paragraph("Mã lý do: " + ", ".join(match["reason_codes"]), 9))
    story.extend([section("Dấu xác nhận khi xuất"), paragraph(
        f"Xuất lúc {local_time(datetime.now(UTC))} (giờ Việt Nam). Chữ ký trong biên bản ghi nhận việc tài khoản đã ký phiên bản {version}. Việc phân bổ quỹ cần thực hiện riêng trong mục bảo đảm.")])
    for item in confirmations:
        signed = item.get("signature")
        if signed:
            party = "Người gửi" if item["role"] == "MERCHANT" else "Người nhận"
            story.append(paragraph(f"{party} · Tài khoản {signed['account_name']} · Ký lúc {local_time(signed['signed_at'])}", 9, before=3))
    if chain:
        states = ["Chờ nạp quỹ", "Chờ hai bên chấp nhận", "Đang bảo đảm", "Đang tranh chấp", "Đã phân bổ", "Đã hủy"]
        state = dict(enumerate(states)).get(int(chain["state"]), str(chain["state"])) if str(chain.get("state")).isdigit() else "Chưa xác minh"
        story.extend([section("Thông tin quỹ bảo đảm"), grid([
            [field("Mạng", chain["chain_id"]), field("Trạng thái khi đồng bộ", state)],
            [field("Hợp đồng", chain["address"]), field("Lần đồng bộ gần nhất", local_time(chain["synced_at"]))],
        ], [WIDTH / 2] * 2, columns=True)])

    stream = BytesIO()
    pdf = SimpleDocTemplate(stream, pagesize=A4, leftMargin=36, rightMargin=36,
                            topMargin=53, bottomMargin=42, title=f"Biên bản {document['reference']} · v{version}",
                            author="ParcelLedger")

    def furniture(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(MUTED)
        canvas.setFont(LABEL, 8)
        canvas.drawString(42, h-34, "ParcelLedger")
        canvas.drawRightString(w-42, h-34, "HỒ SƠ GIAO NHẬN")
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(.5)
        canvas.line(42, h-46, w-42, h-46)
        canvas.line(42, 34, w-42, 34)
        canvas.drawString(42, 20, f"ParcelLedger   /   Phiên bản {version}   /   {canonical_hash[:12]}")
        canvas.drawRightString(w-42, 20, f"Trang {doc.page}")
        canvas.restoreState()

    pdf.build(story, onFirstPage=furniture, onLaterPages=furniture)
    return stream.getvalue()
