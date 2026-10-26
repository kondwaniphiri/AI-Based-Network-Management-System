"""Build and render the source-based requirements and SDLC report."""
from pathlib import Path
import html
import json
import re
import tempfile
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,Table,TableStyle,Preformatted,KeepTogether
from reportlab.graphics.shapes import Drawing,Rect,Line,String,Polygon
import pymupdf

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'reports/Software_Process_Report.md'
OUTPUT=ROOT/'output/pdf/Software_Process_Report.pdf'
OUTPUT.parent.mkdir(parents=True,exist_ok=True)
QA=Path(tempfile.mkdtemp(prefix='iot_report_qa_'))
PAGE_W,PAGE_H=A4
W=PAGE_W-96
INK=colors.HexColor('#182522')
MUTED=colors.HexColor('#5b6662')
LINE=colors.HexColor('#d5dcd8')
PALE=colors.HexColor('#f1f4f1')
styles={
 'body':ParagraphStyle('body',fontName='Times-Roman',fontSize=10.4,leading=14.1,spaceAfter=7,allowWidows=0,allowOrphans=0),
 'h1':ParagraphStyle('h1',fontName='Helvetica-Bold',fontSize=17,leading=21,spaceAfter=15,textColor=colors.black),
 'h2':ParagraphStyle('h2',fontName='Helvetica-Bold',fontSize=11.1,leading=14,spaceBefore=10,spaceAfter=6,textColor=colors.black),
 'cell':ParagraphStyle('cell',fontName='Helvetica',fontSize=8.5,leading=11.2,spaceAfter=0,splitLongWords=1),
 'th':ParagraphStyle('th',fontName='Helvetica-Bold',fontSize=8.5,leading=11.2,textColor=colors.white),
 'bullet':ParagraphStyle('bullet',fontName='Times-Roman',fontSize=10.4,leading=14.1,leftIndent=12,firstLineIndent=0,bulletIndent=0,spaceAfter=5),
 'code':ParagraphStyle('code',fontName='Courier',fontSize=8.1,leading=11,backColor=PALE,borderPadding=9,spaceBefore=6,spaceAfter=12),
 'url':ParagraphStyle('url',fontName='Helvetica',fontSize=8.3,leading=11,textColor=INK,spaceAfter=8,splitLongWords=1),
 'title':ParagraphStyle('title',fontName='Helvetica-Bold',fontSize=27,leading=32,spaceAfter=20,textColor=colors.black),
 'subtitle':ParagraphStyle('subtitle',fontName='Helvetica',fontSize=14,leading=20,spaceAfter=20,textColor=colors.black),
 'small':ParagraphStyle('small',fontName='Helvetica',fontSize=9,leading=13,textColor=MUTED,spaceAfter=8),
}

def markup(text):
    text=html.escape(text)
    text=re.sub(r'`([^`]+)`',r'<font name="Courier">\1</font>',text)
    text=re.sub(r'\*\*([^*]+)\*\*',r'<b>\1</b>',text)
    return text

def para(text,kind='body'):
    return Paragraph(markup(text),styles[kind])

def box(d,x,y,w,h,title,lines):
    d.add(Rect(x,y,w,h,fillColor=PALE,strokeColor=colors.HexColor('#9eaaa3'),strokeWidth=.7))
    d.add(String(x+w/2,y+h-16,title,fontName='Helvetica-Bold',fontSize=9,textAnchor='middle',fillColor=colors.black))
    for k,line in enumerate(lines):
        d.add(String(x+w/2,y+h-30-k*11,line,fontName='Helvetica',fontSize=8,textAnchor='middle',fillColor=MUTED))

def architecture():
    d=Drawing(W,240)
    box(d,174,191,151,45,'Existing MiFi',['Upstream 192.168.8.0/24'])
    box(d,0,112,205,58,'Server PC',['192.168.8.100','Flask and collectors'])
    box(d,W-205,112,205,58,'ESP32 gateway',['STA example 192.168.8.110','AP 192.168.50.1'])
    for x in [102.5,W-102.5]:
        d.add(Line(x,170,249,191,strokeColor=MUTED,strokeWidth=.8))
    d.add(Line(W-102.5,112,W-102.5,87,strokeColor=MUTED,strokeWidth=.8))
    d.add(Line(80,87,W-80,87,strokeColor=MUTED,strokeWidth=.8))
    d.add(String(W/2,96,'Routed downstream network',fontName='Helvetica',fontSize=8,textAnchor='middle',fillColor=MUTED))
    for i,x in enumerate([12,183,W-145]):
        d.add(Line(x+66,87,x+66,70,strokeColor=MUTED,strokeWidth=.8))
        box(d,x,20,133,50,f'Sensor {i+1}',[f'192.168.50.{101+i}',['HTTP plus UDP','HTTP only','UDP only'][i]])
    d.add(String(W/2,5,'Phone clients use DHCP .10 to .30 on the hotspot',fontName='Helvetica',fontSize=8,textAnchor='middle',fillColor=MUTED))
    return d

def lifecycle():
    d=Drawing(W,68)
    labels=[['Planning'],['Requirements'],['Analysis','and design'],['Implementation'],['Verification'],['Deployment'],['Maintenance']]
    width=(W-6*9)/7
    for i,lines in enumerate(labels):
        x=i*(width+9)
        d.add(Rect(x,22,width,40,fillColor=PALE,strokeColor=LINE,strokeWidth=.8))
        for j,line in enumerate(lines):
            d.add(String(x+width/2,46-j*10,line,fontName='Helvetica',fontSize=8,textAnchor='middle'))
        if i<6:
            d.add(Line(x+width,42,x+width+9,42,strokeColor=MUTED,strokeWidth=.8))
    d.add(String(W/2,7,'Iterate after verification or operational feedback',fontName='Helvetica',fontSize=8,textAnchor='middle',fillColor=MUTED))
    return d

