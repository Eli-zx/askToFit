"""生成服装售前知识库的业务文档（DOCX / PDF / PPTX / Markdown / PNG / 旧版 DOC）。

所有内容围绕虚构店铺「星辰服饰旗舰店 2026 春夏」编写，覆盖退换货、尺码、
面料、发票、物流、会员优惠、大促与门店导购等真实业务场景。文档中包含表格、
流程图、尺码图和活动海报，用于验证多格式解析、表格抽取与图片 OCR 链路。

用法：
    uv run python scripts/build_business_docs.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pymupdf as fitz
import pythoncom
import win32com.client
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Inches as PInches
from pptx.util import Pt as PPt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

OUTPUT_DIR = PROJECT_ROOT / "data" / "business_docs"
ASSET_DIR = OUTPUT_DIR / "_assets"

FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD_PATH = "C:/Windows/Fonts/msyhbd.ttc"
FONT_NAME = "微软雅黑"

BRAND = RGBColor(0x4F, 0x46, 0xE5)
INK = RGBColor(0x1E, 0x29, 0x3B)


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def _set_docx_font(target, size: int | None = None, bold: bool = False) -> None:
    target.font.name = FONT_NAME
    target.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
    if size:
        target.font.size = Pt(size)
    target.font.bold = bold


def _new_docx() -> Document:
    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = FONT_NAME
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
    for style_name in ("Title", "Heading 1", "Heading 2"):
        style = document.styles[style_name]
        style.font.name = FONT_NAME
        style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
    return document


def _add_paragraph(document: Document, text: str, *, bold: bool = False):
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    _set_docx_font(run, bold=bold)
    if bold:
        run.font.color.rgb = INK
    return paragraph


def _add_bullets(document: Document, items: list[str]) -> None:
    for item in items:
        paragraph = document.add_paragraph(style="List Bullet")
        _set_docx_font(paragraph.add_run(item))


def _add_table(
    document: Document, headers: list[str], rows: list[list[str]]
) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = ""
        _set_docx_font(cell.paragraphs[0].add_run(header), size=10, bold=True)
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = ""
            _set_docx_font(cells[index].paragraphs[0].add_run(value), size=10)
    document.add_paragraph()


def _draw_flow(path: Path, title: str, steps: list[str]) -> Path:
    width, height = 1000, 460
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(FONT_BOLD_PATH, 36)
    step_font = ImageFont.truetype(FONT_PATH, 24)
    draw.rectangle((0, 0, width, 82), fill=(79, 70, 229))
    draw.text((36, 20), title, font=title_font, fill="white")

    top = 150
    box_h = 84
    gap = 34
    for index, step in enumerate(steps):
        y = top + index * (box_h + gap)
        draw.rounded_rectangle(
            (80, y, width - 80, y + box_h),
            radius=18,
            fill=(240, 239, 255),
            outline=(79, 70, 229),
            width=2,
        )
        draw.text((110, y + 24), f"{index + 1}. {step}", font=step_font, fill=(30, 41, 59))
        if index < len(steps) - 1:
            draw.line(
                (width // 2, y + box_h, width // 2, y + box_h + gap),
                fill=(79, 70, 229),
                width=4,
            )
            draw.polygon(
                [
                    (width // 2 - 9, y + box_h + gap - 4),
                    (width // 2 + 9, y + box_h + gap - 4),
                    (width // 2, y + box_h + gap + 10),
                ],
                fill=(79, 70, 229),
            )
    image.save(path)
    return path


def _draw_size_chart(path: Path) -> Path:
    width, height = 1000, 700
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(FONT_BOLD_PATH, 34)
    body_font = ImageFont.truetype(FONT_PATH, 23)
    small_font = ImageFont.truetype(FONT_PATH, 20)
    draw.rectangle((0, 0, width, 76), fill=(14, 116, 144))
    draw.text((34, 18), "星辰服饰 · 尺码速查图", font=title_font, fill="white")

    headers = ["尺码", "身高", "体重", "胸围"]
    rows = [
        ["S", "150-158cm", "42-48kg", "76-82cm"],
        ["M", "155-163cm", "48-55kg", "82-88cm"],
        ["L", "160-168cm", "55-62kg", "88-94cm"],
        ["XL", "165-173cm", "62-70kg", "94-100cm"],
    ]
    x0, y0, col_w, row_h = 70, 120, 215, 64
    for col, header in enumerate(headers):
        draw.rectangle(
            (x0 + col * col_w, y0, x0 + (col + 1) * col_w, y0 + row_h),
            fill=(224, 242, 254),
            outline=(14, 116, 144),
        )
        draw.text((x0 + col * col_w + 18, y0 + 18), header, font=body_font, fill=(30, 41, 59))
    for row_index, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            draw.rectangle(
                (
                    x0 + col * col_w,
                    y0 + row_index * row_h,
                    x0 + (col + 1) * col_w,
                    y0 + (row_index + 1) * row_h,
                ),
                fill="white",
                outline=(148, 163, 184),
            )
            draw.text(
                (x0 + col * col_w + 18, y0 + row_index * row_h + 18),
                value,
                font=small_font,
                fill=(30, 41, 59),
            )
    draw.text((70, 500), "女装 M 码：身高 155-163cm，体重 48-55kg", font=body_font, fill=(30, 41, 59))
    draw.text((70, 546), "修身版型建议选大一码，宽松版型按正常码选择。", font=body_font, fill=(30, 41, 59))
    draw.text((70, 592), "尺码不合适支持 7 天无理由换货。", font=body_font, fill=(14, 116, 144))
    image.save(path)
    return path


def _draw_fabric_card(path: Path, title: str, lines: list[str], color: str) -> Path:
    width, height = 900, 520
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(FONT_BOLD_PATH, 34)
    body_font = ImageFont.truetype(FONT_PATH, 25)
    draw.rounded_rectangle(
        (8, 8, width - 8, height - 8), radius=22, outline=color, width=4
    )
    draw.rectangle((8, 8, width - 8, 90), fill=color)
    draw.text((36, 24), title, font=title_font, fill="white")
    y = 130
    for line in lines:
        draw.text((40, y), line, font=body_font, fill=(30, 41, 59))
        y += 62
    image.save(path)
    return path


def _draw_poster(path: Path) -> Path:
    width, height = 1200, 1600
    image = Image.new("RGB", (width, height), (49, 27, 146))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 360), fill=(249, 115, 22))
    draw.rectangle((0, 360, width, 410), fill=(253, 224, 71))

    title_font = ImageFont.truetype(FONT_BOLD_PATH, 96)
    subtitle_font = ImageFont.truetype(FONT_PATH, 40)
    section_font = ImageFont.truetype(FONT_BOLD_PATH, 46)
    body_font = ImageFont.truetype(FONT_PATH, 32)
    small_font = ImageFont.truetype(FONT_PATH, 26)

    draw.text((70, 80), "618 年中大促", font=title_font, fill="white")
    draw.text((74, 210), "星辰服饰旗舰店 · 2026 春夏", font=subtitle_font, fill="white")
    draw.text((74, 290), "活动时间：6 月 1 日 - 6 月 20 日", font=subtitle_font, fill="white")

    blocks = [
        ("预售定金", ["5 月 26 日 - 5 月 31 日", "定金 50 元抵 100 元"]),
        ("跨店满减", ["每满 300 元减 50 元", "上不封顶，可与店铺券叠加"]),
        ("会员日", ["6 月 8 日会员日", "金卡及以上会员 9 折"]),
        ("叠加规则", ["会员日折扣与跨店满减可叠加", "与店铺券不叠加，系统取最优方案"]),
    ]
    y = 500
    for title, lines in blocks:
        draw.rounded_rectangle(
            (70, y, width - 70, y + 220),
            radius=24,
            fill=(255, 255, 255),
        )
        draw.text((110, y + 28), title, font=section_font, fill=(49, 27, 146))
        line_y = y + 100
        for line in lines:
            draw.text((112, line_y), line, font=body_font, fill=(30, 41, 59))
            line_y += 52
        y += 260

    draw.rectangle((0, 1500, width, height), fill=(249, 115, 22))
    draw.text(
        (70, 1530),
        "定金未付尾款不退；付尾款后整单退款，定金可退。",
        font=small_font,
        fill="white",
    )
    draw.text(
        (70, 1568),
        "部分退款按商品金额占比退定金，膨胀优惠按比例扣减。",
        font=small_font,
        fill="white",
    )
    image.save(path)
    return path


# --------------------------------------------------------------------------- #
# PDF 写入器
# --------------------------------------------------------------------------- #
class PdfWriter:
    def __init__(self, path: Path, title: str) -> None:
        self.path = path
        self.title = title
        self.document = fitz.open()
        self.page: fitz.Page | None = None
        self.y = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.page = self.document.new_page()
        self.y = 64

    def _ensure(self, height: float) -> None:
        if self.y + height > 800:
            self._new_page()

    def text(
        self,
        value: str,
        *,
        size: float = 11,
        bold: bool = False,
        color: tuple[float, float, float] = (0.12, 0.16, 0.23),
        gap: float = 8,
    ) -> None:
        wrapped = _wrap_text(value, size, 483)
        for line in wrapped:
            self._ensure(size + gap)
            if bold:
                self.page.insert_text(
                    (56, self.y),
                    line,
                    fontname="china-s",
                    fontsize=size,
                    color=color,
                    render_mode=2,
                    border_width=0.3,
                )
            else:
                self.page.insert_text(
                    (56, self.y),
                    line,
                    fontname="china-s",
                    fontsize=size,
                    color=color,
                )
            self.y += size + gap

    def heading(self, value: str, level: int = 1) -> None:
        if level == 1:
            self._ensure(34)
            self.page.draw_rect(
                fitz.Rect(56, self.y - 14, 539, self.y + 10),
                color=(0.93, 0.93, 0.99),
                fill=(0.93, 0.93, 0.99),
            )
            self.text(value, size=16, bold=True, color=(0.31, 0.27, 0.9), gap=12)
        else:
            self.text(value, size=13, bold=True, color=(0.12, 0.16, 0.23), gap=8)

    def table(self, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
        row_h = 26.0
        self._ensure((len(rows) + 1) * row_h + 12)
        x = 56.0
        for index, header in enumerate(headers):
            rect = fitz.Rect(x, self.y, x + widths[index], self.y + row_h)
            self.page.draw_rect(
                rect,
                color=(0.58, 0.55, 0.9),
                fill=(0.93, 0.93, 0.99),
                width=0.8,
            )
            self.page.insert_text(
                (x + 6, self.y + 17),
                header,
                fontname="china-s",
                fontsize=9,
                color=(0.12, 0.16, 0.23),
            )
            x += widths[index]
        self.y += row_h
        for row in rows:
            x = 56.0
            for index, value in enumerate(row):
                rect = fitz.Rect(x, self.y, x + widths[index], self.y + row_h)
                self.page.draw_rect(rect, color=(0.78, 0.8, 0.86), width=0.6)
                self.page.insert_text(
                    (x + 6, self.y + 17),
                    value,
                    fontname="china-s",
                    fontsize=9,
                    color=(0.12, 0.16, 0.23),
                )
                x += widths[index]
            self.y += row_h
        self.y += 12

    def image(self, image_path: Path, width: float, height: float) -> None:
        self._ensure(height + 16)
        rect = fitz.Rect(56, self.y, 56 + width, self.y + height)
        self.page.insert_image(rect, filename=str(image_path))
        self.y += height + 16

    def save(self) -> Path:
        self.document.set_metadata({"title": self.title})
        self.document.save(self.path)
        self.document.close()
        return self.path


def _visual_width(value: str) -> float:
    return sum(1.0 if ord(char) > 127 else 0.55 for char in value)


def _wrap_text(value: str, size: float, max_width: float) -> list[str]:
    limit = max_width / size
    lines: list[str] = []
    current = ""
    current_width = 0.0
    for char in value:
        char_width = 1.0 if ord(char) > 127 else 0.55
        if current and current_width + char_width > limit:
            lines.append(current)
            current = char
            current_width = char_width
        else:
            current += char
            current_width += char_width
    if current:
        lines.append(current)
    return lines or [""]


# --------------------------------------------------------------------------- #
# 文档 1：退换货与售后政策手册 DOCX
# --------------------------------------------------------------------------- #
def build_return_policy_docx() -> Path:
    flow = _draw_flow(
        ASSET_DIR / "售后流程图.png",
        "售后退换流程",
        ["签收后 7 天内提交申请", "客服审核吊牌与商品状态", "质量问题转质检，运费商家承担", "审核通过后寄回或上门取件", "仓库签收后 1-3 个工作日退款"],
    )
    document = _new_docx()
    title = document.add_heading("退换货与售后政策手册", level=0)
    _set_docx_font(title.runs[0], size=22)
    _add_paragraph(document, "适用店铺：星辰服饰旗舰店 2026 春夏全系商品")
    _add_paragraph(document, "生效日期：2026 年 3 月 1 日")

    document.add_heading("一、七天无理由退换", level=1)
    _add_bullets(
        document,
        [
            "自签收之日起 7 个自然日内，商品完好、吊牌与防拆扣完整、不影响二次销售，可申请无理由退换。",
            "退换商品需保持未洗涤、未穿着、无香水味、无化妆品残留，原包装与赠品一并寄回。",
            "定制类商品（定制西装、刺绣姓名、改裤长、改袖长）不支持七天无理由退换。",
            "真丝、羊绒等娇贵面料：未洗涤、未穿着方可申请无理由退换；已洗涤的真丝衬衫不支持退换，质量问题除外。",
            "内衣、袜子、泳装等贴身衣物，非质量问题不支持退换。",
        ],
    )
    _add_paragraph(document, "特别提示：真丝衬衫洗过之后不能以“不喜欢”“不合适”为由退货；若出现脱丝、染色不均等质量问题，可在 30 天内申请质检。", bold=True)

    document.add_heading("二、质量问题处理", level=1)
    _add_bullets(
        document,
        [
            "自签收之日起 30 天内，出现开线、破洞、明显色差、起球、拉链损坏等问题，可申请质量售后。",
            "质量问题退换货的往返运费由商家承担；非质量问题由买家承担寄回运费。",
            "申请时需提供商品整体图、瑕疵细节图和吊牌照片，必要时按客服指引拍摄视频。",
            "质检结果以品牌售后中心或第三方检测机构结论为准；确认质量问题后可退换或按比例补偿。",
        ],
    )

    document.add_heading("三、换货规则", level=1)
    _add_bullets(
        document,
        [
            "7 天内可申请同款换尺码或换颜色，库存不足时可转为退款。",
            "质量问题换货免费；非质量问题换货需承担 10 元基础换货运费，偏远地区按实际运费补差。",
            "换货商品寄回并验收后，新商品在 48 小时内发出；大促期间顺延至 72 小时。",
        ],
    )

    document.add_heading("四、退款时效", level=1)
    _add_bullets(
        document,
        [
            "仓库签收并验收通过后，退款审核通常在 24 小时内完成。",
            "原路退回：微信 / 支付宝 1-3 个工作日到账；银行卡 3-7 个工作日到账。",
            "使用积分、优惠券的订单，退款时按实付金额退回；已使用积分按规则返还，优惠券是否返还以券面说明为准。",
        ],
    )

    document.add_heading("五、品类退换标准", level=1)
    _add_table(
        document,
        ["商品类型", "无理由时限", "特殊要求", "质量问题时限"],
        [
            ["常规女装 / 男装", "7 天", "吊牌完整、未洗涤", "30 天"],
            ["真丝 / 羊绒", "7 天", "未洗涤、未穿着", "30 天"],
            ["定制西装", "不支持", "量体确认后生产", "30 天"],
            ["内衣 / 袜子 / 泳装", "不支持", "质量问题除外", "30 天"],
            ["配饰 / 鞋包", "7 天", "不影响二次销售", "30 天"],
        ],
    )

    document.add_heading("六、售后流程图", level=1)
    document.add_picture(str(flow), width=Inches(6.2))
    _add_paragraph(document, "图注：售后申请统一从订单页发起，客服将在 2 小时内首次响应。")

    path = OUTPUT_DIR / "退换货与售后政策手册.docx"
    document.save(path)
    return path


# --------------------------------------------------------------------------- #
# 文档 2：尺码与版型指南 PDF
# --------------------------------------------------------------------------- #
def build_size_guide_pdf() -> Path:
    chart = _draw_size_chart(ASSET_DIR / "尺码速查图.png")
    writer = PdfWriter(OUTPUT_DIR / "尺码与版型指南.pdf", "尺码与版型指南")
    writer.heading("尺码与版型指南", 1)
    writer.text("适用店铺：星辰服饰旗舰店 2026 春夏")
    writer.text("本指南帮助顾客在售前快速确认尺码，减少因尺码不合适产生的退换货。")

    writer.heading("一、量体方法", 2)
    writer.text("胸围：经过胸部最高点水平围量一周，软尺贴合身体但不勒紧。")
    writer.text("腰围：经过腰部最细处水平围量一周，保持自然呼吸。")
    writer.text("臀围：经过臀部最高点水平围量一周，双脚并拢站立。")
    writer.text("肩宽：从左肩骨外端经过后颈点到右肩骨外端的直线距离。")

    writer.heading("二、女装尺码表", 2)
    writer.table(
        ["尺码", "身高", "体重", "胸围", "腰围"],
        [
            ["S", "150-158cm", "42-48kg", "76-82cm", "58-64cm"],
            ["M", "155-163cm", "48-55kg", "82-88cm", "64-70cm"],
            ["L", "160-168cm", "55-62kg", "88-94cm", "70-76cm"],
            ["XL", "165-173cm", "62-70kg", "94-100cm", "76-82cm"],
        ],
        [58, 92, 92, 94, 94],
    )

    writer.heading("三、男装尺码表", 2)
    writer.table(
        ["尺码", "身高", "体重", "胸围", "腰围"],
        [
            ["M", "165-172cm", "58-66kg", "88-94cm", "74-80cm"],
            ["L", "172-178cm", "66-74kg", "94-100cm", "80-86cm"],
            ["XL", "178-184cm", "74-82kg", "100-106cm", "86-92cm"],
            ["XXL", "184-190cm", "82-90kg", "106-112cm", "92-98cm"],
        ],
        [58, 92, 92, 94, 94],
    )

    writer.heading("四、版型选择建议", 2)
    writer.text("修身版型：肩线与胸围贴合，建议比日常尺码选大一码。")
    writer.text("标准版型：按尺码表正常选择即可。")
    writer.text("宽松版型：衣身余量较大，喜欢合身效果可考虑选小一码。")
    writer.text("西装：按胸围加 2cm 余量选择；定制西装需现场量体并确认版型。")
    writer.text("示例：身高 175cm、体重 70kg 的男性顾客，修身款建议 XL，宽松款建议 L。")

    writer.heading("五、尺码速查图", 2)
    writer.text("下图为门店与客服常用的女装尺码速查图，包含身高、体重与胸围对照。")
    writer.image(chart, 430, 301)

    writer.heading("六、尺码不合适怎么办", 2)
    writer.text("尺码不合适支持 7 天无理由换货，吊牌需保持完整且商品未洗涤。")
    writer.text("换货时可在订单页选择“换尺码”，非质量问题需承担 10 元换货运费。")
    writer.text("定制西装、刺绣姓名等个性化商品不支持因尺码原因退换，下单前请务必确认量体数据。")
    return writer.save()


# --------------------------------------------------------------------------- #
# 文档 3：2026 春夏新品与面料卖点 PPTX
# --------------------------------------------------------------------------- #
def build_product_pptx() -> Path:
    silk = _draw_fabric_card(
        ASSET_DIR / "真丝面料卡.png",
        "真丝系列 · 19 姆米桑蚕丝",
        ["桑蚕丝 19 姆米，垂坠有光泽", "30 度以下冷水手洗", "不可机洗，不可漂白", "洗后不支持无理由退换"],
        "#7C3AED",
    )
    cotton = _draw_fabric_card(
        ASSET_DIR / "凉感棉面料卡.png",
        "凉感棉系列 · 60 支长绒棉",
        ["60 支长绒棉，透气亲肤", "支持 30 度机洗", "深浅色分开洗涤", "支持 7 天无理由退换"],
        "#0EA5E9",
    )
    suit = _draw_fabric_card(
        ASSET_DIR / "定制西装卡.png",
        "定制西装 · 羊毛混纺",
        ["羊毛混纺，挺括抗皱", "定制周期 7-10 个工作日", "量体确认后生产", "不支持七天无理由退换"],
        "#334155",
    )

    presentation = Presentation()
    presentation.slide_width = PInches(13.333)
    presentation.slide_height = PInches(7.5)

    title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    title_slide.shapes.title.text = "2026 春夏新品与面料卖点"
    title_slide.placeholders[1].text = "星辰服饰旗舰店 · 售前培训资料"

    content_slides = [
        (
            "真丝系列",
            [
                "面料：19 姆米桑蚕丝，垂坠感好，适合通勤与宴会场景。",
                "洗涤：30 度以下冷水手洗，使用中性洗涤剂，阴凉处平铺晾干。",
                "禁忌：不可机洗、不可漂白、不可高温熨烫。",
                "退换：已洗涤的真丝衬衫不支持七天无理由退换，质量问题 30 天内可申请质检。",
            ],
        ),
        (
            "凉感棉系列",
            [
                "面料：60 支长绒棉，轻薄透气，适合夏季日常穿着。",
                "洗涤：支持 30 度机洗，深浅色分开，避免长时间浸泡。",
                "卖点：吸湿排汗、不易起球、版型稳定。",
                "退换：支持 7 天无理由退换，吊牌完整且未洗涤。",
            ],
        ),
        (
            "定制西装系列",
            [
                "面料：羊毛混纺，挺括抗皱，适合商务与婚礼场景。",
                "流程：预约量体 → 选择面料 → 确认版型 → 生产 → 试穿微调。",
                "周期：下单后 7-10 个工作日完成，旺季顺延 3 天。",
                "退换：定制商品不支持七天无理由退换，质量问题 30 天内处理。",
            ],
        ),
    ]
    for title, bullets in content_slides:
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = title
        frame = slide.placeholders[1].text_frame
        frame.text = bullets[0]
        for bullet in bullets[1:]:
            paragraph = frame.add_paragraph()
            paragraph.text = bullet
        for paragraph in frame.paragraphs:
            paragraph.font.size = PPt(20)
        slide.shapes.title.text_frame.paragraphs[0].font.size = PPt(32)

    image_slides = [
        ("真丝面料护理卡", silk),
        ("凉感棉面料卡", cotton),
        ("定制西装交付说明", suit),
    ]
    for title, image_path in image_slides:
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = title
        slide.shapes.add_picture(
            str(image_path), PInches(1.4), PInches(1.9), width=PInches(10.4)
        )

    table_slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    table_slide.shapes.title.text = "面料与退换规则对照"
    table = table_slide.shapes.add_table(
        4, 4, PInches(1.0), PInches(2.0), PInches(11.3), PInches(3.2)
    ).table
    headers = ["系列", "核心面料", "洗涤方式", "退换注意"]
    rows = [
        ["真丝系列", "19 姆米桑蚕丝", "冷水手洗", "洗后不支持无理由"],
        ["凉感棉系列", "60 支长绒棉", "30 度机洗", "支持 7 天无理由"],
        ["定制西装", "羊毛混纺", "建议干洗", "不支持 7 天无理由"],
    ]
    for col, header in enumerate(headers):
        table.cell(0, col).text = header
    for row_index, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            table.cell(row_index, col).text = value

    path = OUTPUT_DIR / "2026春夏新品与面料卖点.pptx"
    presentation.save(path)
    return path


# --------------------------------------------------------------------------- #
# 文档 4：发票与对公结算制度 DOCX
# --------------------------------------------------------------------------- #
def build_invoice_docx() -> Path:
    document = _new_docx()
    title = document.add_heading("发票与对公结算制度", level=0)
    _set_docx_font(title.runs[0], size=22)
    _add_paragraph(document, "适用主体：星辰服饰旗舰店及企业采购客户")
    _add_paragraph(document, "生效日期：2026 年 1 月 1 日")

    document.add_heading("一、发票类型", level=1)
    _add_bullets(
        document,
        [
            "电子普通发票：适用于个人及不需要抵扣进项税的企业客户，开具后发送至订单预留邮箱。",
            "增值税专用发票：仅限一般纳税人企业申请，需提供完整开票资料，服装类商品适用 13% 税率。",
            "电子普通发票与纸质普通发票具有同等法律效力，可作为报销凭证。",
        ],
    )

    document.add_heading("二、开票时限", level=1)
    _add_bullets(
        document,
        [
            "订单完成后 30 天内可申请开票，超过 30 天需联系客服提交特殊申请。",
            "电子普通发票：资料审核通过后 1-3 个工作日内开具。",
            "增值税专用发票：资料审核通过后 3-5 个工作日内开具。",
            "每月 25 日之后提交的专票申请，顺延至次月 5 日前开具。",
        ],
    )

    document.add_heading("三、开票金额与内容", level=1)
    _add_bullets(
        document,
        [
            "开票金额为订单实付金额（含运费），优惠券与积分抵扣按折后金额开具。",
            "发票内容默认为“服装”，可开具商品明细，需在下单时或申请开票时备注。",
            "退货订单需先完成退款再处理发票红冲；部分退货按退款金额红冲后重新开具。",
        ],
    )

    document.add_heading("四、增值税专用发票资料", level=1)
    _add_table(
        document,
        ["资料项", "填写要求"],
        [
            ["公司名称", "与营业执照一致的全称"],
            ["纳税人识别号", "统一社会信用代码，18 位"],
            ["注册地址与电话", "营业执照登记地址及企业联系电话"],
            ["开户行与账号", "企业对公账户开户行名称及账号"],
            ["一般纳税人证明", "首次开票需提供扫描件或电子税务局截图"],
        ],
    )

    document.add_heading("五、对公结算", level=1)
    _add_bullets(
        document,
        [
            "企业客户支持银行转账，单笔订单满 5000 元可申请对公结算。",
            "账期客户需签署采购合同，账期最长 30 天，逾期按合同约定收取违约金。",
            "对公转账请备注订单号，财务在确认到账后 1 个工作日内安排发货。",
            "需要报价单、采购合同或盖章版对账单的客户，可联系企业客服专线。",
        ],
    )

    document.add_heading("六、常见问题", level=1)
    _add_table(
        document,
        ["问题", "回答"],
        [
            ["个人可以开专票吗？", "不可以，专票仅限一般纳税人企业申请。"],
            ["退货后发票怎么办？", "整单退货需配合红冲；部分退货按退款金额红冲后重开。"],
            ["可以修改发票抬头吗？", "未开具前可修改；已开具需红冲后重新申请。"],
            ["专票多久能收到？", "审核通过后 3-5 个工作日开具，电子专票发送至预留邮箱。"],
        ],
    )

    path = OUTPUT_DIR / "发票与对公结算制度.docx"
    document.save(path)
    return path


# --------------------------------------------------------------------------- #
# 文档 5：物流仓配与时效说明 Markdown
# --------------------------------------------------------------------------- #
def build_logistics_md() -> Path:
    content = """# 物流仓配与时效说明

