"""Neighbor-house inspection — durable DB, audit trail, deadlines, comparisons and PDF.
Requires: streamlit, Pillow, pandas, reportlab, sqlalchemy, psycopg[binary], streamlit-drawable-canvas.
Set DATABASE_URL for persistent hosted PostgreSQL. Without it, local SQLite is NOT durable on Streamlit Cloud.
"""
import base64
import datetime as dt
import io
import json
import os
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
import streamlit as st
from PIL import Image as PILImage, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image as PDFImage, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)
from sqlalchemy import create_engine, inspect, text
from streamlit_drawable_canvas import st_canvas

st.set_page_config(page_title='สำรวจความเสียหายบ้านข้างเคียง', page_icon='🏠', layout='wide')
ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('INSPECTION_DATA_DIR', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
FONT = ROOT / 'NotoSansThai-Regular.ttf'
if not FONT.is_file():
    st.error('ไม่พบ NotoSansThai-Regular.ttf กรุณาเก็บไฟล์ฟอนต์เดิมไว้ข้าง app.py')
    st.stop()
pdfmetrics.registerFont(TTFont('Thai', str(FONT)))


def setting(name, default=''):
    try:
        return str(st.secrets.get(name, os.environ.get(name, default)))
    except Exception:
        return str(os.environ.get(name, default))


DATABASE_URL = setting('DATABASE_URL')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = 'postgresql://' + DATABASE_URL[len('postgres://'):]
if DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL = 'postgresql+psycopg://' + DATABASE_URL[len('postgresql://'):]
if not DATABASE_URL:
    DATABASE_URL = 'sqlite:///' + str(DATA / 'inspection.db')
REMOTE_DB = DATABASE_URL.startswith('postgresql+')
STATUS = ['รอดำเนินการ', 'กำลังดำเนินการ', 'แก้ไขแล้ว', 'ไม่ต้องแก้ไข']
PRIORITY = ['ปกติ', 'เร่งด่วน', 'วิกฤต']
MENU = ['📝 แบบฟอร์มสำรวจ', '📂 ประวัติรายการ', '📊 แดชบอร์ด', '🕘 ประวัติการแก้ไข', '💾 สำรองข้อมูล']


@st.cache_resource

def engine():
    e = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={'timeout': 30} if not REMOTE_DB else {})
    with e.begin() as c:
        c.execute(text('''CREATE TABLE IF NOT EXISTS inspections (
          id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, owner TEXT, house_no TEXT,
          project TEXT, survey_date TEXT, items TEXT, rep_name TEXT, owner_sign_name TEXT,
          rep_sign TEXT, owner_sign TEXT)'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS assets (
          id TEXT PRIMARY KEY, mime TEXT NOT NULL, data BYTEA NOT NULL)''' if REMOTE_DB else '''CREATE TABLE IF NOT EXISTS assets (
          id TEXT PRIMARY KEY, mime TEXT NOT NULL, data BLOB NOT NULL)'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS audit_log (
          id TEXT PRIMARY KEY, record_id TEXT NOT NULL, event_time TEXT NOT NULL,
          actor TEXT, action TEXT, summary TEXT, snapshot TEXT)'''))
    return e


try:
    DB = engine()
except Exception as exc:
    st.error('เชื่อมต่อฐานข้อมูลไม่สำเร็จ กรุณาตรวจสอบ DATABASE_URL ใน Secrets')
    st.exception(exc)
    st.stop()


def query(sql, params=None):
    with DB.connect() as c:
        return [dict(r) for r in c.execute(text(sql), params or {}).mappings()]


def all_records():
    return query('SELECT * FROM inspections ORDER BY created_at DESC')


def fetch(rid):
    rows = query('SELECT * FROM inspections WHERE id=:id', {'id': rid})
    return rows[0] if rows else None


def items_of(r):
    try:
        return json.loads(r['items']) if isinstance(r['items'], str) else r['items']
    except (ValueError, TypeError):
        return []


