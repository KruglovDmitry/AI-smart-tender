"""Synthetic coverage of format adapters without cloud or installed office dependencies."""
from __future__ import annotations
import io
import zipfile
import pytest
from PIL import Image
from test_doc_tools import client


def test_text_decoding_preserves_source_and_continuation(client):
    c, root = client
    text = '  Артикул 00123\r\n\tзначение = 24\r\n' + 'X' * 40001 + '\nEND  '
    (root/'source.txt').write_bytes(text.encode('utf-16'))
    index = c.post('/read_text', json={'path':'source.txt'}).json()
    assert index['mode'] == 'index' and index['page_unit'] == 'logical_part'
    cursor = {}; chunks=[]; cursors=set()
    while True:
        out=c.post('/read_text', json={'path':'source.txt','mode':'full','max_parts':2,'max_chars':777, **cursor})
        assert out.status_code==200, out.text
        data=out.json();chunks.extend(p['text'] for p in data['parts'])
        cursor=data['next_cursor']
        if not cursor:break
        key=tuple(sorted(cursor.items()));assert key not in cursors;cursors.add(key)
    assert ''.join(chunks)==text
    assert c.post('/search_document',json={'path':'source.txt','query':'00123'}) .json()['results']
    whole=c.post('/read_document',json={'path':'source.txt','max_chars':100000}).json()
    assert whole['content'] == text


def test_markup_is_source_and_never_fetches_or_executes(client,monkeypatch):
    c,root=client
    import httpx
    monkeypatch.setattr(httpx,'get',lambda *a,**k: (_ for _ in ()).throw(AssertionError('no fetch')))
    source='<html><script>bad()</script><table><tr><td>00123</td></tr></table></html>'
    (root/'page.html').write_text(source,encoding='utf-8')
    out=c.post('/read_text',json={'path':'page.html'}).json()
    assert out['parts'][0]['text']==source
    (root/'russian.log').write_bytes('Привет 00123'.encode('cp1251'))
    cp=c.post('/read_text',json={'path':'russian.log'}).json()
    assert cp['parts'][0]['text']=='Привет 00123' and cp['parts'][0]['warnings']


def _presentation(root, count=1):
    from pptx import Presentation
    from pptx.util import Inches
    prs=Presentation()
    for n in range(count):
        slide=prs.slides.add_slide(prs.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1)).text=f'Заголовок {n+1}'
        table=slide.shapes.add_table(2,2, Inches(1), Inches(2), Inches(5), Inches(1)).table
        table.cell(0,0).text='Артикул';table.cell(0,1).text='Напряжение'
        table.cell(1,0).text='00123';table.cell(1,1).text='24 V DC'
        group=slide.shapes.add_group_shape()
        group.shapes.add_textbox(Inches(1),Inches(4),Inches(2),Inches(1)).text='Текст в группе'
        image=io.BytesIO();Image.new('RGB',(200,100),'white').save(image,format='PNG');image.seek(0)
        slide.shapes.add_picture(image, Inches(6), Inches(1), Inches(2))
        slide.notes_slide.notes_text_frame.text='Комментарий докладчика'
    prs.save(root/'slides.pptx')


def test_presentation_tables_groups_notes_and_large_index(client):
    c,root=client;_presentation(root,9)
    index=c.post('/read_presentation',json={'path':'slides.pptx'}).json()
    assert index['mode']=='index' and index['part_count']==9 and index['page_unit']=='slide'
    out=c.post('/read_presentation',json={'path':'slides.pptx','parts':'2','visual_mode':'off'}).json()
    slide=out['parts'][0]
    assert all(s in slide['text'] for s in ('00123','24 V DC','Текст в группе','Комментарий докладчика'))
    assert any(b['kind']=='table' for b in slide['layout']['blocks'])
    assert slide['layout']['images'][0]['id'].startswith('slide-2-image-')
    loaded=c.put('/process',content=(root/'slides.pptx').read_bytes(),headers={'X-Filename':'slides.pptx'})
    assert loaded.status_code==200
    assert loaded.json()[0]['metadata']['page_unit']=='slide'


def test_presentation_image_vision_cache_and_explicit_renderer_failure(client,monkeypatch):
    c,root=client;_presentation(root)
    from app.document_tool import vision,office
    calls=[]
    monkeypatch.setattr(vision,'_vl_config',lambda:('https://example.invalid/v1','fake','qwen-test'))
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'message':{'content':'White picture'}}]}
    monkeypatch.setattr(vision.httpx,'post',lambda *a,**kw:(calls.append(kw['json']) or Response()))
    read=c.post('/read_presentation',json={'path':'slides.pptx'}).json()
    image_id=read['parts'][0]['layout']['images'][0]['id']
    body={'path':'slides.pptx','slide':1,'image_id':image_id,'question':'Describe'}
    first=c.post('/view_presentation_image',json=body).json()
    second=c.post('/view_presentation_image',json=body).json()
    assert not first['cached'] and second['cached'] and len(calls)==1
    def missing(*a,**kw):raise ValueError('LibreOffice unavailable')
    monkeypatch.setattr(office,'executable',missing)
    rendered=c.post('/view_slide',json={'path':'slides.pptx'})
    assert rendered.status_code==400 and 'LibreOffice' in rendered.text


