import streamlit as st
import sqlite3, json, io, os, datetime, uuid, zipfile
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
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

def database_url():
    try:
        return st.secrets.get('DATABASE_URL', os.environ.get('DATABASE_URL', ''))
    except Exception:
        return os.environ.get('DATABASE_URL', '')

if not database_url():
    st.error('กรุณาตั้งค่า DATABASE_URL ใน Streamlit Cloud → App settings → Secrets ก่อนใช้งาน')
    st.stop()

@st.cache_resource

def database():
    connection = psycopg2.connect(database_url(), connect_timeout=15, sslmode='require')
    connection.autocommit = True
    return connection

def db_run(sql, params=(), one=False, many=False):
    global conn
    for attempt in range(2):
        try:
            if conn.closed:
                conn = psycopg2.connect(database_url(), sslmode='require')
                conn.autocommit = True
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                if one:
                    return dict(cur.fetchone()) if cur.rowcount else None
                if many:
                    return [dict(row) for row in cur.fetchall()]
                return None
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            if attempt:
                raise
            try:
                database.clear()
            except Exception:
                pass
            conn = database()

try:
    conn = database()
    db_run('''CREATE TABLE IF NOT EXISTS inspections (
        id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, owner TEXT,
        house_no TEXT, project TEXT, survey_date TEXT, items TEXT,
        rep_name TEXT, owner_sign_name TEXT, rep_sign TEXT, owner_sign TEXT)''')
    db_run('''CREATE TABLE IF NOT EXISTS media (
        id TEXT PRIMARY KEY, content BYTEA NOT NULL, content_type TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW())''')
    db_run('''CREATE TABLE IF NOT EXISTS audit_log (
        id BIGSERIAL PRIMARY KEY, record_id TEXT NOT NULL, changed_at TIMESTAMPTZ DEFAULT NOW(),
        event TEXT NOT NULL, previous_data TEXT, new_data TEXT)''')
except Exception as exc:
    st.error('เชื่อมต่อ Neon ไม่สำเร็จ กรุณาตรวจสอบ DATABASE_URL ใน Secrets')
    st.code(str(exc).split('password=')[0][:350])
    st.stop()

def all_records():
    return db_run('SELECT * FROM inspections ORDER BY created_at ASC', many=True)

def fetch(record_id):
    return db_run('SELECT * FROM inspections WHERE id=%s', (record_id,), one=True)

def media_bytes(path):
    if not path:
        return None
    if path.startswith('asset:'):
        row = db_run('SELECT content FROM media WHERE id=%s', (path[6:],), one=True)
        return bytes(row['content']) if row else None
    if Path(path).is_file():
        return Path(path).read_bytes()
    return None

def put_media(data, content_type):
    media_id = uuid.uuid4().hex
    db_run('INSERT INTO media (id, content, content_type) VALUES (%s,%s,%s)',
           (media_id, psycopg2.Binary(data), content_type))
    return 'asset:' + media_id

def save_file(file, prefix):
    if file is None:
        return ''
    if isinstance(file, str):
        return file
    data = file.getvalue()
    if len(data) > 12 * 1024 * 1024:
        raise ValueError('รูปภาพต้องไม่เกิน 12 MB')
    im = ImageOps.exif_transpose(PILImage.open(io.BytesIO(data))).convert('RGB')
    im.thumbnail((1600, 1600))
    out = io.BytesIO()
    im.save(out, format='JPEG', quality=82, optimize=True)
    return put_media(out.getvalue(), 'image/jpeg')

def save_sign(canvas, prefix, existing=''):
    if canvas is None or canvas.image_data is None:
        return existing
    arr = canvas.image_data.astype('uint8')
    if arr[:, :, :3].min() >= 245:
        return existing
    im = PILImage.fromarray(arr, 'RGBA').convert('RGB')
    out = io.BytesIO()
    im.save(out, format='PNG')
    return put_media(out.getvalue(), 'image/png')