def asset_bytes(ref):
    if not ref:
        return None
    if str(ref).startswith('db:'):
        rows = query('SELECT data FROM assets WHERE id=:id', {'id': ref[3:]})
        return bytes(rows[0]['data']) if rows else None
    # Backwards compatibility with the existing app's local paths.
    try:
        path = Path(ref)
        return path.read_bytes() if path.is_file() else None
    except (OSError, ValueError):
        return None


def persist_image(file_obj, prefix='photo'):
    if file_obj is None:
        return ''
    raw = file_obj.getvalue() if hasattr(file_obj, 'getvalue') else file_obj
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError('รูปภาพต้องไม่เกิน 12 MB')
    with PILImage.open(io.BytesIO(raw)) as src:
        im = ImageOps.exif_transpose(src).convert('RGB')
        im.thumbnail((1600, 1600))
        out = io.BytesIO()
        im.save(out, 'JPEG', quality=83, optimize=True)
    aid = uuid.uuid4().hex
    with DB.begin() as c:
        c.execute(text('INSERT INTO assets(id,mime,data) VALUES (:id,:mime,:data)'),
                  {'id': aid, 'mime': 'image/jpeg', 'data': out.getvalue()})
    return 'db:' + aid


def persist_sign(canvas, old=''):
    if canvas is None or canvas.image_data is None:
        return old
    arr = canvas.image_data.astype('uint8')
    if arr[:, :, :3].min() >= 245:
        return old
    im = PILImage.fromarray(arr, 'RGBA').convert('RGB')
    out = io.BytesIO()
    im.save(out, 'PNG')
    aid = uuid.uuid4().hex
    with DB.begin() as c:
        c.execute(text('INSERT INTO assets(id,mime,data) VALUES (:id,:mime,:data)'),
                  {'id': aid, 'mime': 'image/png', 'data': out.getvalue()})
    return 'db:' + aid


def fresh_item():
    return {'before': '', 'damage': '', 'after': '', 'repair': '', 'status': STATUS[0],
            'responsible': '', 'due_date': '', 'priority': PRIORITY[0], 'completed_date': ''}


def fresh():
    return {'id': uuid.uuid4().hex, 'owner': '', 'house_no': '', 'project': '',
            'survey_date': dt.date.today().isoformat(), 'items': [fresh_item()],
            'rep_name': '', 'owner_sign_name': '', 'rep_sign': '', 'owner_sign': ''}


def load_form(record):
    obj = {**record, 'items': items_of(record)} if record else fresh()
    obj['items'] = [{**fresh_item(), **i} for i in obj['items']]
    st.session_state.form_record = obj
    st.session_state.form_version = st.session_state.get('form_version', 0) + 1


def change_summary(old, new):
    if old is None:
        return 'สร้างรายการสำรวจใหม่'
    changes = []
    for field, label in [('owner', 'เจ้าของบ้าน'), ('house_no', 'บ้านเลขที่'), ('project', 'โครงการ')]:
        if old.get(field) != new.get(field):
            changes.append(label)
    a, b = items_of(old), new['items']
    if len(a) != len(b):
        changes.append(f'จำนวนรายการ {len(a)} → {len(b)}')
    for index, item in enumerate(b):
        if index >= len(a):
            continue
        for field, label in [('status', 'สถานะ'), ('after', 'ภาพหลังแก้ไข'),
                             ('repair', 'รายละเอียดการแก้ไข'), ('due_date', 'กำหนดเสร็จ'),
                             ('responsible', 'ผู้รับผิดชอบ'), ('before', 'ภาพก่อนแก้ไข')]:
            if a[index].get(field, '') != item.get(field, ''):
                changes.append(f'รายการ {index + 1}: {label}')
    return ', '.join(changes) if changes else 'บันทึกข้อมูล (ไม่มีการเปลี่ยนแปลงที่ตรวจพบ)'