适用店铺：星辰服饰旗舰店 2026 春夏

## 一、发货时效

- 现货商品：付款后 48 小时内发出；618、双 11 等大促期间延长至 72 小时。
- 定制商品：量体确认后 7-10 个工作日完成生产并发出，旺季顺延 3 天。
- 预售商品：以商品详情页标注的最晚发货时间为准。
- 每日 16:00 前付款的现货订单，尽量当日出库；16:00 后顺延至次日。

## 二、仓库分布

| 仓库 | 覆盖区域 | 说明 |
| --- | --- | --- |
| 上海仓 | 华东、华北、华中 | 女装、配饰主力仓 |
| 广州仓 | 华南、西南 | 男装、鞋包主力仓 |
| 天津仓 | 东北、西北 | 大促期间临时启用 |

系统根据收货地址与库存就近分配仓库，不支持指定发货仓。

## 三、配送时效

| 地区 | 预计时效 | 备注 |
| --- | --- | --- |
| 江浙沪皖 | 1-2 天 | 大部分订单次日达 |
| 华东其他地区 | 2-3 天 | 遇大促顺延 1-2 天 |
| 华北、华南 | 3-4 天 | 偏远区县顺延 1 天 |
| 西南、西北 | 4-6 天 | 山区线路顺延 1-2 天 |
| 新疆、西藏、内蒙古 | 5-8 天 | 偏远地区不承诺时效 |

