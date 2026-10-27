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