def save_record(r, actor):
    old = fetch(r['id'])
    now = dt.datetime.now().isoformat(timespec='seconds')
    values = {'id': r['id'], 'created_at': old['created_at'] if old else now,
              'updated_at': now, 'owner': r['owner'], 'house_no': r['house_no'],
              'project': r['project'], 'survey_date': r['survey_date'],
              'items': json.dumps(r['items'], ensure_ascii=False),
              'rep_name': r.get('rep_name', ''), 'owner_sign_name': r.get('owner_sign_name', ''),
              'rep_sign': r.get('rep_sign', ''), 'owner_sign': r.get('owner_sign', '')}
    summary = change_summary(old, r)
    with DB.begin() as c:
        c.execute(text('''INSERT INTO inspections
            (id,created_at,updated_at,owner,house_no,project,survey_date,items,rep_name,owner_sign_name,rep_sign,owner_sign)
            VALUES (:id,:created_at,:updated_at,:owner,:house_no,:project,:survey_date,:items,:rep_name,:owner_sign_name,:rep_sign,:owner_sign)
            ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,owner=excluded.owner,
            house_no=excluded.house_no,project=excluded.project,survey_date=excluded.survey_date,
            items=excluded.items,rep_name=excluded.rep_name,owner_sign_name=excluded.owner_sign_name,
            rep_sign=excluded.rep_sign,owner_sign=excluded.owner_sign'''), values)
        c.execute(text('''INSERT INTO audit_log (id,record_id,event_time,actor,action,summary,snapshot)
            VALUES (:id,:record_id,:event_time,:actor,:action,:summary,:snapshot)'''),
            {'id': uuid.uuid4().hex, 'record_id': r['id'], 'event_time': now,
             'actor': actor.strip() or 'ไม่ระบุ', 'action': 'แก้ไข' if old else 'สร้าง',
             'summary': summary, 'snapshot': json.dumps(values, ensure_ascii=False)})
    return values


def is_done(item):
    return item.get('status') in ('แก้ไขแล้ว', 'ไม่ต้องแก้ไข')


def is_overdue(item):
    return bool(item.get('due_date') and not is_done(item) and item['due_date'] < dt.date.today().isoformat())


