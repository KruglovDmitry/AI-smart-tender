"""Инструменты больших документов и таблиц: поиск артикулов, чтение страниц, запрос и заполнение Excel."""

from __future__ import annotations

import importlib

import pytest
from openpyxl import Workbook, load_workbook


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    from app import config

    importlib.reload(config)
    import app.document_tool as dt
    import app.document_tool.spreadsheets.create as et
    import app.document_tool.spreadsheets.export as ex
    import app.document_index as di
    import app.document_tool.spreadsheets.operations as tt
    import app.main as main

    for m in (dt, ex, et, di, tt, main):
        importlib.reload(m)
    from fastapi.testclient import TestClient

    return TestClient(main.app), tmp_path


def _catalog(path):
    import pymupdf

    doc = pymupdf.open()
    for text in (
        "СЕРИЯ 55\nТип 55.32 - 2 CO 10 A\nКодировка: 55.32.9.024.0000",
        "Тип 55.32 катушки AC и DC\nПример: 55.34.9.220.9202",
        "Твердотельное реле 34.81.7.012.9024 питание 12В DC",
    ):
        doc.new_page().insert_text((50, 72), text, fontname="helv")
    doc.save(path)


def test_search_code_exact_and_prefix(client):
    c, root = client
    (root / "catalogs").mkdir()
    _catalog(root / "catalogs" / "cat.pdf")

    r = c.post("/search_document", json={"path": "catalogs/cat.pdf", "query": "348170129024"}).json()
    assert r["match"] == "exact" and [x["page"] for x in r["results"]] == [3]

    # Полного кода нет; самый длинный напечатанный префикс 55.32.9 есть только в кодировке на стр. 1.
    r = c.post("/search_document", json={"path": "catalogs/cat.pdf", "query": "55.32.9.012.0000"}).json()
    assert r["match"] == "prefix" and r["matched"] == "55329"
    assert [x["page"] for x in r["results"]] == [1]

    r = c.post("/search_document", json={"path": "catalogs/cat.pdf", "query": "55.32.7.110.0000"}).json()
    assert r["matched"] == "5532" and {x["page"] for x in r["results"]} == {1, 2}


def test_read_pages_and_info(client):
    c, root = client
    (root / "catalogs").mkdir()
    _catalog(root / "catalogs" / "cat.pdf")
    info = c.post("/document_info", json={"path": "catalogs/cat.pdf"}).json()
    assert info["pages"] == 3 and info["broken_pages"] == []
    r = c.post("/read_pages", json={"path": "catalogs/cat.pdf", "pages": "2-3", "include_tables": False}).json()
    assert [p["page"] for p in r["pages"]] == [2, 3] and "34.81.7.012.9024" in r["pages"][1]["text"]
    assert c.post("/read_pages", json={"path": "catalogs/cat.pdf", "pages": "9"}).status_code == 400


def test_owui_loader_rtf_text_not_markup(client):
    c, root = client
    word = "Техническое задание".encode("cp1251")
    rtf = r"{\rtf1\ansi\ansicpg1251{\fonttbl{\f0 Times New Roman;}}\f0 " + "".join(f"\\'{b:02x}" for b in word) + r"\par}"
    r = c.put("/process", content=rtf.encode("ascii"), headers={"X-Filename": "tz.rtf"})
    text = r.json()[0]["page_content"]
    assert "Техническое задание" in text and "\\rtf1" not in text


def test_owui_loader_extensionless_pdf(client):
    """Имя без расширения (как с ЕИС) — тип по содержимому."""
    import pymupdf

    c, _root = client
    buf = __import__("io").BytesIO()
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 72), "Delivery term 30 days", fontname="helv")
    doc.save(buf)
    doc.close()
    r = c.put(
        "/process",
        content=buf.getvalue(),
        headers={"X-Filename": "I. Obschie svedeniya (tovar)"},
    )
    assert r.status_code == 200
    text = r.json()[0]["page_content"]
    assert "Delivery" in text or "30" in text


def test_extract_file_sniffs_extensionless_pdf(tmp_path, monkeypatch):
    import importlib

    import pymupdf

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    from app import config

    importlib.reload(config)
    import app.document_tool.extract as ex

    importlib.reload(ex)
    path = tmp_path / "I. Obschie svedeniya"
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 72), "hello pdf", fontname="helv")
    doc.save(path)
    doc.close()
    out = ex.extract_file(path)
    assert out["extension"] == ".pdf" and "hello" in out["content"]