以上时效为付款后开始计算，不含定制生产周期。

## 四、运费标准

- 单笔订单实付满 199 元包邮（偏远地区除外）。
- 未满 199 元收取基础运费 10 元。
- 新疆、西藏每单加收 15 元偏远运费；内蒙古加收 10 元。
- 港澳台及海外地区暂不发货。
- 退换货运费：质量问题由商家承担；非质量问题由买家承担寄回运费，换货另收 10 元基础运费。

## 五、承运与签收

- 合作快递：顺丰、中通、京东物流，系统自动选择，不支持指定快递。
- 包裹发出后可在订单页查看物流单号与实时轨迹。
- 收到包裹时请当场检查外包装；如有破损、受潮，请拍照并拒收，联系客服补发。
- 代收点签收视为本人签收，请在 48 小时内检查商品。

## 六、物流异常处理

- 物流轨迹超过 48 小时无更新，可联系客服发起查件，客服在 2 小时内响应。
- 确认丢件后，可选择补发或全额退款；补发商品按原订单时效发出。
- 地址填错需在发货前联系客服修改；已发货订单可尝试拦截，拦截失败需买家拒收。
- 大促期间物流压力较大，延迟 3-5 天属于正常范围，请提前向顾客说明。

## 七、常见问题

- 问：新疆发货要多久？答：预计 5-8 天，满 199 元包邮但需加收 15 元偏远运费。
- 问：可以发顺丰吗？答：系统根据线路自动选择，不支持指定快递。
- 问：今天下单什么时候发？答：现货 48 小时内发出，16:00 前付款尽量当日出库。
"""
    path = OUTPUT_DIR / "物流仓配与时效说明.md"
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 文档 6：会员积分与优惠叠加规则 Markdown
# --------------------------------------------------------------------------- #
def build_member_md() -> Path:
    content = """# 会员积分与优惠叠加规则