def pdf_report(r):
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=(29.7 * cm, 21 * cm),
                            leftMargin=1.3 * cm, rightMargin=1.3 * cm,
                            topMargin=1.3 * cm, bottomMargin=1.4 * cm)
    body = ParagraphStyle('body', fontName='Thai', fontSize=9, leading=14, wordWrap='CJK')
    small = ParagraphStyle('small', parent=body, fontSize=8, leading=12)
    heading = ParagraphStyle('heading', parent=body, fontSize=16, leading=22,
                             alignment=TA_CENTER, textColor=colors.HexColor('#183153'))
    section = ParagraphStyle('section', parent=body, fontSize=11, leading=17,
                             textColor=colors.HexColor('#183153'))

    def p(value, style=body):
        return Paragraph(escape(str(value if value is not None else '-')).replace('\n', '<br/>'), style)

    def photo(ref, max_w=5.3 * cm, max_h=4.3 * cm):
        raw = asset_bytes(ref)
        if raw:
            try:
                image = PILImage.open(io.BytesIO(raw))
                w, h = image.size
                scale = min(max_w / w, max_h / h)
                return PDFImage(io.BytesIO(raw), width=w * scale, height=h * scale)
            except Exception:
                pass
        return p('ยังไม่มีภาพ', small)

    items = items_of(r)
    completed = sum(is_done(i) for i in items)
    overdue = sum(is_overdue(i) for i in items)
    story = [p('รายงานสำรวจความเสียหายบ้านข้างเคียง', heading), Spacer(1, .25 * cm)]
    story.append(p(f"เลขที่รายงาน: {r['id'][:12].upper()}   |   ออกรายงาน: {dt.datetime.now():%Y-%m-%d %H:%M}", small))
    story.append(Spacer(1, .15 * cm))
    story.append(p('1. ข้อมูลบ้านและโครงการ', section))
    meta = [[p('ชื่อเจ้าของบ้าน', small), p(r['owner']), p('บ้านเลขที่', small), p(r['house_no'])],
            [p('โครงการ', small), p(r['project']), p('วันที่สำรวจ', small), p(r['survey_date'])]]
    mt = Table(meta, colWidths=[3 * cm, 10 * cm, 3 * cm, 10 * cm])
    mt.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                            ('LINEBELOW', (0, 0), (-1, -1), .3, colors.HexColor('#dce4ee'))]))
    story.extend([mt, Spacer(1, .25 * cm), p('2. สรุปผลการสำรวจ', section)])
    summary = Table([[p(f'จำนวนรายการ: {len(items)}'), p(f'เสร็จแล้ว: {completed}'),
                      p(f'ค้างเกินกำหนด: {overdue}'),
                      p(f'ความคืบหน้า: {(completed / len(items) * 100) if items else 0:.1f}%')]],
                    colWidths=[6.5 * cm] * 4)
    summary.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#eaf2fc')),
                                 ('BOX', (0, 0), (-1, -1), .5, colors.HexColor('#b6cbe5')),
                                 ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                 ('TOPPADDING', (0, 0), (-1, -1), 10),
                                 ('BOTTOMPADDING', (0, 0), (-1, -1), 10)]))
    story.extend([summary, Spacer(1, .35 * cm), p('3. รายละเอียดความเสียหายและผลการแก้ไข', section)])
    for index, item in enumerate(items, 1):
        heading_row = Table([[p(f'รายการที่ {index}   |   สถานะ: {item.get("status", "-")}   |   ความเร่งด่วน: {item.get("priority", "ปกติ")}', small)]], colWidths=[26 * cm])
        heading_row.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#dfe9f5')),
                                         ('TOPPADDING', (0, 0), (-1, -1), 7),
                                         ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
        before = Table([[p('ภาพก่อนแก้ไข', small)], [photo(item.get('before'))],
                        [p('รายละเอียด: ' + (item.get('damage') or '-'), small)]], colWidths=[6.1 * cm])
        after = Table([[p('ภาพหลังแก้ไข', small)], [photo(item.get('after'))],
                       [p('การแก้ไข: ' + (item.get('repair') or '-'), small)]], colWidths=[6.1 * cm])
        details = [f"ผู้รับผิดชอบ: {item.get('responsible') or '-'}",
                   f"กำหนดเสร็จ: {item.get('due_date') or '-'}",
                   f"วันที่เสร็จ: {item.get('completed_date') or '-'}"]
        detail = p('\n'.join(details), small)
        t = Table([[before, after, detail]], colWidths=[6.4 * cm, 6.4 * cm, 13.2 * cm])
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#d3dce7')),
                               ('TOPPADDING', (0, 0), (-1, -1), 7)]))
        story.extend([Spacer(1, .2 * cm), heading_row, t])
    story.extend([Spacer(1, .35 * cm), p('4. ลงนามรับรอง', section)])

    def sign_cell(ref, name, role):
        image = photo(ref, 4.5 * cm, 1.4 * cm) if ref else p('........................................')
        table = Table([[image], [p(name or '................................', small)], [p(role, small)]], colWidths=[11 * cm])
        table.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER')]))
        return table

    signatures = Table([[sign_cell(r.get('rep_sign'), r.get('rep_name'), 'ตัวแทนโครงการ'),
                         sign_cell(r.get('owner_sign'), r.get('owner_sign_name'), 'เจ้าของบ้าน')]],
                       colWidths=[13 * cm, 13 * cm])
    story.append(KeepTogether(signatures))

    def footer(canvas, d):
        canvas.setStrokeColor(colors.HexColor('#d8e1ed'))
        canvas.line(1.3 * cm, 1.05 * cm, 28.4 * cm, 1.05 * cm)
        canvas.setFont('Thai', 8)
        canvas.drawString(1.3 * cm, .7 * cm, f"เลขที่รายงาน {r['id'][:12].upper()}")
        canvas.drawRightString(28.4 * cm, .7 * cm, f'หน้า {d.page}')

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def export_backup():
    records = all_records()
    audits = query('SELECT * FROM audit_log ORDER BY event_time')
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('inspections.json', json.dumps(records, ensure_ascii=False, indent=2))
        z.writestr('audit_log.json', json.dumps(audits, ensure_ascii=False, indent=2))
        z.writestr('README.txt', 'Backup of inspections, audit log and image assets. Keep private.\n')
        for row in query('SELECT id,mime,data FROM assets'):
            ext = 'png' if row['mime'] == 'image/png' else 'jpg'
            z.writestr(f'assets/{row["id"]}.{ext}', bytes(row['data']))
        # Older local images, when available, are included as well.
        paths = set()
        for record in records:
            for item in items_of(record):
                paths.update(str(item.get(k) or '') for k in ('before', 'after'))
            paths.update(str(record.get(k) or '') for k in ('rep_sign', 'owner_sign'))
        for path in paths:
            if path and not path.startswith('db:'):
                try:
                    f = Path(path)
                    if f.is_file():
                        z.write(f, f'legacy_assets/{f.name}')
                except OSError:
                    pass
    return result.getvalue()