def save_record(r):
    old = fetch(r['id'])
    now = datetime.datetime.now().isoformat(timespec='seconds')
    columns = ('id','created_at','updated_at','owner','house_no','project','survey_date',
               'items','rep_name','owner_sign_name','rep_sign','owner_sign')
    values = (r['id'], old['created_at'] if old else now, now, r['owner'], r['house_no'],
              r['project'], r['survey_date'], json.dumps(r['items'], ensure_ascii=False),
              r['rep_name'], r['owner_sign_name'], r['rep_sign'], r['owner_sign'])
    db_run('''INSERT INTO inspections (id,created_at,updated_at,owner,house_no,project,
             survey_date,items,rep_name,owner_sign_name,rep_sign,owner_sign)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
             ON CONFLICT (id) DO UPDATE SET
             updated_at=EXCLUDED.updated_at,owner=EXCLUDED.owner,house_no=EXCLUDED.house_no,
             project=EXCLUDED.project,survey_date=EXCLUDED.survey_date,items=EXCLUDED.items,
             rep_name=EXCLUDED.rep_name,owner_sign_name=EXCLUDED.owner_sign_name,
             rep_sign=EXCLUDED.rep_sign,owner_sign=EXCLUDED.owner_sign''', values)
    db_run('INSERT INTO audit_log (record_id,event,previous_data,new_data) VALUES (%s,%s,%s,%s)',
           (r['id'], 'แก้ไข' if old else 'สร้างใหม่',
            json.dumps(old,ensure_ascii=False) if old else None,
            json.dumps(dict(zip(columns,values)),ensure_ascii=False)))

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
        if path and media_bytes(path):
            try:
                raw=media_bytes(path);im=PILImage.open(io.BytesIO(raw));w,h=im.size
                ratio=min(5*cm/w,6*cm/h)
                return Image(io.BytesIO(raw),width=w*ratio,height=h*ratio)
            except Exception:pass
        return p('ยังไม่มีรูปภาพ',small)
    # แสดงข้อมูลแต่ละช่องแยกกัน เพื่อป้องกันข้อความยาวเบียดวันที่/บ้านเลขที่
    raw_date = str(r.get('survey_date') or '').strip()
    try:
        survey_display = datetime.date.fromisoformat(raw_date[:10]).strftime('%d/%m/%Y')
    except (ValueError, TypeError):
        survey_display = 'ไม่ระบุ'
    detail_rows = [
        [p('ชื่อเจ้าของบ้าน',small), p(r.get('owner') or 'ไม่ระบุ'),
         p('บ้านเลขที่',small), p(r.get('house_no') or 'ไม่ระบุ')],
        [p('ชื่อโครงการ',small), p(r.get('project') or 'ไม่ระบุ'),
         p('วันที่สำรวจ',small), p(survey_display)],
    ]
    detail_table = Table(detail_rows, colWidths=[2.6*cm, 10.3*cm, 2.4*cm, 12.0*cm], hAlign='LEFT')
    detail_table.setStyle(TableStyle([
        ('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#cbd5e1')),
        ('BACKGROUND',(0,0),(0,-1),colors.HexColor('#e2e8f0')),
        ('BACKGROUND',(2,0),(2,-1),colors.HexColor('#e2e8f0')),
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),7),
        ('RIGHTPADDING',(0,0),(-1,-1),7),
        ('TOPPADDING',(0,0),(-1,-1),7),
        ('BOTTOMPADDING',(0,0),(-1,-1),7),
    ]))
    story=[p('รายการความเสียหายบ้านข้างเคียง',title),Spacer(1,.3*cm),
           p('1. รายละเอียดบ้าน'),Spacer(1,.15*cm),detail_table,
           Spacer(1,.4*cm),p('2. รายละเอียดความเสียหาย'),Spacer(1,.2*cm)]
    header=[p(x,small) for x in ['ที่','รูปภาพก่อนแก้ไข','รายละเอียดก่อนแก้ไข','รูปภาพหลังแก้ไข','รายละเอียดการแก้ไข','สถานะ']]
    for index,item in enumerate(json.loads(r['items']),1):
        cells=[p(index,small),pic(item.get('before')),p(item.get('damage'),small),pic(item.get('after')),p((item.get('repair') or '')+'\nผู้รับผิดชอบ: '+(item.get('assignee') or '-')+'\nกำหนด: '+(item.get('due_date') or '-'),small),p(item.get('status'),small)]
        table=Table([header,cells],colWidths=[1*cm,5.2*cm,6.5*cm,5.2*cm,6.5*cm,3.1*cm],repeatRows=1,hAlign='CENTER')
        table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.HexColor('#cbd5e1')),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e2e8f0')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(0,0),(0,-1),'CENTER'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        story.extend([table,Spacer(1,.25*cm)])
    def signature(path,name,role):
        image=Image(io.BytesIO(media_bytes(path)),width=5*cm,height=1.6*cm) if path and media_bytes(path) else p('................................................')
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
                if before is not None:
                    digest=hashlib.sha256(before.getvalue()).hexdigest()
                    if item.get('_before_digest')!=digest:
                        item['before']=save_file(before,f'before_{r["id"]}_{i}')
                        item['_before_digest']=digest
                if item.get('before') and media_bytes(item['before']):st.image(media_bytes(item['before']),width=240)
            with c2:
                st.markdown('**ภาพหลังแก้ไข**')
                after=st.file_uploader('แนบภาพหลังแก้ไข (เพิ่มภายหลังได้)',type=['png','jpg','jpeg','webp'],key=f'after_{v}_{i}')
                if after is not None:
                    digest=hashlib.sha256(after.getvalue()).hexdigest()
                    if item.get('_after_digest')!=digest:
                        item['after']=save_file(after,f'after_{r["id"]}_{i}')
                        item['_after_digest']=digest
                if item.get('after') and media_bytes(item['after']):st.image(media_bytes(item['after']),width=240)
            d1,d2=st.columns(2)
            with d1:
                item['damage']=st.text_area('รายละเอียดความเสียหาย (ก่อนแก้ไข)',value=item.get('damage',''),height=120,key=f'damage_{v}_{i}',placeholder='ระบุความเสียหายที่พบ...')
            with d2:
                item['repair']=st.text_area('รายละเอียดการแก้ไข (หลังแก้ไข)',value=item.get('repair',''),height=120,key=f'repair_{v}_{i}',placeholder='ระบุวิธีการแก้ไข หรือผลการซ่อม...')
            item['status']=st.selectbox('สถานะการแก้ไข',statuses,index=statuses.index(item.get('status','รอดำเนินการ')) if item.get('status','รอดำเนินการ') in statuses else 0,key=f'status_{v}_{i}')
            item['assignee']=st.text_input('ผู้รับผิดชอบการแก้ไข',value=item.get('assignee',''),key=f'assignee_{v}_{i}')
            item['due_date']=st.text_input('กำหนดแก้ไข (YYYY-MM-DD)',value=item.get('due_date',''),key=f'due_{v}_{i}',placeholder='2026-11-30')
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
            save_record(r);st.session_state.last_saved=r['id'];load_form(None);st.success('บันทึกสำเร็จ เปิดดู PDF ได้ในหน้าประวัติ และเริ่มกรอกชุดใหม่ได้ทันที')
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
    with st.expander('🕘 ประวัติการแก้ไข'):
        logs=db_run('SELECT record_id,changed_at,event FROM audit_log ORDER BY changed_at DESC LIMIT 200',many=True)
        st.dataframe(pd.DataFrame(logs),hide_index=True,use_container_width=True) if logs else st.info('ยังไม่มีประวัติ')
