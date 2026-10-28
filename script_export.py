"""Immutable, read-only script export snapshots; no production accounting."""
from pathlib import Path
from datetime import datetime
import csv, io, json, os, re, uuid, zipfile
from xml.sax.saxutils import escape
from editing_profiles import NAMES

HEADERS=('显示ID','文案库','原文','状态','使用次数','预留','最近使用','绑定数','可用配音数')
FORMATS=('独立 TXT','TXT 合集','CSV','XLSX')

def snapshot(library, *, scope, selected=(), profile_id=None, query='', status='全部', include_disabled=False, cross_library=False,spec_id=None):
    if scope=='已选文案':
        if not selected:raise ValueError('未选择文案，不能导出全部。')
        rows=[library.get(sid) for sid in dict.fromkeys(selected)]
        if not cross_library and any(r['profile_id']!=profile_id for r in rows):raise ValueError('所选文案跨库，请显式选择跨库导出。')
        if not cross_library and spec_id is not None and any(r['spec_id']!=spec_id for r in rows):raise ValueError('所选文案跨规格，请重新选择')
    elif scope in ('当前筛选结果','当前文案库全部'):
        rows=library.list(status if scope=='当前筛选结果' else '全部',profile_id=None if cross_library else profile_id,query=query if scope=='当前筛选结果' else '',spec_id=None if cross_library else spec_id)
        if not include_disabled:rows=[r for r in rows if r['enabled']]
    else:raise ValueError('未知导出范围。')
    if not rows:raise ValueError('当前范围没有文案。')
    for r in rows:
        r['external_summary']=library.external.summary(r['script_id'])
        r['category_name']=library.catalog.categories()[r['profile_id']]
        r['spec_name']=library.catalog.get(r['profile_id'],r.get('spec_id','default'))['name']
    return rows

def safe_name(value):
    name=re.sub(r'[^\w\-]','_',str(value),flags=re.UNICODE).strip(' ._')[:100] or 'script'
    if name.upper() in ('CON','PRN','AUX','NUL') or re.fullmatch(r'(COM|LPT)[1-9]',name,re.I):name='_'+name
    return name

def safe_csv(value):
    s=str(value)
    return "'"+s if s.lstrip(' \t\r\n').startswith(('=','+','-','@')) else s

def values(row):
    return (row.get('display_id') or row['script_id'],row.get('category_name',NAMES.get(row['profile_id'],row['profile_id']))+' / '+row.get('spec_name','通用规格'),row['raw_text'],'启用' if row['enabled'] else '停用',row['usage_count'],row['reserved_count'],row['last_used_at'] or '',row['external_summary']['bound'],row['external_summary']['valid'])

def atomic(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);part=path.with_name(path.name+'.part')
    with part.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(part,path)