st.title('🏠 ระบบสำรวจความเสียหายบ้านข้างเคียง')
st.caption('ภาพก่อน–หลัง • ติดตามงาน • ประวัติการแก้ไข • PDF ภาษาไทย • สำรองข้อมูล')
if not REMOTE_DB:
    st.warning('ขณะนี้ใช้ SQLite ในเครื่องเซิร์ฟเวอร์: บน Streamlit Cloud ข้อมูลอาจสูญหายเมื่อรีสตาร์ต/ปรับใช้ใหม่ กรุณาตั้งค่า DATABASE_URL สำหรับ PostgreSQL ถาวร และสำรองข้อมูลก่อนเปลี่ยนระบบ')

if 'pending_menu' in st.session_state:
    st.session_state.current_menu = st.session_state.pop('pending_menu')
elif 'current_menu' not in st.session_state:
    st.session_state.current_menu = MENU[0]
tab = st.radio('เมนูหลัก', MENU, horizontal=True, key='current_menu')
records = all_records()

if tab == MENU[0]:
    if 'form_record' not in st.session_state:
        load_form(None)
    r = st.session_state.form_record
    v = st.session_state.form_version
    if st.button('➕ เริ่มชุดข้อมูลใหม่', key='new_top'):
        load_form(None)
        st.rerun()
    st.subheader('1. รายละเอียดบ้าน')
    a, b = st.columns(2)
    with a:
        r['owner'] = st.text_input('ชื่อเจ้าของบ้าน *', value=r['owner'], key=f'owner_{v}')
        r['house_no'] = st.text_input('บ้านเลขที่ *', value=r['house_no'], key=f'house_{v}')
    with b:
        r['project'] = st.text_input('ชื่อโครงการ *', value=r['project'], key=f'project_{v}')
        r['survey_date'] = str(st.date_input('วันที่สำรวจ', value=dt.date.fromisoformat(r['survey_date']), key=f'date_{v}'))
    st.subheader('2. รายการความเสียหาย')
    st.caption(f"จำนวน {len(r['items'])}/100 รายการ")
    for i, item in enumerate(r['items']):
        with st.container(border=True):
            st.markdown(f'#### รายการความเสียหาย #{i + 1}')
            left, right = st.columns(2)
            with left:
                uploaded = st.file_uploader('ภาพก่อนแก้ไข', type=['jpg', 'jpeg', 'png', 'webp'], key=f'before_{v}_{i}')
                if uploaded is not None and item.get('_before_file') != (uploaded.name, uploaded.size):
                    item['before'] = persist_image(uploaded)
                    item['_before_file'] = (uploaded.name, uploaded.size)
                if item.get('before'):
                    raw = asset_bytes(item['before'])
                    if raw:
                        st.image(raw, width=260)
                item['damage'] = st.text_area('รายละเอียดความเสียหาย', value=item.get('damage', ''), key=f'damage_{v}_{i}')
            with right:
                uploaded = st.file_uploader('ภาพหลังแก้ไข (เพิ่มภายหลังได้)', type=['jpg', 'jpeg', 'png', 'webp'], key=f'after_{v}_{i}')
                if uploaded is not None and item.get('_after_file') != (uploaded.name, uploaded.size):
                    item['after'] = persist_image(uploaded)
                    item['_after_file'] = (uploaded.name, uploaded.size)
                if item.get('after'):
                    raw = asset_bytes(item['after'])
                    if raw:
                        st.image(raw, width=260)
                item['repair'] = st.text_area('รายละเอียดการแก้ไข', value=item.get('repair', ''), key=f'repair_{v}_{i}')
            c1, c2, c3 = st.columns(3)
            with c1:
                status = item.get('status', STATUS[0])
                item['status'] = st.selectbox('สถานะ', STATUS, index=STATUS.index(status) if status in STATUS else 0, key=f'status_{v}_{i}')
                item['responsible'] = st.text_input('ผู้รับผิดชอบ', value=item.get('responsible', ''), key=f'resp_{v}_{i}')
            with c2:
                priority = item.get('priority', PRIORITY[0])
                item['priority'] = st.selectbox('ความเร่งด่วน', PRIORITY, index=PRIORITY.index(priority) if priority in PRIORITY else 0, key=f'priority_{v}_{i}')
                due = item.get('due_date') or ''
                due_value = st.date_input('กำหนดวันแก้ไข', value=dt.date.fromisoformat(due) if due else None,
                                          key=f'due_{v}_{i}', format='YYYY/MM/DD')
                item['due_date'] = due_value.isoformat() if due_value else ''
            with c3:
                completed = item.get('completed_date') or ''
                done_date = st.date_input('วันที่แก้ไขเสร็จ', value=dt.date.fromisoformat(completed) if completed else None,
                                          key=f'completed_{v}_{i}', format='YYYY/MM/DD')
                item['completed_date'] = done_date.isoformat() if done_date else ''
                if is_overdue(item):
                    st.error('เกินกำหนดแก้ไข')
            if st.button('🗑️ ลบรายการนี้', key=f'del_{v}_{i}', disabled=len(r['items']) == 1):
                r['items'].pop(i)
                st.session_state.form_version += 1
                st.rerun()
    if st.button('➕ เพิ่มรายการความเสียหาย', disabled=len(r['items']) >= 100):
        r['items'].append(fresh_item())
        st.session_state.form_version += 1
        st.rerun()
    st.subheader('3. ลงนามรับรอง')
    s1, s2 = st.columns(2)
    with s1:
        st.markdown('**ลายเซ็นตัวแทนโครงการ**')
        rep_canvas = st_canvas(stroke_width=2, stroke_color='#111827', background_color='#FFFFFF',
                               height=130, width=320, drawing_mode='freedraw', key=f'rep_canvas_{v}')
        r['rep_name'] = st.text_input('ชื่อผู้ลงนาม (ตัวแทนโครงการ)', value=r.get('rep_name', ''), key=f'rep_name_{v}')
        if r.get('rep_sign'):
            st.caption('มีลายเซ็นเดิมอยู่แล้ว หากไม่เซ็นใหม่จะเก็บลายเซ็นเดิม')
    with s2:
        st.markdown('**ลายเซ็นเจ้าของบ้าน**')
        own_canvas = st_canvas(stroke_width=2, stroke_color='#111827', background_color='#FFFFFF',
                               height=130, width=320, drawing_mode='freedraw', key=f'own_canvas_{v}')
        r['owner_sign_name'] = st.text_input('ชื่อผู้ลงนาม (เจ้าของบ้าน)', value=r.get('owner_sign_name', ''), key=f'own_name_{v}')
        if r.get('owner_sign'):
            st.caption('มีลายเซ็นเดิมอยู่แล้ว หากไม่เซ็นใหม่จะเก็บลายเซ็นเดิม')
    actor = st.text_input('ชื่อผู้บันทึก/ผู้แก้ไข (สำหรับประวัติการแก้ไข)', key=f'actor_{v}')
    if st.button('💾 บันทึกข้อมูล', type='primary', use_container_width=True):
        if not all([r['owner'].strip(), r['house_no'].strip(), r['project'].strip()]):
            st.error('กรุณากรอกชื่อเจ้าของบ้าน บ้านเลขที่ และโครงการ')
        elif not any(i.get('damage', '').strip() for i in r['items']):
            st.error('กรุณากรอกรายละเอียดความเสียหายอย่างน้อย 1 รายการ')
        else:
            r['rep_sign'] = persist_sign(rep_canvas, r.get('rep_sign', ''))
            r['owner_sign'] = persist_sign(own_canvas, r.get('owner_sign', ''))
            for item in r['items']:
                item.pop('_before_file', None)
                item.pop('_after_file', None)
            saved = save_record(r, actor)
            st.session_state.last_saved = saved['id']
            load_form(None)
            st.success('บันทึกสำเร็จ สามารถเปิดดู PDF ในหน้าประวัติรายการได้')
    if st.session_state.get('last_saved'):
        last = fetch(st.session_state.last_saved)
        if last:
            st.download_button('📄 ดาวน์โหลด PDF ล่าสุด', pdf_report(last),
                               file_name=f'inspection_{last["id"][:10]}.pdf', mime='application/pdf')

