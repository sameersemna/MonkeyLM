import re
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Preformatted

with open('/home/sameer/Public/Shared/Work/Projects/MonkeyLM/reports/ollama_suggestions.md', 'r') as f:
    text = f.read()

doc = SimpleDocTemplate('/home/sameer/Public/Shared/Work/Projects/MonkeyLM/reports/ollama_suggestions.pdf',
                        pagesize=letter,
                        leftMargin=0.75*inch, rightMargin=0.75*inch,
                        topMargin=0.75*inch, bottomMargin=0.75*inch)

styles = getSampleStyleSheet()
title_style = ParagraphStyle('Title2', parent=styles['Title'], fontSize=18, spaceAfter=12)
h1_style = ParagraphStyle('H1', parent=styles['Heading1'], fontSize=14, spaceBefore=16, spaceAfter=8,
                          textColor=colors.HexColor('#1a1a2e'))
h2_style = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=12, spaceBefore=12, spaceAfter=6,
                          textColor=colors.HexColor('#16213e'))
h3_style = ParagraphStyle('H3', parent=styles['Heading3'], fontSize=11, spaceBefore=10, spaceAfter=4,
                          textColor=colors.HexColor('#0f3460'))
body_style = ParagraphStyle('Body2', parent=styles['Normal'], fontSize=9, leading=13, spaceAfter=6)
code_style = ParagraphStyle('Code2', parent=styles['Code'], fontSize=8, leading=10,
                            backColor=colors.HexColor('#f4f4f4'), leftIndent=12)
bold_style = ParagraphStyle('Bold2', parent=body_style, fontSize=9)
bullet_style = ParagraphStyle('Bullet2', parent=body_style, leftIndent=20, bulletIndent=10,
                               spaceBefore=2, spaceAfter=2)

story = []
lines = text.split('\n')
i = 0
in_code_block = False
code_lines = []
in_table = False
table_rows = []


def flush_code():
    global code_lines
    if code_lines:
        story.append(Preformatted('\n'.join(code_lines), code_style))
        story.append(Spacer(1, 6))
        code_lines.clear()


def flush_table():
    global table_rows, in_table
    if table_rows and len(table_rows) > 1:
        col_widths = [doc.width / len(table_rows[0])] * len(table_rows[0])
        t = Table(table_rows, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1a2e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(t)
        story.append(Spacer(1, 8))
    table_rows = []
    in_table = False


def escape_md(text):
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'`([^`]+)`', r'<font face="Courier" size="8">\1</font>', text)
    return text


while i < len(lines):
    line = lines[i]

    if line.startswith('```'):
        if in_code_block:
            flush_code()
            in_code_block = False
        else:
            in_code_block = True
        i += 1
        continue

    if in_code_block:
        code_lines.append(line)
        i += 1
        continue

    if '|' in line and line.strip().startswith('|') and line.strip().endswith('|'):
        if not in_table:
            in_table = True
            table_rows = []
        cells = [c.strip() for c in line.strip().split('|')[1:-1]]
        if all(c.replace('-', '').replace(':', '').replace(' ', '') == '' for c in cells):
            i += 1
            continue
        cell_style = ParagraphStyle('Cell', parent=body_style, fontSize=8, leading=10, spaceAfter=0)
        table_rows.append([Paragraph(escape_md(c), cell_style) for c in cells])
        i += 1
        continue
    else:
        if in_table:
            flush_table()

    stripped = line.strip()

    if stripped == '':
        story.append(Spacer(1, 4))
        i += 1
        continue

    if stripped.startswith('# ') and not stripped.startswith('## '):
        flush_table()
        story.append(Paragraph(escape_md(stripped[2:]), title_style))
    elif stripped.startswith('## '):
        flush_table()
        story.append(Paragraph(escape_md(stripped[3:]), h1_style))
    elif stripped.startswith('### '):
        flush_table()
        story.append(Paragraph(escape_md(stripped[4:]), h2_style))
    elif stripped.startswith('---'):
        flush_table()
        story.append(Spacer(1, 2))
        story.append(Table([['']], colWidths=[doc.width], style=TableStyle([
            ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.HexColor('#cccccc')),
        ])))
        story.append(Spacer(1, 6))
    elif stripped.startswith('- '):
        flush_table()
        story.append(Paragraph(escape_md(stripped[2:]), bullet_style))
    elif stripped.startswith('**') and stripped.endswith('**'):
        flush_table()
        story.append(Paragraph(escape_md(stripped), bold_style))
    else:
        flush_table()
        story.append(Paragraph(escape_md(stripped), body_style))

    i += 1

flush_code()
flush_table()

doc.build(story)
print('PDF generated: reports/ollama_suggestions.pdf')