适用店铺：星辰服饰旗舰店 2026 春夏

## 一、会员等级

| 等级 | 累计消费 | 核心权益 |
| --- | --- | --- |
| 普通会员 | 注册即享 | 1 元积 1 分，生日券 20 元 |
| 银卡会员 | 满 2000 元 | 会员日 95 折，专属客服 |
| 金卡会员 | 满 5000 元 | 会员日 9 折，免费换货 2 次 |
| 黑金会员 | 满 10000 元 | 会员日 85 折，优先发货，专属搭配师 |

累计消费按近 12 个月实付金额计算，退款订单会扣减对应金额。

## 二、积分规则

- 每实付 1 元累计 1 积分，订单完成后 15 天到账。
- 100 积分可抵 1 元，单笔订单最多抵扣订单金额的 20%。
- 积分可与店铺券、跨店满减、会员折扣同时使用，抵扣顺序见下文。
- 退货时按退款金额扣回对应积分；积分不足时从后续订单积分中补扣。
- 积分有效期为 24 个月，过期自动清零，不另行通知。

## 三、会员日规则

- 每月 8 日为会员日，会员日折扣仅限当天 00:00-23:59 下单。
- 银卡 95 折、金卡 9 折、黑金 85 折，折扣基于商品活动价计算。
- 会员日折扣可与跨店满减叠加，不与店铺优惠券叠加；系统自动计算最优方案。
- 会员日折扣不适用于定制西装、联名限量款和已标注“不参与会员日”的商品。

