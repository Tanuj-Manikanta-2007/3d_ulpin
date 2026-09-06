"""Build the project documentation PDF.

Usage: python docs/build_pdf.py
Requires: reportlab (documentation-generation dependency, not application runtime).
"""

from pathlib import Path
import re

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "3d_ulpin_system_documentation.md"
OUTPUT = ROOT / "3d_ulpin_system_documentation.pdf"


def clean_inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", text)
    text = text.replace("&", "&amp;").replace("<font name='Courier'>", "@@FONT@@")
    text = text.replace("</font>", "@@ENDFONT@@")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("@@FONT@@", "<font name='Courier'>").replace("@@ENDFONT@@", "</font>")
    return text


def build_pdf() -> None:
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="TitleCustom", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=22, leading=27, alignment=TA_CENTER, spaceAfter=18,
    ))
    styles.add(ParagraphStyle(
        name="H1Custom", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=15, leading=19, spaceBefore=12, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="H2Custom", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=11.5, leading=15, spaceBefore=9, spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="BodyCustom", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9.4, leading=13.2, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="BulletCustom", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9.2, leading=12.5, leftIndent=14, firstLineIndent=-8, spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name="SmallCustom", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=7.5, leading=10, textColor="#555555",
    ))

    document = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="3D ULPIN Cadastral and Vertical Property Identification System",
        author="3D ULPIN Project",
    )

    story = []
    in_code = False
    code_lines = []

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            story.append(Preformatted("\n".join(code_lines), styles["Code"]))
            story.append(Spacer(1, 6))
            code_lines = []

    for raw_line in SOURCE.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if line == "---":
            flush_code()
            story.append(PageBreak())
            continue
        if line.startswith("```"):
            if in_code:
                flush_code()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line:
            story.append(Spacer(1, 3))
            continue
        if line.startswith("# "):
            story.append(Paragraph(clean_inline(line[2:]), styles["TitleCustom"]))
            story.append(Paragraph("Project technical documentation | Prototype implementation", styles["SmallCustom"]))
            story.append(Spacer(1, 10))
        elif line.startswith("## "):
            story.append(Paragraph(clean_inline(line[3:]), styles["H1Custom"]))
        elif line.startswith("### "):
            story.append(Paragraph(clean_inline(line[4:]), styles["H2Custom"]))
        elif line.startswith("- "):
            story.append(Paragraph("&#8226; " + clean_inline(line[2:]), styles["BulletCustom"]))
        elif re.match(r"^\d+\. ", line):
            story.append(Paragraph(clean_inline(line), styles["BulletCustom"]))
        elif line.startswith("|"):
            story.append(Paragraph(clean_inline(line.replace("|", "   ")), styles["SmallCustom"]))
        else:
            story.append(Paragraph(clean_inline(line), styles["BodyCustom"]))

    flush_code()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColorRGB(0.35, 0.35, 0.35)
        canvas.drawString(18 * mm, 9 * mm, "3D ULPIN System Documentation")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    print(OUTPUT)


if __name__ == "__main__":
    build_pdf()