def _template(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "IMPORT"
    ws.append(["Код ТНВЭД", "Полное наименование", "Вид товара", "Артикул"])
    ws.append(["Tnved", "2478", "12", "13914"])
    ws.append(["8536490000", None, None, 553290240000])
    ws.append(["8536490000", None, None, 405180600000])
    ref = wb.create_sheet("Справочники")
    ref.append(["Вид товара", "РЕЛЕ ПРОМЕЖУТОЧНОЕ МИНИАТЮРНОЕ"])
    wb.save(path)


def test_query_and_fill_table(client):
    c, root = client
    (root / "catalogs").mkdir()
    _template(root / "catalogs" / "t.xlsx")

    d = c.post("/describe_table", json={"path": "catalogs/t.xlsx", "sheet": "IMPORT"}).json()
    assert d["details"][0]["header_row"] == 1

    q = c.post(
        "/query_table",
        json={"path": "catalogs/t.xlsx", "data_start_row": 3, "columns": ["Артикул"], "filters": [{"column": "B", "op": "empty"}]},
    ).json()
    assert [r["row"] for r in q["rows"]] == [3, 4]

    f = c.post(
        "/fill_table",
        json={
            "path": "catalogs/t.xlsx",
            "key_column": "D",
            "updates": [
                {"key": "553290240000", "values": {"B": "Реле 2CO", "Вид товара": "РЕЛЕ ПРОМЕЖУТОЧНОЕ МИНИАТЮРНОЕ"}},
                {"key": "405180600000", "values": {"C": "ВЫДУМАННЫЙ ВИД"}},
            ],
            "allowed_values": {"C": ["РЕЛЕ ПРОМЕЖУТОЧНОЕ МИНИАТЮРНОЕ"]},
        },
    ).json()
    assert f["cells_written"] == 2 and f["problems_total"] == 1 and f["path"].startswith("exports/")

    wb = load_workbook(root / f["path"])
    assert wb["IMPORT"]["B3"].value == "Реле 2CO" and wb["IMPORT"]["C4"].value is None
    assert wb.sheetnames == ["IMPORT", "Справочники"]
    # Исходный шаблон не тронут
    assert load_workbook(root / "catalogs" / "t.xlsx")["IMPORT"]["B3"].value is None




def test_pdf_skill_is_discoverable_and_scoped(client):
    c, root = client
    result = c.get('/document_skill', params={'file_type': 'pdf'})
    assert result.status_code == 200
    skill = result.json()
    assert skill['scope'] == 'tools-server'
    assert 'Open WebUI' in skill['instructions']
    assert 'match=prefix' in skill['instructions']
    assert c.get('/document_skill', params={'file_type': '../config'}).status_code == 422
    assert '/document_skill' in c.get('/openapi.json').json()['paths']
    _catalog(root / 'cat.pdf')
    info = c.post('/document_info', json={'path': 'cat.pdf'}).json()
    assert 'document_skill' in info['skill_hint']


def test_pdf_reader_renders_and_reads_legacy_text(tmp_path):
    from app.document_tool.pdf import read

    path = tmp_path / 'cat.pdf'
    _catalog(path)
    assert len(read.extract_pages(path)) == 3
    assert '55.32.9.024.0000' in read.extract_text(path)
    assert read.render_png(path, 1, dpi=72).startswith(b'\x89PNG\r\n\x1a\n')


def test_pdf_ocr_path_uses_moved_handler(client, monkeypatch):
    from app import document_index
    from app.document_tool.pdf import ocr

    c, root = client
    path = root / 'scan.pdf'
    _catalog(path)
    idx = document_index.get_index('scan.pdf')
    idx.pages[0].update(broken=True)
    monkeypatch.setattr(ocr, 'page_text', lambda path, n: 'recognized article 123')
    assert idx.text(1) == ('recognized article 123', 'ocr')
    # A second call reads the same OCR cache, even if the engine is unavailable.
    monkeypatch.setattr(ocr, 'page_text', lambda *args: (_ for _ in ()).throw(AssertionError('must use cache')))
    assert idx.text(1) == ('recognized article 123', 'ocr')


def test_read_pdf_small_structure_and_false_extension(client):
    c, root = client
    _catalog(root / 'actual.doc')
    r = c.post('/read_pdf', json={'path':'actual.doc','visual_mode':'off'} )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out['content_complete'] is True
    assert out['pages_read'] == [1,2,3]
    assert out['pages'][0]['layout']['blocks'][0]['kind'] == 'text'
    assert out['pages'][0]['layout']['coordinate_system'] == 'pdf_points_top_left'


def test_read_pdf_large_index_then_exhaustive_batches(client):
    import pymupdf
    c, root = client
    d=pymupdf.open()
    for n in range(12):
        d.new_page().insert_text((50,72), f'Page {n+1}: delivery requirements and payment terms')
    d.save(root/'large.pdf'); d.close()
    out=c.post('/read_pdf',json={'path':'large.pdf','visual_mode':'off'}).json()
    assert out['mode']=='index' and not out['content_complete'] and out['pages_read']==[]
    payload={'path':'large.pdf','mode':'full','visual_mode':'off','max_pages':5}
    seen=[]
    for _ in range(4):
        result=c.post('/read_pdf',json=payload)
        assert result.status_code==200,result.text
        result=result.json();seen.extend(result['pages_read'])
        if not result['next_cursor']: break
        payload.update(result['next_cursor'])
    assert seen==list(range(1,13))
    assert not result['content_complete']  # a final batch alone is not the whole document


def test_read_pdf_text_cursor_has_no_gaps(client):
    c, root = client
    _catalog(root/'cat.pdf')
    payload={'path':'cat.pdf','pages':'1,3','max_chars':20,'include_tables':False,
             'include_layout':False,'visual_mode':'off'}
    chunks={1:[],3:[]}
    for _ in range(30):
        r=c.post('/read_pdf',json=payload)
        assert r.status_code==200,r.text
        out=r.json()
        for page in out['pages']:chunks[page['page']].append(page['text'])
        if not out['next_cursor']: break
        payload.update(out['next_cursor'])
    from app.document_index import get_index
    idx=get_index('cat.pdf')
    assert ''.join(chunks[1])==idx.text(1)[0]
    assert ''.join(chunks[3])==idx.text(3)[0]


def test_read_pdf_visual_budget_and_failure_are_explicit(client,monkeypatch):
    c,root=client; _catalog(root/'cat.pdf')
    from app.document_tool.pdf import vision
    calls=[]
    def view(*args,**kwargs):
        calls.append(args[1]);return {'answer':'visual data','cached':False,'model':'fake-vl','usage':{'total_tokens':5}}
    monkeypatch.setattr(vision,'view_page',view)
    payload={'path':'cat.pdf','visual_mode':'required','max_visual_pages':1}
    out=c.post('/read_pdf',json=payload).json()
    assert calls==[1] and out['visual_attempts']==1
    assert out['pages'][0]['visual']['answer']=='visual data'
    assert 'visual_budget_exhausted' in out['pages'][1]['warnings'][0]
    assert not out['content_complete']
    monkeypatch.setattr(vision,'view_page',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('missing config')))
    out=c.post('/read_pdf',json=payload).json()
    assert out['pages'][0]['warnings'][0].startswith('visual_unavailable')