elif tab == MENU[1]:
    st.subheader('ประวัติการสำรวจ')
    search = st.text_input('ค้นหาชื่อเจ้าของบ้าน บ้านเลขที่ หรือโครงการ')
    shown = [r for r in records if search.lower() in (r['owner'] + ' ' + r['house_no'] + ' ' + r['project']).lower()]
    if not shown:
        st.info('ยังไม่มีข้อมูลที่ตรงกับการค้นหา')
    for number, r in enumerate(shown, 1):
        items = items_of(r)
        done = sum(is_done(x) for x in items)
        overdue = sum(is_overdue(x) for x in items)
        with st.container(border=True):
            st.markdown(f"**{number}. บ้านเลขที่ {r['house_no']} — {r['owner']}**")
            st.caption(f"โครงการ: {r['project']} | สำรวจ: {r['survey_date']} | อัปเดต: {r['updated_at']} | เสร็จ {done}/{len(items)} | เกินกำหนด {overdue}")
            c1, c2 = st.columns(2)
            with c1:
                if st.button('✏️ เปิดแก้ไข/เพิ่มภาพหลังแก้ไข', key='edit_' + r['id']):
                    load_form(r)
                    st.session_state.pending_menu = MENU[0]
                    st.rerun()
            with c2:
                st.download_button('⬇️ ดาวน์โหลด PDF ล่าสุด', pdf_report(r),
                                   file_name=f'inspection_{r["id"][:10]}.pdf',
                                   mime='application/pdf', key='pdf_' + r['id'])
            with st.expander('🖼️ เปรียบเทียบภาพก่อน–หลัง และรายละเอียด'):
                for i, item in enumerate(items, 1):
                    st.markdown(f'**รายการ {i} — {item.get("status", "-")}**')
                    left, right = st.columns(2)
                    with left:
                        st.caption('ก่อนแก้ไข')
                        raw = asset_bytes(item.get('before'))
                        if raw:
                            st.image(raw, use_container_width=True)
                        st.write(item.get('damage') or '-')
                    with right:
                        st.caption('หลังแก้ไข')
                        raw = asset_bytes(item.get('after'))
                        if raw:
                            st.image(raw, use_container_width=True)
                        st.write(item.get('repair') or '-')