def test_images_frames_crop_vision_and_local_ocr(client,monkeypatch):
    c,root=client
    from app.document_tool import vision
    from app.document_tool.pdf import ocr
    first=Image.new('RGB',(100,60),'white');second=Image.new('RGB',(100,60),'blue')
    first.save(root/'scan.tiff',save_all=True,append_images=[second])
    meta=c.post('/read_image',json={'path':'scan.tiff'}).json()
    assert meta['page_unit']=='image_frame' and meta['part_count']==2 and not meta['content_complete']
    assert meta['parts'][0]['text']=='' and meta['parts'][0]['layout']['blocks'][0]['width']==100
    calls=[]
    monkeypatch.setattr(vision,'_vl_config',lambda:('https://example.invalid/v1','fake','qwen-test'))
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'message':{'content':'Blue scan'}}],'usage':{'total_tokens':10}}
    monkeypatch.setattr(vision.httpx,'post',lambda *a,**kw:(calls.append(kw['json']) or Response()))
    body={'path':'scan.tiff','frame':2,'bbox':[10,10,80,50]}
    out=c.post('/view_image',json=body).json()
    assert out['answer']=='Blue scan' and not out['cached']
    assert c.post('/view_image',json=body).json()['cached'] and len(calls)==1
    assert c.post('/view_image',json={**body,'bbox':[0,0,1000,1000]}).status_code==400
    monkeypatch.setattr(ocr,'image_text',lambda _: '00123 24 V DC')
    local=c.post('/ocr_image',json={'path':'scan.tiff','frame':1}).json()
    assert local['source']=='ocr' and '00123' in local['text']
    assert len(calls)==1
    assert 'Image pixels have not been read' in c.post('/read_document',json={'path':'scan.tiff'}).text


def test_magic_images_override_false_text_extension(client):
    c,root=client
    image=Image.new('RGB',(50,20),'white');image.save(root/'scan.txt',format='PNG')
    from app.document_tool.sniff import detect_extension
    assert detect_extension(root/'scan.txt')=='.png'
    (root/'plain.txt').write_text('BM ordinary source text',encoding='utf-8')
    assert detect_extension(root/'plain.txt')=='.txt'
    for suffix, image_format in [('bmp','BMP'), ('webp','WEBP'), ('gif','GIF'), ('jpg','JPEG')]:
        image.save(root/('sample.'+suffix),format=image_format)
        assert detect_extension(root/('sample.'+suffix))=='.'+suffix
    assert c.post('/read_text',json={'path':'scan.txt'}).status_code==400
    assert c.post('/read_image',json={'path':'scan.txt'}).status_code==200


def test_archive_manifest_selected_file_and_traversal(client):
    c,root=client
    with zipfile.ZipFile(root/'docs.zip','w') as archive:
        archive.writestr('folder/spec.txt','Артикул 00123')
        archive.writestr('../outside.txt','bad')
        link=zipfile.ZipInfo('link');link.create_system=3;link.external_attr=(0o120777 << 16)
        archive.writestr(link,'folder/spec.txt')
    manifest=c.get('/list_archive',params={'path':'docs.zip','limit':2}).json()
    assert manifest['total_entries']==3 and manifest['next_offset']==2
    assert manifest['entries'][1]['blocked_reason']=='unsafe_member_name'
    rejected=c.post('/open_archive_member',json={'path':'docs.zip','member':'../outside.txt'})
    assert rejected.status_code==400 and not (root.parent/'outside.txt').exists()
    assert c.post('/open_archive_member',json={'path':'docs.zip','member':'link'}).status_code==400
    opened=c.post('/open_archive_member',json={'path':'docs.zip','member':'folder/spec.txt'}).json()
    assert opened['path'].startswith('.archive-members/')
    out=c.post('/read_text',json={'path':opened['path']}).json()
    assert out['parts'][0]['text']=='Артикул 00123'
    indexed=c.post('/read_file',json={'path':'docs.zip'}).json()
    assert indexed['page_unit']=='archive_manifest_part' and not indexed['content_complete']
    assert '00123' not in indexed['parts'][0]['text']


