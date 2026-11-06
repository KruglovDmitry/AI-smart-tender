import io
import pytest
from pathlib import Path
from docx import Document
from docx.shared import Inches
from PIL import Image
from test_doc_tools import client


def _document(root,large=False):
    image=Image.new('RGB',(240,100),'white');image.save(root/'picture.png')
    doc=Document();doc.add_heading('Требования',1);doc.add_paragraph('Перед таблицей')
    table=doc.add_table(rows=2,cols=2)
    table.cell(0,0).text='Артикул';table.cell(0,1).text='Напряжение'
    table.cell(1,0).text='00123';table.cell(1,1).text='24 V DC'
    doc.add_paragraph('После таблицы');doc.add_picture(str(root/'picture.png'),width=Inches(2))
    doc.sections[0].header.paragraphs[0].text='Колонтитул'
    if large:
        for i in range(80):doc.add_paragraph(f'Пункт {i}: '+('срок поставки; '*60))
    doc.save(root/'input.docx')


def test_read_folder_uses_content_type_for_extensionless_docx(client):
    c, root = client
    folder = root / 'tenders' / 'lot'
    folder.mkdir(parents=True)
    _document(folder)
    (folder / 'input.docx').replace(folder / 'I. Общие сведения')
    listed = c.get('/list_documents', params={'path': 'tenders/lot'}).json()
    entry = next(item for item in listed['entries'] if item['name'].startswith('I.'))
    assert entry['supported'] and entry['extension'] == '.docx'
    out = c.post('/read_folder', json={'path': 'tenders/lot'}).json()
    assert any('00123' in doc['content'] for doc in out['documents'])


def test_docx_field_note_does_not_block_a_finished_read(client):
    from docx.oxml import OxmlElement
    c, root = client
    doc = Document()
    doc.add_paragraph('Срок поставки 15 дней')
    paragraph = doc.add_paragraph()
    run = paragraph.add_run()
    field = OxmlElement('w:fldSimple')
    field.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}instr', 'DATE')
    text = OxmlElement('w:t')
    text.text = '01.02.2026'
    run._r.append(field)
    field.append(text)
    doc.save(root / 'fields.docx')
    out = c.post('/read_docx', json={'path': 'fields.docx', 'visual_mode': 'off'})
    assert out.status_code == 200, out.text
    out = out.json()
    assert any('field_results_are_saved_not_recalculated' in part['warnings'] for part in out['parts'])
    assert out['content_complete'] is True
    assert 'Срок поставки 15 дней' in '\n'.join(part['text'] for part in out['parts'])


def test_docx_order_tables_images_headers_and_search(client):
    c,root=client;_document(root)
    skill=c.get('/document_skill',params={'file_type':'docx'});assert skill.status_code==200,skill.text
    out=c.post('/read_docx',json={'path':'input.docx','visual_mode':'off'})
    assert out.status_code==200,out.text
    out=out.json();assert out['page_unit']=='logical_part'
    text='\n'.join(p['text'] for p in out['parts'])
    assert text.index('Перед таблицей')<text.index('00123')<text.index('После таблицы')
    assert 'Колонтитул' in text
    assert any(b['kind']=='table_row' for p in out['parts'] for b in p['layout']['blocks'])
    images=c.get('/list_docx_images',params={'path':'input.docx'}).json()
    assert images['total']==1 and images['images'][0]['available']
    found=c.post('/search_document',json={'path':'input.docx','query':'00123'}).json()
    assert found['results']


def test_docx_large_index_and_continuation(client):
    c,root=client;_document(root,True)
    out=c.post('/read_docx',json={'path':'input.docx','visual_mode':'off'}).json()
    assert out['mode']=='index' and not out['content_complete'] and out['parts_read']==[]
    payload={'path':'input.docx','mode':'full','visual_mode':'off','max_parts':3,'max_chars':1800}
    chunks=[];cursors=set()
    for _ in range(100):
        r=c.post('/read_docx',json=payload);assert r.status_code==200,r.text
        out=r.json();chunks.extend(p['text'] for p in out['parts'])
        cursor=out['next_cursor']
        if cursor is None:break
        key=tuple(sorted(cursor.items()));assert key not in cursors;cursors.add(key)
        payload.update(cursor)
    else:pytest.fail('DOCX continuation did not terminate')
    assert 'Пункт 79:' in ''.join(chunks)


def test_docx_embedded_image_uses_shared_vision_cache(client,monkeypatch):
    c,root=client;_document(root)
    from app.document_tool.docx import vision
    from app.document_tool import vision as shared
    monkeypatch.setattr(vision,'_vl_config',lambda:('https://example.invalid/v1','fake','fake-vl'))
    calls=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'message':{'content':'A white synthetic picture'}}],'usage':{'total_tokens':12}}
    monkeypatch.setattr(shared.httpx,'post',lambda *a,**kw:(calls.append(kw['json']) or Response()))
    image_id=vision.list_images('input.docx')['images'][0]['image_id']
    first=vision.view_image('input.docx',image_id,'What is visible?','high')
    second=vision.view_image('input.docx',image_id,'What is visible?','high')
    assert not first['cached'] and second['cached'] and len(calls)==1
    assert first['image_ids']==[image_id]
    assert calls[0]['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/png;base64,')