def table(rows):
    headers=rows[0]
    n=len(headers)
    if n==2: widths=[W*.30,W*.70]
    elif n==4: widths=[35,158,216,W-409]
    elif headers[0]=='ID': widths=[48,210,W-258] if rows[1][0].startswith('NFR') else [38,220,W-258]
    elif headers[0]=='Field': widths=[100,85,W-185]
    elif headers[0]=='Object': widths=[145,162,W-307]
    elif headers[0]=='Module': widths=[178,W-178]
    elif headers[0]=='Phase': widths=[88,275,W-363]
    elif headers[0]=='Area': widths=[125,244,W-369]
    elif headers[0]=='Category': widths=[150,77,W-227]
    elif headers[0]=='Endpoint': widths=[110,166,W-276]
    elif headers[0]=='Risk': widths=[109,146,W-255]
    else: widths=[119,172,W-291]
    data=[[para(cell,'th' if i==0 else 'cell') for cell in row] for i,row in enumerate(rows)]
    t=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT',splitByRow=1)
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),INK),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,PALE]),
        ('LINEBELOW',(0,0),(-1,0),.6,INK),('LINEBELOW',(0,1),(-1,-1),.3,LINE),
    ]))
    return [t,Spacer(1,10)]

pages=SOURCE.read_text(encoding='utf-8-sig').split('<!-- page -->')
story=[]
cover=[line.strip() for line in pages[0].splitlines() if line.strip()]
story += [Spacer(1,80),para('SOFTWARE ENGINEERING REPORT','small'),Spacer(1,15),para(cover[0].lstrip('# '),'title'),para(cover[1],'subtitle'),Spacer(1,16),para(cover[2]+'  |  '+cover[3],'small'),para(cover[4],'small'),Spacer(1,77),para(cover[5]),Spacer(1,14),para(cover[6]),PageBreak()]
for page_index,page in enumerate(pages[1:],start=1):
    lines=page.strip().splitlines()
    i=0
    while i<len(lines):
        line=lines[i].strip()
        if not line: i+=1;continue
        if line.startswith('```'):
            code=[];i+=1
            while i<len(lines) and not lines[i].strip().startswith('```'):
                code.append(lines[i]);i+=1
            story.append(Preformatted('\n'.join(code),styles['code']));i+=1;continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].strip().startswith('|'):
                row=[cell.strip() for cell in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?',cell) for cell in row):rows.append(row)
                i+=1
            story.extend(table(rows));continue
        if line=='[[architecture]]':story += [architecture(),Spacer(1,12)];i+=1;continue
        if line=='[[lifecycle]]':story += [lifecycle(),Spacer(1,10)];i+=1;continue
        if line.startswith('# '):story.append(para(line[2:],'h1'));i+=1;continue
        if line.startswith('## '):story.append(para(line[3:],'h2'));i+=1;continue
        if line.startswith('- '):
            story.append(Paragraph(markup(line[2:]),styles['bullet'],bulletText='-'));i+=1;continue
        if re.match(r'^\d+\. ',line):story.append(para(line));i+=1;continue
        if line.startswith('http'):
            story.append(Paragraph('<link href="'+html.escape(line,quote=True)+'">'+html.escape(line)+'</link>',styles['url']));i+=1;continue
        paragraph=[line];i+=1
        while i<len(lines) and lines[i].strip() and not lines[i].strip().startswith(('#','|','```','[[','http')):
            paragraph.append(lines[i].strip());i+=1
        story.append(para(' '.join(paragraph)))
    if page_index<len(pages)-1:story.append(PageBreak())

def decorate(canvas,doc):
    canvas.saveState()
    if doc.page>1:
        canvas.setFont('Helvetica',7.7);canvas.setFillColor(MUTED)
        canvas.drawString(48,PAGE_H-29,'AI-Based Network Management System')
        canvas.drawRightString(PAGE_W-48,PAGE_H-29,'Requirements and SDLC')
        canvas.setStrokeColor(LINE);canvas.setLineWidth(.5);canvas.line(48,PAGE_H-36,PAGE_W-48,PAGE_H-36)
    canvas.setFont('Helvetica',7.5);canvas.setFillColor(MUTED)
    canvas.drawString(48,24,'26 October 2026  |  Implemented prototype with hardware validation pending')
    canvas.drawRightString(PAGE_W-48,24,str(doc.page))
    canvas.restoreState()

SimpleDocTemplate(str(OUTPUT),pagesize=A4,rightMargin=48,leftMargin=48,topMargin=52,bottomMargin=43,
    title='AI-Based Network Management System Software Process Report',
    author='Project documentation',subject='Requirements architecture SDLC and verification').build(story,onFirstPage=decorate,onLaterPages=decorate)
pdf=pymupdf.open(OUTPUT)
page_meta=[]
for index,page in enumerate(pdf):
    image=QA/f'page-{index+1:02d}.png'
    page.get_pixmap(matrix=pymupdf.Matrix(1.5,1.5),alpha=False).save(image)
    text=page.get_text()
    page_meta.append({'page':index+1,'characters':len(text),'first_line':text.splitlines()[0] if text else ''})
report={'pdf':str(OUTPUT),'pages':len(pdf),'planned_pages':len(pages),'qa_images':str(QA),'page_text':page_meta,
        'source_words':len(SOURCE.read_text(encoding='utf-8-sig').split()),'date':'2026-10-26'}
(ROOT/'reports/report_quality_check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