def test_pdf_region_render_and_vision_cache(client,monkeypatch):
    c,root=client;_catalog(root/'cat.pdf')
    from app.document_tool.pdf import read,vision
    import pymupdf
    full=read.render_png(root/'cat.pdf',1,72)
    crop=read.render_png(root/'cat.pdf',1,72,[0,0,200,150])
    pix=pymupdf.Pixmap(crop)
    assert pix.width==200 and pix.height==150
    import pytest
    with pytest.raises(ValueError):read.render_png(root/'cat.pdf',1,72,[-1,0,20,20])
    calls=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'message':{'content':'Visible 24 V'}}],'usage':{'total_tokens':8}}
    def post(*a,**kw):calls.append(kw['json']);return Response()
    monkeypatch.setattr(vision,'_vl_config',lambda:('https://example.invalid/v1','fake','fake-vl'))
    monkeypatch.setattr(vision.httpx,'post',post)
    # Call directly: monkeypatching shared httpx.post must not affect TestClient.
    first=vision.view_page('cat.pdf',1,'voltage',[0,0,200,150],'high')
    second=vision.view_page('cat.pdf',1,'voltage',[0,0,200,150],'high')
    assert not first['cached'] and second['cached'] and len(calls)==1
    assert calls[0]['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/png;base64,')
    vision.view_page('cat.pdf',1,'voltage',None,'low')
    assert len(calls)==2


def test_read_pdf_auto_vision_and_layout_cache(client,monkeypatch):
    c,root=client;_catalog(root/'cat.pdf')
    from app.document_tool.pdf import read,vision
    calls=[]; layouts=[]
    original=read.page_structure
    def structure(*a,**k):
        layouts.append(a[1]); result=original(*a,**k);result['visual_recommended']=True;return result
    monkeypatch.setattr(read,'page_structure',structure)
    monkeypatch.setattr(vision,'view_page',lambda *a,**k: (calls.append(a[1]) or {'answer':'diagram','cached':True}))
    payload={'path':'cat.pdf','pages':'1','visual_mode':'auto'}
    for _ in range(2):
        r=c.post('/read_pdf',json=payload)
        assert r.status_code==200,r.text
        assert r.json()['pages'][0]['visual']['answer']=='diagram'
    assert layouts==[1] and calls==[1,1]