elif tab == MENU[2]:
    st.subheader('แดชบอร์ดสรุปความคืบหน้า')
    all_items = [i for r in records for i in items_of(r)]
    total = len(all_items)
    done = sum(is_done(i) for i in all_items)
    overdue = sum(is_overdue(i) for i in all_items)
    a, b, c, d, e = st.columns(5)
    a.metric('บ้านที่สำรวจ', len(records))
    b.metric('รายการทั้งหมด', total)
    c.metric('เสร็จแล้ว', done)
    d.metric('เกินกำหนด', overdue)
    e.metric('ความคืบหน้า', f'{done / total * 100:.1f}%' if total else '0%')
    st.progress(done / total if total else 0)
    counts = pd.DataFrame({'สถานะ': STATUS, 'จำนวน': [sum(i.get('status') == s for i in all_items) for s in STATUS]})
    st.bar_chart(counts.set_index('สถานะ'))
    if records:
        st.subheader('สรุปรายโครงการ')
        data = []
        for project in sorted({r['project'] for r in records}):
            rs = [r for r in records if r['project'] == project]
            its = [i for r in rs for i in items_of(r)]
            data.append({'โครงการ': project, 'บ้าน': len(rs), 'รายการ': len(its),
                         'เสร็จแล้ว': sum(is_done(i) for i in its), 'เกินกำหนด': sum(is_overdue(i) for i in its)})
        st.dataframe(pd.DataFrame(data), hide_index=True, use_container_width=True)
        st.subheader('รายการที่ต้องติดตาม')
        tasks = []
        for r in records:
            for n, item in enumerate(items_of(r), 1):
                if not is_done(item):
                    tasks.append({'บ้านเลขที่': r['house_no'], 'โครงการ': r['project'], 'รายการ': n,
                                  'รายละเอียด': item.get('damage', ''), 'ผู้รับผิดชอบ': item.get('responsible', ''),
                                  'กำหนดเสร็จ': item.get('due_date', ''), 'ความเร่งด่วน': item.get('priority', ''),
                                  'สถานะ': item.get('status', ''), 'เกินกำหนด': 'ใช่' if is_overdue(item) else 'ไม่'})
        if tasks:
            st.dataframe(pd.DataFrame(tasks), hide_index=True, use_container_width=True)
        else:
            st.success('ไม่มีงานค้างดำเนินการ')