def xlsx_bytes(rows):
    """Minimal OOXML, all cells inline strings, no formulas or macros."""
    matrix=[HEADERS]+[values(r) for r in rows];xml=[]
    for n,row in enumerate(matrix,1):
        cells=[]
        for col,val in enumerate(row):
            text=str(val)
            if len(text)>32767:text='正文超过 XLSX 单元格限制；完整内容见对应 TXT 和 export_manifest.json。'
            text=''.join(c for c in text if c in '\t\n\r' or ord(c)>=32)
            cells.append(f'<c r="{chr(65+col)}{n}" t="inlineStr" s="{1 if n==1 else 0}"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
        xml.append(f'<row r="{n}">'+''.join(cells)+'</row>')
    sheet='<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="2" width="20" customWidth="1"/><col min="3" max="3" width="90" customWidth="1"/><col min="4" max="9" width="18" customWidth="1"/></cols><sheetData>'+''.join(xml)+'</sheetData></worksheet>'
    parts={
        '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="文案" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml':'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="微软雅黑"/></font><font><b/><sz val="11"/><name val="微软雅黑"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf><xf fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        'xl/worksheets/sheet1.xml':sheet}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        for name,body in parts.items():z.writestr(name,body.encode('utf-8'))
    return buffer.getvalue()

def export(rows, directory, format='独立 TXT', *, scope='已选文案', zip_output=False, progress=lambda _:None, cancel=None):
    if format not in FORMATS:raise ValueError('未知导出格式。')
    batch='scripts_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
    root=Path(directory).resolve()/batch;root.mkdir(parents=True,exist_ok=False)
    manifest=dict(batch_id=batch,created=datetime.now().astimezone().isoformat(),scope=scope,format=format,csv_safety="公式开头添加单引号；无损原文见清单和 TXT，数据库不变。",records=[],files=[],success=0,skipped=0,failed=0)
    groups={r['profile_id'] for r in rows};used=set()
    for r in rows:
        rel=Path(safe_name(r.get('display_id') or r['script_id'])+'.txt')
        if len(groups)>1:rel=Path(safe_name(NAMES.get(r['profile_id'],r['profile_id'])))/rel
        if str(rel).casefold() in used:rel=rel.with_name(rel.stem+'_'+r['script_uid'][:8]+'.txt')
        used.add(str(rel).casefold())
        record=dict(internal_id=r['script_uid'],script_id=r['script_id'],display_id=r.get('display_id') or r['script_id'],library=r['profile_id'],body_version=r['body_version'],body_hash=r['text_hash'],raw_text=r['raw_text'],status='启用' if r['enabled'] else '停用',files=[])
        manifest['records'].append(record)
        record.update(spec_id=r.get('spec_id','default'),spec_name=r.get('spec_name',''),category_name=r.get('category_name',''))
        if cancel and cancel.is_set():record['result']='取消';manifest['skipped']+=1;continue
        try:
            # Always retain lossless TXT alongside tabular exports.
            if format!='TXT 合集':atomic(root/rel,r['raw_text'].encode('utf-8'));record['files'].append(str(rel));manifest['files'].append(str(rel))
            record['result']='success';manifest['success']+=1
        except OSError as exc:record.update(result='failed',error=str(exc));manifest['failed']+=1
        progress(f'导出 {len(manifest["records"])}/{len(rows)}')
    usable=[r for r,e in zip(rows,manifest['records']) if e['result']=='success']
    try:
        if format=='TXT 合集':
            body='\n\n'.join(f'[{r.get("display_id") or r["script_id"]} | {r.get("category_name",NAMES.get(r["profile_id"],r["profile_id"]))} / {r.get("spec_name","通用规格")}]\n'+r['raw_text']+'\n'+'-'*40 for r in usable);filename='文案合集.txt';data=body.encode('utf-8')
        elif format=='CSV':
            stream=io.StringIO(newline='');writer=csv.writer(stream);writer.writerow(HEADERS)
            for r in usable:writer.writerow([safe_csv(v) for v in values(r)])
            filename='文案.csv';data=stream.getvalue().encode('utf-8-sig')
        elif format=='XLSX':filename='文案.xlsx';data=xlsx_bytes(usable)
        else:filename=None
        if filename:atomic(root/filename,data);manifest['files'].append(filename)
    except Exception as exc:manifest['failed']+=1;manifest['format_error']=str(exc)
    manifest['long_text_fallbacks']=[r['script_id'] for r in usable if len(r['raw_text'])>32767] if format=='XLSX' else []
    atomic(root/'export_manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8'))
    if zip_output:
        part=root/'本次导出.zip.part'
        with zipfile.ZipFile(part,'w',zipfile.ZIP_DEFLATED) as z:
            for rel in manifest['files']+['export_manifest.json']:z.write(root/rel,rel)
        os.replace(part,root/'本次导出.zip')
    from external_voice import emit
    emit('script_export',scope=scope,batch_id=batch,directory=str(root),success=manifest['success'],failed=manifest['failed'],skipped=manifest['skipped'])
    return dict(directory=str(root),manifest=manifest)