def test_7z_selected_memory_stream(client):
    import py7zr
    c,root=client
    (root/'input.txt').write_text('Seven zip 00123',encoding='utf-8')
    with py7zr.SevenZipFile(root/'docs.7z','w') as archive:
        archive.write(root/'input.txt','nested/input.txt')
    listing=c.get('/list_archive',params={'path':'docs.7z'})
    assert listing.status_code==200,listing.text
    opened=c.post('/open_archive_member',json={'path':'docs.7z','member':'nested/input.txt'})
    assert opened.status_code==200,opened.text
    out=c.post('/read_text',json={'path':opened.json()['path']})
    assert out.json()['parts'][0]['text']=='Seven zip 00123'
    assert not (root/'nested').exists()


def test_legacy_odf_repeated_cells_and_rtf(client):
    c,root=client
    xml='<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet><table:table><table:table-row table:number-rows-repeated="2"><table:table-cell table:number-columns-repeated="2"><text:p>00123</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet></office:body></office:document-content>'
    with zipfile.ZipFile(root/'legacy.ods','w') as archive:
        archive.writestr('mimetype','application/vnd.oasis.opendocument.spreadsheet')
        archive.writestr('content.xml',xml)
    out=c.post('/read_file',json={'path':'legacy.ods'}).json()
    assert out['parts'][0]['text'].count('00123')==4 and out['parts'][0]['warnings']
    # XML entity expansion must never resolve local files or network resources.
    with zipfile.ZipFile(root/'entity.odt','w') as archive:
        archive.writestr('content.xml','<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><x>&e;</x>')
    assert c.post('/read_file',json={'path':'entity.odt'}).status_code==400
    (root/'legacy.rtf').write_bytes(br'{\rtf1\ansi ID 00123\par 24 V}')
    rtf=c.post('/read_file',json={'path':'legacy.rtf'}).json()
    assert '00123' in rtf['parts'][0]['text'] and rtf['parts'][0]['warnings']


def test_skills_schema_and_upload_policy(client,monkeypatch):
    c,root=client
    for kind in ['presentations','images','text','archives','legacy']:
        out=c.get('/document_skill',params={'file_type':kind})
        assert out.status_code==200 and out.json()['scope']=='tools-server'
    schema=c.get('/openapi.json').json()
    assert all(path in schema['paths'] for path in ['/read_file','/read_image','/read_presentation','/open_archive_member'])
    from app.document_tool.images import vision
    monkeypatch.setattr(vision,'view_frame',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('no cloud during uploads')))
    image=io.BytesIO();Image.new('RGB',(50,50),'white').save(image,format='PNG')
    upload=c.put('/process',content=image.getvalue(),headers={'X-Filename':'image.png'})
    assert upload.status_code==200 and upload.json()[0]['page_content']==''



def test_archive_upload_keeps_contents_while_server_index_is_manifest(client):
    c,root=client
    from docx import Document
    word=io.BytesIO();doc=Document();doc.add_paragraph('Uploaded article 00123');doc.save(word)
    with zipfile.ZipFile(root/'upload.zip','w') as archive:
        archive.writestr('spec.docx',word.getvalue())
        archive.writestr('../unsafe.txt','not imported')
    indexed=c.post('/read_file',json={'path':'upload.zip'}).json()
    assert '00123' not in ''.join(p['text'] for p in indexed['parts'])
    uploaded=c.put('/process',content=(root/'upload.zip').read_bytes(),headers={'X-Filename':'upload.zip'})
    assert uploaded.status_code==200,uploaded.text
    data=uploaded.json()
    assert 'Uploaded article 00123' in ''.join(p['page_content'] for p in data)
    assert 'not imported' not in ''.join(p['page_content'] for p in data)
    assert 'archive_contents_incomplete' in data[0]['metadata']['warnings']


def test_markup_upload_is_readable_but_read_text_keeps_source(client):
    c,root=client
    html='<html><body><h1>Catalogue</h1><table><tr><td>00123</td><td>24 V</td></tr></table></body></html>'
    (root/'upload.html').write_text(html,encoding='utf-8')
    source=c.post('/read_text',json={'path':'upload.html'}).json()
    assert source['parts'][0]['text']==html
    uploaded=c.put('/process',content=html.encode(),headers={'X-Filename':'upload.html'})
    assert uploaded.status_code==200,uploaded.text
    text=''.join(p['page_content'] for p in uploaded.json())
    assert 'Catalogue' in text and '00123' in text and '<table>' not in text


def test_transparent_image_keeps_dark_labels_on_visible_background():
    from app.document_tool.raster import prepare_png
    image=Image.new('RGBA',(20,20),(0,0,0,0));image.putpixel((10,10),(0,0,0,255))
    original=io.BytesIO();image.save(original,format='PNG')
    with Image.open(io.BytesIO(prepare_png(original.getvalue()))) as prepared:
        assert prepared.getpixel((0,0))==(255,255,255)
        assert prepared.getpixel((10,10))==(0,0,0)