def test_read_pdf_table_has_coordinates_and_values(client):
    import pymupdf
    c,root=client
    d=pymupdf.open();p=d.new_page(width=500,height=300)
    for y in [50,80,110,140]:p.draw_line((40,y),(460,y))
    for x in [40,220,460]:p.draw_line((x,50),(x,140))
    for y,a,b in [(70,'Article','TEST-001'),(100,'Coil','24 V DC'),(130,'Contacts','10 A')]:
        p.insert_text((50,y),a);p.insert_text((230,y),b)
    d.save(root/'table.pdf');d.close()
    r=c.post('/read_pdf',json={'path':'table.pdf','visual_mode':'off'})
    assert r.status_code==200,r.text
    page=r.json()['pages'][0]
    columns = page['layout']['tables'][0]['columns']
    assert columns == 2 or any('table_grid_ambiguous' in w for w in page['warnings'])
    if columns != 2:
        assert not page['complete'] and not r.json()['content_complete']
    assert len(page['layout']['tables'][0]['bbox'])==4
    assert '[TABLES:' in page['text'] and '24 V DC' in page['text']


def test_legacy_and_new_readers_share_structure_cache(client,monkeypatch):
    c,root=client;_catalog(root/'cat.pdf')
    from app.document_tool.pdf import read
    calls=[]; original=read.page_structure
    def structure(*args,**kwargs):
        calls.append((args[1],args[2]));return original(*args,**kwargs)
    monkeypatch.setattr(read,'page_structure',structure)
    legacy=c.post('/read_pages',json={'path':'cat.pdf','pages':'1'})
    modern=c.post('/read_pdf',json={'path':'cat.pdf','pages':'1','visual_mode':'off'})
    again=c.post('/read_pages',json={'path':'cat.pdf','pages':'1'})
    assert legacy.status_code==modern.status_code==again.status_code==200
    assert calls==[(1,True)]


def test_structure_cache_upgrades_without_losing_table_policy(client,monkeypatch):
    c,root=client;_catalog(root/'cat.pdf')
    from app.document_index import get_index
    from app.document_tool.pdf import read
    calls=[];original=read.page_structure
    def structure(path,n,include_tables):
        calls.append(include_tables);return original(path,n,include_tables)
    monkeypatch.setattr(read,'page_structure',structure)
    idx=get_index('cat.pdf')
    idx.read_page(1,include_tables=False)
    idx.read_page(1,include_tables=True)
    assert idx.read_page(1,include_tables=False)['tables']==[]
    idx.read_page(1,include_tables=True)
    assert calls==[False,True]
    assert list((idx.cache/'structure').glob('*.json'))
    assert not (idx.cache/'tables').exists() and not (idx.cache/'layout').exists()


def test_upload_reuses_reader_cache_without_vision_and_respects_table_flag(client,monkeypatch):
    c,root=client;_catalog(root/'cat.pdf')
    from app.document_tool.pdf import read,vision
    from app.integrations.open_webui import loader as owui_loader
    calls=[];original=read.page_structure
    def structure(path,n,include_tables):
        calls.append(n);out=original(path,n,include_tables)
        out['tables']=[{'id':'table-1','bbox':[1,1,2,2],'rows':1,'columns':1,'markdown':'TABLE_SENTINEL'}]
        out['warnings']=['table_grid_ambiguous: test']
        return out
    monkeypatch.setattr(read,'page_structure',structure)
    monkeypatch.setattr(vision,'view_page',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('uploads must not call vision')))
    data=(root/'cat.pdf').read_bytes()
    first=owui_loader.process_upload(data,'cat.pdf')
    second=owui_loader.process_upload(data,'renamed.pdf')
    disabled=owui_loader.process_upload(data,'cat.pdf',include_tables=False)
    assert calls==[1,2,3]
    assert 'TABLE_SENTINEL' in first[0]['page_content'] and 'TABLE_SENTINEL' in second[0]['page_content']
    assert 'TABLE_SENTINEL' not in disabled[0]['page_content']
    assert first[0]['metadata']['warnings']=='table_grid_ambiguous: test'


def test_shared_page_cache_recovers_after_corruption(client,monkeypatch):
    c,root=client;_catalog(root/'cat.pdf')
    from app.document_index import get_index
    idx=get_index('cat.pdf');idx.read_page(1)
    cache=idx.cache/'structure'/'1.json'
    cache.write_text('{broken',encoding='utf-8')
    assert idx.read_page(1)['structure']['width']>0
    import json
    assert json.loads(cache.read_text(encoding='utf-8'))['width']>0
