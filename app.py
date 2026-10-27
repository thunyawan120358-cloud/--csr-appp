app.py

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
    return buf.getvalue()

st.title('🏠 ระบบสำรวจความเสียหายบ้านข้างเคียง')
st.caption('บันทึกภาพก่อน–หลังแก้ไข • ติดตามสถานะ • รายงาน PDF ภาษาไทย')
MENU_OPTIONS = [
    '📝 แบบฟอร์มสำรวจ',
    '📂 ประวัติรายการ',
    '📊 แดชบอร์ด'
]

if 'pending_menu' in st.session_state:
    st.session_state.current_menu = st.session_state.pop('pending_menu')
elif 'current_menu' not in st.session_state:
    st.session_state.current_menu = MENU_OPTIONS[0]

tab = st.radio(
    'เมนูหลัก',
    MENU_OPTIONS,
    key='current_menu',
    horizontal=True
)
records=all_records()
statuses=['รอดำเนินการ','กำลังดำเนินการ','แก้ไขแล้ว','ไม่ต้องแก้ไข']
if tab.startswith('📝'):
    if 'form_record' not in st.session_state:load_form(None)
    r=st.session_state.form_record
    v=st.session_state.form_version
    if st.button('➕ เริ่มชุดข้อมูลใหม่',key='new_top'):
        load_form(None);st.rerun()
    st.subheader('1. รายละเอียดบ้าน')
    a,b=st.columns(2)
    with a:
        r['owner']=st.text_input('ชื่อเจ้าของบ้าน *',value=r['owner'],key=f'owner_{v}')
        r['house_no']=st.text_input('บ้านเลขที่ *',value=r['house_no'],key=f'house_{v}')
    with b:
        r['project']=st.text_input('ชื่อโครงการ *',value=r['project'],key=f'project_{v}')
        r['survey_date']=str(st.date_input('วันที่สำรวจ',value=datetime.date.fromisoformat(r['survey_date']),key=f'date_{v}'))
    st.subheader('2. รายการความเสียหาย')
    st.caption(f"จำนวน {len(r['items'])}/100 รายการ — สามารถเพิ่มภาพหลังแก้ไขในภายหลัง")
    for i,item in enumerate(r['items']):
        with st.container(border=True):
            st.markdown(f"### รายการความเสียหาย #{i+1}")
            c1,c2=st.columns(2)
            with c1:
                st.markdown('**ภาพก่อนแก้ไข**')
                before=st.file_uploader('แนบภาพความเสียหาย',type=['png','jpg','jpeg','webp'],key=f'before_{v}_{i}')
                if before is not None:item['before']=save_file(before,f'before_{r["id"]}_{i}')
                if item.get('before') and Path(item['before']).is_file():st.image(item['before'],width=240)
            with c2:
                st.markdown('**ภาพหลังแก้ไข**')
                after=st.file_uploader('แนบภาพหลังแก้ไข (เพิ่มภายหลังได้)',type=['png','jpg','jpeg','webp'],key=f'after_{v}_{i}')
                if after is not None:item['after']=save_file(after,f'after_{r["id"]}_{i}')
                if item.get('after') and Path(item['after']).is_file():st.image(item['after'],width=240)
            d1,d2=st.columns(2)
            with d1:
                item['damage']=st.text_area('รายละเอียดความเสียหาย (ก่อนแก้ไข)',value=item.get('damage',''),height=120,key=f'damage_{v}_{i}',placeholder='ระบุความเสียหายที่พบ...')
            with d2:
                item['repair']=st.text_area('รายละเอียดการแก้ไข (หลังแก้ไข)',value=item.get('repair',''),height=120,key=f'repair_{v}_{i}',placeholder='ระบุวิธีการแก้ไข หรือผลการซ่อม...')
            item['status']=st.selectbox('สถานะการแก้ไข',statuses,index=statuses.index(item.get('status','รอดำเนินการ')),key=f'status_{v}_{i}')
            if st.button('🗑️ ลบรายการนี้',key=f'del_{v}_{i}',disabled=len(r['items'])==1):
                r['items'].pop(i);st.session_state.form_version+=1;st.rerun()
    st.caption(f"เพิ่มแล้ว {len(r['items'])} จาก 100 รายการ")
    if st.button('➕ เพิ่มรายละเอียดความเสียหายข้อถัดไป',disabled=len(r['items'])>=100,use_container_width=True,type='secondary'):
        r['items'].append({'before':'','damage':'','after':'','repair':'','status':'รอดำเนินการ'})
        st.session_state.form_version+=1;st.rerun()
    if len(r['items'])>=100:st.info('ครบจำนวนสูงสุด 100 รายการแล้ว')
    st.subheader('3. ลงนามรับรอง')
    s1,s2=st.columns(2)
    with s1:
        st.markdown('**ลายเซ็นตัวแทนโครงการ**')
        rep_canvas=st_canvas(stroke_width=2,stroke_color='#111827',background_color='#FFFFFF',height=130,width=320,drawing_mode='freedraw',key=f'rep_canvas_{v}')
        r['rep_name']=st.text_input('ชื่อผู้ลงนาม (ตัวแทนโครงการ)',value=r.get('rep_name',''),key=f'rep_name_{v}')
    with s2:
        st.markdown('**ลายเซ็นเจ้าของบ้าน**')
        own_canvas=st_canvas(stroke_width=2,stroke_color='#111827',background_color='#FFFFFF',height=130,width=320,drawing_mode='freedraw',key=f'own_canvas_{v}')
        r['owner_sign_name']=st.text_input('ชื่อผู้ลงนาม (เจ้าของบ้าน)',value=r.get('owner_sign_name',''),key=f'own_name_{v}')
    if r.get('rep_sign'):st.caption('มีลายเซ็นตัวแทนโครงการที่บันทึกไว้แล้ว')
    if r.get('owner_sign'):st.caption('มีลายเซ็นเจ้าของบ้านที่บันทึกไว้แล้ว')
    if st.button('💾 บันทึกข้อมูล',type='primary',use_container_width=True):
        if not all([r['owner'].strip(),r['house_no'].strip(),r['project'].strip()]):st.error('กรุณากรอกชื่อเจ้าของบ้าน บ้านเลขที่ และชื่อโครงการ')
        elif not any(x.get('damage','').strip() for x in r['items']):st.error('กรุณากรอกรายละเอียดความเสียหายอย่างน้อย 1 รายการ')
        else:
            r['rep_sign']=save_sign(rep_canvas,f'rep_{r["id"]}',r.get('rep_sign',''))
            r['owner_sign']=save_sign(own_canvas,f'owner_{r["id"]}',r.get('owner_sign',''))
            old=fetch(r['id']);now=datetime.datetime.now().isoformat(timespec='seconds')
            conn.execute('INSERT OR REPLACE INTO inspections VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(r['id'],old['created_at'] if old else now,now,r['owner'],r['house_no'],r['project'],r['survey_date'],json.dumps(r['items'],ensure_ascii=False),r['rep_name'],r['owner_sign_name'],r['rep_sign'],r['owner_sign']))
            conn.commit();st.session_state.last_saved=r['id'];load_form(None);st.success('บันทึกสำเร็จ เปิดดู PDF ได้ในหน้าประวัติ และเริ่มกรอกชุดใหม่ได้ทันที')
    if st.session_state.get('last_saved'):
        last=fetch(st.session_state.last_saved)
        if last:st.download_button('📄 ดาวน์โหลด PDF ของรายการที่บันทึกล่าสุด',make_pdf(last),file_name=f'inspection_{last["house_no"]}.pdf',mime='application/pdf')
elif tab.startswith('📂'):
    st.subheader('ประวัติการสำรวจ')
    search=st.text_input('ค้นหาชื่อเจ้าของบ้าน บ้านเลขที่ หรือโครงการ')
    shown=[r for r in records if search.lower() in (r['owner']+' '+r['house_no']+' '+r['project']).lower()]
    if not shown:st.info('ยังไม่มีข้อมูลที่ตรงกับการค้นหา')
    for number,r in enumerate(shown,1):
        items=json.loads(r['items']);done=sum(x['status'] in ['แก้ไขแล้ว','ไม่ต้องแก้ไข'] for x in items)
        with st.container(border=True):
            st.markdown(f"**{number}. บ้านเลขที่ {r['house_no']} — {r['owner']}**")
            st.caption(f"โครงการ: {r['project']} | สำรวจ: {r['survey_date']} | อัปเดต: {r['updated_at']} | เสร็จ {done}/{len(items)}")
            c1,c2=st.columns(2)
            with c1:
                if st.button('✏️ เปิดแก้ไข/เพิ่มภาพหลังแก้ไข', key='edit_'+str(r['id'])):
                    load_form(r)
                    st.session_state.pending_menu = MENU_OPTIONS[0]
                    st.rerun()
            with c2:
                st.download_button(
                    '⬇️ ดาวน์โหลด PDF ล่าสุด',
                    data=make_pdf(r),
                    file_name=f"inspection_{r['id']}.pdf",
                    mime='application/pdf',
                    key='pdf_'+str(r['id'])
                )
    st.info('กดเปิดแก้ไขเพื่อกลับไปยังแบบฟอร์มพร้อมข้อมูลเดิม จากนั้นแก้ไขและกดบันทึกข้อมูล')
else:
    st.subheader('แดชบอร์ดภาพรวม')
    all_items=[i for r in records for i in json.loads(r['items'])]
    total=len(all_items);done=sum(x['status'] in ['แก้ไขแล้ว','ไม่ต้องแก้ไข'] for x in all_items)
    a,b,c,d=st.columns(4)
    a.metric('บ้านที่สำรวจ',len(records));b.metric('รายการความเสียหาย',total);c.metric('ดำเนินการเสร็จ',done);d.metric('ความคืบหน้า',f'{done/total*100:.1f}%' if total else '0%')
    st.progress(done/total if total else 0)
    counts=pd.DataFrame({'สถานะ':statuses,'จำนวน':[sum(x['status']==s for x in all_items) for s in statuses]}).set_index('สถานะ')
    st.bar_chart(counts)
    if records:
        st.subheader('สรุปรายโครงการ')
        data=[]
        for project in sorted({r['project'] for r in records}):
            rs=[r for r in records if r['project']==project];it=[i for r in rs for i in json.loads(r['items'])]
            data.append({'โครงการ':project,'บ้าน':len(rs),'รายการ':len(it),'เสร็จแล้ว':sum(x['status'] in ['แก้ไขแล้ว','ไม่ต้องแก้ไข'] for x in it)})
        st.dataframe(pd.DataFrame(data),hide_index=True,use_container_width=True)