## 四、跨店满减与店铺券

- 跨店满减：每满 300 元减 50 元，上不封顶，可与店铺券、会员日折扣叠加。
- 店铺券：满 299 减 30、满 599 减 80，可与跨店满减叠加。
- 限时秒杀、清仓特价商品不参与跨店满减和店铺券。
- 同一订单只能使用一张店铺券，不能与其他店铺券叠加。

## 五、优惠计算顺序

1. 商品活动价与限时折扣。
2. 店铺优惠券。
3. 跨店满减。
4. 会员日折扣。
5. 积分抵扣。

示例：金卡会员在会员日购买 1200 元商品，使用满 599 减 80 店铺券，参与每满 300 减 50。
店铺券后 1120 元，跨店满减 150 元后 970 元，会员日 9 折后 873 元，最多可用积分抵扣 174.6 元。

## 六、618 预售定金规则

- 预售时间：5 月 26 日至 5 月 31 日；尾款时间：6 月 1 日至 6 月 3 日。
- 定金 50 元抵 100 元，每位会员限购 5 件预售商品。
- 未支付尾款：订单自动关闭，定金不退。
- 支付尾款后申请整单退款：定金与尾款全额原路退回。
- 支付尾款后部分退款：定金按退款商品金额占比退回，膨胀优惠按比例扣减。
- 预售订单发货前可修改一次收货地址，发货后按物流拦截流程处理。