def test_create_docx_is_verified_and_downloadable(client):
    c,root=client
    Image.new('RGB',(200,100),'white').save(root/'local.png')
    payload={'filename':'answer.docx','title':'Ответ','blocks':[{'kind':'heading','text':'Раздел'},
        {'text':'Русский текст','bold':True},{'kind':'bullet','text':'Пункт'},
        {'kind':'table','rows':[['Код','Значение'],['00123','24 V DC']]},
        {'kind':'image','image_path':'local.png','text':'Схема'},{'kind':'page_break'},{'text':'Конец'}]}
    r=c.post('/write_docx',json=payload);assert r.status_code==200,r.text
    out=r.json();assert out['verification']['body_and_media_checked']
    assert not out['verification']['layout_rendered']
    document=Document(root/out['path']);assert document.tables[0].cell(1,0).text=='00123'
    download=c.get('/download_file',params={'path':out['path']})
    assert download.status_code==200 and download.content.startswith(b'PK')
    assert 'wordprocessingml' in download.headers['content-type']
    again=c.post('/write_docx',json=payload).json();assert again['path']!=out['path']


def test_docx_unconfigured_render_is_explicit(client,monkeypatch):
    c,root=client;_document(root)
    from app.document_tool.docx import render
    def unavailable(*args):raise ValueError('Physical DOCX pages require LibreOffice')
    monkeypatch.setattr(render,'render_pdf',unavailable)
    out=c.post('/view_docx_page',json={'path':'input.docx','page':1})
    assert out.status_code==400 and 'LibreOffice' in out.json()['detail']


def test_docx_upload_uses_same_reader_without_vision(client,monkeypatch):
    c,root=client;_document(root)
    from app.document_tool.docx import vision
    monkeypatch.setattr(vision,'view_part',lambda *a,**k:(_ for _ in ()).throw(AssertionError('upload must not call vision')))
    out=c.put('/process',content=(root/'input.docx').read_bytes(),headers={'X-Filename':'input.docx'})
    assert out.status_code==200,out.text
    data=out.json();assert '00123' in '\n'.join(p['page_content'] for p in data)
    assert data[0]['metadata']['page_unit']=='logical_part'


def test_docx_rendered_page_reuses_conversion_and_source(client,monkeypatch):
    import hashlib
    import pymupdf
    from types import SimpleNamespace
    c,root=client;_document(root)
    source=(root/'input.docx').read_bytes()
    from app.document_tool.docx import render
    from app.document_tool.pdf import vision
    monkeypatch.delenv('TOOLS_DOCX_SOFFICE',raising=False)
    monkeypatch.setattr(render.shutil,'which',lambda _: 'fake-soffice')
    calls=[]
    def convert(args,**kw):
        calls.append(args)
        assert '--headless' in args and any(a.startswith('-env:UserInstallation=') for a in args)
        folder=Path(args[args.index('--outdir')+1])
        doc=pymupdf.open();doc.new_page();doc.new_page();doc.save(folder/'document.pdf');doc.close()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(render.subprocess,'run',convert)
    meta=c.post('/render_docx',json={'path':'input.docx'})
    assert meta.status_code==200,meta.text
    assert meta.json()['physical_pages']==2
    monkeypatch.setattr(vision,'view_page',lambda path,page,question,bbox,detail:{'path':path,'page':page,'answer':'physical page','cached':True})
    viewed=c.post('/view_docx_page',json={'path':'input.docx','page':2})
    assert viewed.status_code==200,viewed.text
    assert viewed.json()['unit']=='rendered_physical_page' and viewed.json()['path']=='input.docx'
    assert len(calls)==1 and (root/'input.docx').read_bytes()==source


def test_docx_content_controls_and_large_single_block_are_not_lost(client):
    from docx.oxml import OxmlElement
    c,root=client;doc=Document();doc.add_paragraph('A'*9000)
    control=OxmlElement('w:sdt');content=OxmlElement('w:sdtContent')
    paragraph=OxmlElement('w:p');run=OxmlElement('w:r');text=OxmlElement('w:t');text.text='Inside content control'
    run.append(text);paragraph.append(run);content.append(paragraph);control.append(content)
    doc.element.body.insert(1,control);doc.save(root/'control.docx')
    from app.document_tool.docx.read import extract_parts
    parts=extract_parts(root/'control.docx')
    assert 'Inside content control' in '\n'.join(p['text'] for p in parts)
    assert sum(p['text'].count('A') for p in parts)==9000
    assert any(b['continued'] for p in parts for b in p['blocks'])
