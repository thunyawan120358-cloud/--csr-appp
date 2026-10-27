import streamlit as st
import sqlite3, json, io, os, datetime, uuid
from pathlib import Path
from PIL import Image as PILImage, ImageOps
import pandas as pd
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Image, Spacer, KeepTogether
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xml.sax.saxutils import escape
from streamlit_drawable_canvas import st_canvas

st.set_page_config(page_title='สำรวจความเสียหายบ้านข้างเคียง',page_icon='🏠',layout='wide')
ROOT=Path(__file__).parent
DATA=Path(os.environ.get('INSPECTION_DATA_DIR',str(ROOT/'data')))
DATA.mkdir(parents=True,exist_ok=True)
(DB,UPLOADS)=(DATA/'inspection.db',DATA/'uploads')
UPLOADS.mkdir(exist_ok=True)
FONT=ROOT/'NotoSansThai-Regular.ttf'
if not FONT.exists():
    st.error('ไม่พบไฟล์ NotoSansThai-Regular.ttf ในโฟลเดอร์เดียวกับ app.py'); st.stop()
pdfmetrics.registerFont(TTFont('Thai',str(FONT)))

@st.cache_resource
def database():
    c=sqlite3.connect(DB,check_same_thread=False,timeout=30)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS inspections (id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, owner TEXT, house_no TEXT, project TEXT, survey_date TEXT, items TEXT, rep_name TEXT, owner_sign_name TEXT, rep_sign TEXT, owner_sign TEXT)')
    c.commit()
    return c
conn=database()

def all_records():
    conn.row_factory=sqlite3.Row
    return [dict(r) for r in conn.execute('SELECT * FROM inspections ORDER BY created_at ASC').fetchall()]
def fetch(record_id):
    return next((r for r in all_records() if r['id']==record_id),None)
def save_file(file, prefix):
    if file is None:return ''
    if isinstance(file,str):return file
    data=file.getvalue()
    if len(data)>12*1024*1024:raise ValueError('รูปภาพต้องไม่เกิน 12 MB')
    im=PILImage.open(io.BytesIO(data));im=ImageOps.exif_transpose(im).convert('RGB')
    im.thumbnail((1600,1600))
    path=UPLOADS/f'{prefix}_{uuid.uuid4().hex}.jpg'; im.save(path,quality=82,optimize=True)
    return str(path)
def save_sign(canvas, prefix, existing=''):
    if canvas is None or canvas.image_data is None:return existing
    import numpy as np
    arr=canvas.image_data.astype('uint8')
    if arr[:,:,:3].min()>=245:return existing
    im=PILImage.fromarray(arr,'RGBA').convert('RGB')
    path=UPLOADS/f'{prefix}_{uuid.uuid4().hex}.png';im.save(path)
    return str(path)
def fresh():
    return {'id':uuid.uuid4().hex,'owner':'','house_no':'','project':'','survey_date':str(datetime.date.today()),'items':[{'before':'','damage':'','after':'','repair':'','status':'รอดำเนินการ'}], 'rep_name':'','owner_sign_name':'','rep_sign':'','owner_sign':''}
def load_form(r):
    obj={**r,'items':json.loads(r['items'])} if r else fresh()
    st.session_state.form_record=obj
    st.session_state.form_version=st.session_state.get('form_version',0)+1

def make_pdf(r):
    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=(29.7*cm,21*cm),rightMargin=1*cm,leftMargin=1*cm,topMargin=1.2*cm,bottomMargin=1.2*cm)
    normal=ParagraphStyle('thai',fontName='Thai',fontSize=10,leading=16,wordWrap='CJK')
    small=ParagraphStyle('small',parent=normal,fontSize=8,leading=12)
    title=ParagraphStyle('title',parent=normal,fontSize=16,leading=22,alignment=1)
    def p(s,style=normal):return Paragraph(escape(str(s or '-')).replace('\n','<br/>'),style)
    def pic(path):
        if path and Path(path).is_file():
            try:
                im=PILImage.open(path);w,h=im.size
                ratio=min(5*cm/w,6*cm/h)
                return Image(path,width=w*ratio,height=h*ratio)
            except Exception:pass
        return p('ยังไม่มีรูปภาพ',small)
    story=[p('รายการความเสียหายบ้านข้างเคียง',title),Spacer(1,.3*cm),p('1. รายละเอียดบ้าน'),p(f"ชื่อเจ้าของบ้าน: {r['owner']}     บ้านเลขที่: {r['house_no']}"),p(f"ชื่อโครงการ: {r['project']}     วันที่สำรวจ: {r['survey_date']}"),Spacer(1,.4*cm),p('2. รายละเอียดความเสียหาย'),Spacer(1,.2*cm)]
    header=[p(x,small) for x in ['ที่','รูปภาพก่อนแก้ไข','รายละเอียดก่อนแก้ไข','รูปภาพหลังแก้ไข','รายละเอียดการแก้ไข','สถานะ']]
    for index,item in enumerate(json.loads(r['items']),1):
        cells=[p(index,small),pic(item.get('before')),p(item.get('damage'),small),pic(item.get('after')),p(item.get('repair'),small),p(item.get('status'),small)]
        table=Table([header,cells],colWidths=[1*cm,5.2*cm,6.5*cm,5.2*cm,6.5*cm,3.1*cm],repeatRows=1,hAlign='CENTER')
        table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.HexColor('#cbd5e1')),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e2e8f0')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(0,0),(0,-1),'CENTER'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        story.extend([table,Spacer(1,.25*cm)])
    def signature(path,name,role):
        image=Image(path,width=5*cm,height=1.6*cm) if path and Path(path).is_file() else p('................................................')
        cell=Table([[image],[p(name or '................................',small)],[p(role,small)]],colWidths=[8*cm])
        cell.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
        return cell
    story.append(Spacer(1,.4*cm))
    signatures=Table([[signature(r.get('rep_sign'),r.get('rep_name'),'ตัวแทนโครงการ'),signature(r.get('owner_sign'),r.get('owner_sign_name'),'เจ้าของบ้าน')]],colWidths=[13.6*cm,13.6*cm])
    story.append(KeepTogether(signatures))
    def footer(canvas,doc):
        canvas.setFont('Thai',8);canvas.drawRightString(28.5*cm,.6*cm,f'หน้า {doc.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