else:
    st.subheader('แดชบอร์ดภาพรวม')
    all_items=[i for r in records for i in json.loads(r['items'])]
    total=len(all_items);done=sum(x['status'] in ['แก้ไขแล้ว','ไม่ต้องแก้ไข'] for x in all_items)
    a,b,c,d=st.columns(4)
    a.metric('บ้านที่สำรวจ',len(records));b.metric('รายการความเสียหาย',total);c.metric('ดำเนินการเสร็จ',done);d.metric('ความคืบหน้า',f'{done/total*100:.1f}%' if total else '0%')
    st.progress(done/total if total else 0)
    counts=pd.DataFrame({'สถานะ':statuses,'จำนวน':[sum(x['status']==s for x in all_items) for s in statuses]}).set_index('สถานะ')
    st.bar_chart(counts)
    today=str(datetime.date.today())
    overdue=[(r,i) for r in records for i in json.loads(r['items'])
             if i.get('due_date') and i['due_date'] < today and i.get('status') not in ('แก้ไขแล้ว','ไม่ต้องแก้ไข')]
    st.metric('รายการเกินกำหนด',len(overdue))
    if overdue:
        st.dataframe(pd.DataFrame([{'บ้านเลขที่':r['house_no'],'โครงการ':r['project'],
             'รายละเอียด':i.get('damage',''),'ผู้รับผิดชอบ':i.get('assignee',''),
             'กำหนดแก้ไข':i.get('due_date','')} for r,i in overdue]),hide_index=True,use_container_width=True)
    if records:
        st.subheader('สรุปรายโครงการ')
        data=[]
        for project in sorted({r['project'] for r in records}):
            rs=[r for r in records if r['project']==project];it=[i for r in rs for i in json.loads(r['items'])]
            data.append({'โครงการ':project,'บ้าน':len(rs),'รายการ':len(it),'เสร็จแล้ว':sum(x['status'] in ['แก้ไขแล้ว','ไม่ต้องแก้ไข'] for x in it)})
        st.dataframe(pd.DataFrame(data),hide_index=True,use_container_width=True)