elif tab == MENU[3]:
    st.subheader('ประวัติการแก้ไข (Audit Trail)')
    logs = query('SELECT event_time,actor,action,record_id,summary FROM audit_log ORDER BY event_time DESC LIMIT 1000')
    if logs:
        st.dataframe(pd.DataFrame(logs).rename(columns={'event_time': 'วันเวลา', 'actor': 'ผู้แก้ไข',
                                                        'action': 'การกระทำ', 'record_id': 'รหัสรายการ',
                                                        'summary': 'รายละเอียด'}), hide_index=True, use_container_width=True)
        st.caption('เก็บสำเนาข้อมูล ณ เวลาบันทึกในฐานข้อมูลด้วย แต่ไม่แสดงข้อมูลส่วนตัวทั้งหมดในตารางนี้')
    else:
        st.info('ยังไม่มีประวัติการแก้ไข ข้อมูลที่เคยบันทึกก่อนอัปเกรดจะไม่มีประวัติย้อนหลัง')

else:
    st.subheader('สำรองข้อมูล')
    st.write('ดาวน์โหลดข้อมูลสำรวจ ประวัติการแก้ไข และรูปภาพทั้งหมดเป็น ZIP แล้วเก็บในพื้นที่ที่ปลอดภัย')
    st.warning('ไฟล์สำรองมีข้อมูลส่วนบุคคล รูปภาพ และลายเซ็น ห้ามเผยแพร่ต่อสาธารณะ')
    if st.button('เตรียมไฟล์สำรองข้อมูล'):
        st.session_state.backup_bytes = export_backup()
    if st.session_state.get('backup_bytes'):
        st.download_button('⬇️ ดาวน์โหลดไฟล์สำรอง ZIP', st.session_state.backup_bytes,
                           file_name=f'inspection_backup_{dt.datetime.now():%Y%m%d_%H%M}.zip',
                           mime='application/zip')
    st.info('การสำรอง ZIP เป็นแบบดาวน์โหลดด้วยตนเอง หากต้องการสำรองอัตโนมัติ ให้เปิดระบบสำรองของผู้ให้บริการ PostgreSQL เพิ่มเติม')