## 七、常见问题

- 问：会员日折扣和跨店满减能一起用吗？答：可以叠加；会员日折扣与店铺券不叠加，系统取最优方案。
- 问：积分可以提现吗？答：不可以，积分仅用于订单抵扣和积分商城兑换。
- 问：退货后优惠券会退回吗？答：以券面说明为准，有效期内的店铺券通常原路返还。
- 问：618 定金没付尾款能退吗？答：不能，未支付尾款视为放弃订单，定金不退。
"""
    path = OUTPUT_DIR / "会员积分与优惠叠加规则.md"
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 文档 7：售前咨询与异常场景 SOP PDF
# --------------------------------------------------------------------------- #
def build_sales_sop_pdf() -> Path:
    flow = _draw_flow(
        ASSET_DIR / "售前异常处理流程.png",
        "售前异常处理流程",
        ["识别顾客场景与情绪", "核对订单、商品与政策", "给出明确结论与替代方案", "无法处理时升级主管", "2 小时内回访并记录"],
    )
    writer = PdfWriter(OUTPUT_DIR / "售前咨询与异常场景SOP.pdf", "售前咨询与异常场景 SOP")
    writer.heading("售前咨询与异常场景 SOP", 1)
    writer.text("适用范围：星辰服饰旗舰店客服、门店导购与售前支持团队")
    writer.text("目标：统一口径、缩短响应时间、降低因信息不一致产生的售后退款。")

    writer.heading("一、通用应答原则", 2)
    writer.text("先确认顾客身份与订单状态，再引用政策；不确定时不要承诺，说明核实后回复。")
    writer.text("涉及退换、发票、运费等结论时，使用“政策 + 条件 + 下一步动作”的结构回答。")
    writer.text("顾客情绪激动时先共情，再给出可执行方案；无法处理时在 1 小时内升级主管。")

    writer.heading("二、高频场景与标准口径", 2)
    writer.table(
        ["场景", "标准应答要点"],
        [
            ["真丝衬衫洗过能退吗", "已洗涤不支持无理由退换；质量问题 30 天内可申请质检。"],
            ["定制西装能七天无理由吗", "定制商品不支持；质量问题 30 天内处理，量体确认后生产。"],
            ["175cm 70kg 穿什么码", "男装修身建议 XL，宽松建议 L；西装按胸围加 2cm 余量。"],
            ["专票税率和时限", "服装类专票税率 13%，资料审核后 3-5 个工作日开具。"],
            ["新疆发货时效和运费", "预计 5-8 天，满 199 元包邮但加收 15 元偏远运费。"],
            ["会员日能和满减叠加吗", "可以；会员日折扣与店铺券不叠加，系统取最优方案。"],
            ["618 定金能退吗", "未付尾款不退；付尾款后整单退款，定金可退。"],
            ["别家更便宜能补差吗", "同款 7 天内降价可申请补差，不含秒杀、会员日和优惠券。"],
        ],
        [150, 333],
    )

    writer.heading("三、典型异常场景处理", 2)
    writer.text("场景 1：顾客坚持要求已洗涤真丝衬衫退货。")
    writer.text("处理：说明洗涤后影响二次销售，不符合七天无理由条件；若怀疑质量问题，引导拍摄细节图并提交质检，质检确认后按质量问题处理。")
    writer.text("场景 2：定制西装顾客要求无理由退货。")
    writer.text("处理：说明定制商品按个人尺寸生产，不支持七天无理由；核对量体确认单，如存在版型或工艺问题，安排返修或按质量问题处理。")
    writer.text("场景 3：偏远地区顾客质疑运费。")
    writer.text("处理：说明满 199 元包邮政策，新疆、西藏需加收 15 元偏远运费，内蒙古加收 10 元；可建议合并订单或选择门店自提。")
    writer.text("场景 4：企业客户急需专票。")
    writer.text("处理：核对一般纳税人资料，说明 3-5 个工作日时限；每月 25 日后提交的顺延至次月 5 日前，可协助加急但需主管审批。")
    writer.text("场景 5：顾客在 618 预售后悔要求退定金。")
    writer.text("处理：未付尾款定金不退；已付尾款的整单退款可退定金，部分退款按商品金额占比退回。")

    writer.heading("四、升级与记录", 2)
    writer.text("以下情况必须升级主管：涉及金额超过 3000 元、顾客要求投诉、质检结论存在争议、定制商品返修两次仍未解决。")
    writer.text("升级后在工单中记录顾客诉求、已核实信息、处理方案与承诺时间，2 小时内回访。")
    writer.text("每日下班前汇总未结案工单，交接给次日值班同事。")

    writer.heading("五、异常处理流程图", 2)
    writer.image(flow, 430, 198)
    return writer.save()


# --------------------------------------------------------------------------- #
# 文档 8：618 大促活动规则海报 PNG
# --------------------------------------------------------------------------- #
def build_promo_png() -> Path:
    return _draw_poster(OUTPUT_DIR / "618大促活动规则海报.png")


# --------------------------------------------------------------------------- #
# 文档 9：门店导购快速应答卡 DOC（真实旧版 Word）
# --------------------------------------------------------------------------- #
def build_store_card_doc() -> Path:
    document = _new_docx()
    title = document.add_heading("门店导购快速应答卡", level=0)
    _set_docx_font(title.runs[0], size=22)
    _add_paragraph(document, "适用门店：星辰服饰全国直营店 · 2026 春夏")
    _add_paragraph(document, "使用方式：顾客提问时先看结论，再补充条件，避免过度承诺。")

    document.add_heading("一、面料与洗护", level=1)
    _add_table(
        document,
        ["顾客问题", "快速应答"],
        [
            ["真丝可以机洗吗？", "不可以，30 度以下冷水手洗，中性洗涤剂，阴凉平铺晾干。"],
            ["真丝洗过能退吗？", "已洗涤不支持无理由退换；质量问题 30 天内可申请质检。"],
            ["凉感棉怎么洗？", "30 度机洗，深浅色分开，避免长时间浸泡。"],
            ["羊毛西装怎么打理？", "建议干洗，悬挂通风，不要暴晒。"],
        ],
    )

    document.add_heading("二、尺码与版型", level=1)
    _add_bullets(
        document,
        [
            "身高 175cm、体重 70kg 的男性顾客：修身款建议 XL，宽松款建议 L。",
            "女装 M 码适合身高 155-163cm、体重 48-55kg。",
            "修身版型建议选大一码，宽松版型可按正常码选择。",
            "定制西装需现场量体，按胸围加 2cm 余量确认版型。",
        ],
    )

    document.add_heading("三、售后与定制", level=1)
    _add_bullets(
        document,
        [
            "常规商品支持 7 天无理由退换，吊牌完整且未洗涤。",
            "定制西装、刺绣姓名、改裤长不支持七天无理由，质量问题 30 天内处理。",
            "定制周期 7-10 个工作日，旺季顺延 3 天。",
            "质量问题退换运费由商家承担，非质量问题由顾客承担。",
        ],
    )

    document.add_heading("四、发票、物流与优惠", level=1)
    _add_table(
        document,
        ["顾客问题", "快速应答"],
        [
            ["多久能开发票？", "电子普票 1-3 个工作日，专票 3-5 个工作日，服装类专票税率 13%。"],
            ["新疆发货要多久？", "预计 5-8 天，满 199 元包邮但加收 15 元偏远运费。"],
            ["会员日能和满减叠加吗？", "可以；会员日折扣与店铺券不叠加，系统取最优方案。"],
            ["618 定金能退吗？", "未付尾款不退；付尾款后整单退款，定金可退。"],
        ],
    )

    document.add_heading("五、服务红线", level=1)
    _add_bullets(
        document,
        [
            "不承诺无法兑现的时效、折扣和退款金额。",
            "不私下收取顾客款项，不引导顾客绕过平台交易。",
            "遇到投诉、质检争议或金额超过 3000 元的诉求，立即升级店长。",
        ],
    )

    docx_path = OUTPUT_DIR / "门店导购快速应答卡.docx"
    document.save(docx_path)

    doc_path = OUTPUT_DIR / "门店导购快速应答卡.doc"
    pythoncom.CoInitialize()
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    try:
        opened = word.Documents.Open(str(docx_path.resolve()))
        opened.SaveAs2(str(doc_path.resolve()), FileFormat=0)
        opened.Close(False)
    finally:
        word.Quit()
    docx_path.unlink(missing_ok=True)
    logger.info("旧版 Word 已生成：%s", doc_path.name)
    return doc_path


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    # 清理上次生成的文档（保留 _assets）
    for path in OUTPUT_DIR.iterdir():
        if path.is_file():
            path.unlink()

    builders = [
        build_return_policy_docx,
        build_size_guide_pdf,
        build_product_pptx,
        build_invoice_docx,
        build_logistics_md,
        build_member_md,
        build_sales_sop_pdf,
        build_promo_png,
        build_store_card_doc,
    ]
    for builder in builders:
        path = builder()
        size_kb = path.stat().st_size / 1024
        logger.info("已生成 %s（%.1f KB）", path.relative_to(PROJECT_ROOT), size_kb)

    print(f"\n共生成 {len(builders)} 份业务文档，目录：{OUTPUT_DIR}")


if __name__ == "__main__":
    main()
