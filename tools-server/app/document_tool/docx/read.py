"""Read DOCX in OOXML order. Parts are logical chunks, never physical pages."""
from __future__ import annotations

import hashlib
import posixpath
import re
import zipfile
from urllib.parse import unquote
from pathlib import Path
from lxml import etree as ET

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
V='urn:schemas-microsoft-com:vml'
NS={'w':W,'r':R,'a':A,'v':V}
PART_CHARS=4000
MAX_XML_BYTES=64*1024*1024


def xml(zf,name):
    try:info=zf.getinfo(name)
    except KeyError:return None
    if info.file_size > MAX_XML_BYTES:raise ValueError(f'DOCX XML part exceeds limit: {name}')
    data=zf.read(name)
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:raise ValueError('DOCX XML entity declarations are unsupported')
    return ET.fromstring(data,ET.XMLParser(resolve_entities=False,no_network=True,load_dtd=False,remove_comments=True,remove_pis=True))


def text_of(node):
    chunks=[]
    for item in node.iter():
        tag=ET.QName(item).localname
        if item.tag in {f'{{{W}}}t','{http://schemas.openxmlformats.org/officeDocument/2006/math}t'}:chunks.append(item.text or '')
        elif item.tag==f'{{{W}}}delText':chunks.append('[DELETED: '+(item.text or '')+']')
        elif tag in {'footnoteReference','endnoteReference'}:chunks.append(f'[{tag}:{item.get(chr(123)+W+chr(125)+"id","")}]')
        elif item.tag in {f'{{{W}}}tab',f'{{{W}}}br',f'{{{W}}}cr'}:chunks.append('\n' if tag!='tab' else '\t')
    return ''.join(chunks)


def _body_nodes(parent):
    for child in parent:
        tag=ET.QName(child).localname
        if tag in {'p','tbl'}:yield child
        elif tag not in {'sectPr','bookmarkStart','bookmarkEnd'}:yield from _body_nodes(child)


def _refs(zf,part,node):
    ids=node.xpath('.//a:blip/@r:embed | .//a:blip/@r:link | .//v:imagedata/@r:id',namespaces=NS)
    if not ids:return []
    cache=getattr(zf,'_docx_relationships',None)
    if cache is None:cache={};zf._docx_relationships=cache
    if part not in cache:
        rel_name=posixpath.join(posixpath.dirname(part),'_rels',posixpath.basename(part)+'.rels')
        relationships=xml(zf,rel_name)
        cache[part]={r.get('Id'):r for r in relationships} if relationships is not None else {}
    by_id=cache[part];out=[]
    for rid in dict.fromkeys(ids):
        rel=by_id.get(rid)
        if rel is None:continue
        external=rel.get('TargetMode')=='External'
        raw=unquote(rel.get('Target',''))
        target=posixpath.normpath(raw.lstrip('/') if raw.startswith('/') else posixpath.join(posixpath.dirname(part),raw))
        valid=not external and not target.startswith(('../','/')) and target in zf.namelist()
        out.append({'id':'image-'+hashlib.sha256((part+':'+rid).encode()).hexdigest()[:12],
                    'target':target if valid else None,'available':valid,'source_part':part})
    return out


def extract_parts(path:Path):
    blocks=[]; warnings=[];section=1;heading='';table_number=0
    with zipfile.ZipFile(path) as zf:
        if sum(i.file_size for i in zf.infolist()) > 256*1024*1024:raise ValueError('DOCX expanded package exceeds 256 MiB')
        if 'word/document.xml' not in zf.namelist():raise ValueError('Not a DOCX document')
        styles=xml(zf,'word/styles.xml'); levels={}
        if styles is not None:
            for style in styles:
                lvl=style.find('w:pPr/w:outlineLvl',NS)
                if lvl is not None:levels[style.get(f'{{{W}}}styleId')]=int(lvl.get(f'{{{W}}}val','0'))+1
        parts=['word/document.xml']+sorted(n for n in zf.namelist() if re.fullmatch(r'word/(header\d+|footer\d+|footnotes|endnotes|comments)\.xml',n))
        for part in parts:
            root=xml(zf,part)
            container=root.find('w:body',NS) if part=='word/document.xml' else root
            if root.xpath('.//w:instrText | .//w:fldSimple',namespaces=NS):warnings.append('field_results_are_saved_not_recalculated')
            if root.xpath('.//*[local-name()="oMath"]'):warnings.append('math_layout_requires_visual_review')
            if root.xpath('.//w:altChunk | .//w:object | .//w:txbxContent',namespaces=NS):warnings.append('embedded_objects_or_textboxes_require_visual_review')
            if root.xpath('.//w:tbl/w:tr/w:tc/w:tbl',namespaces=NS):warnings.append('nested_table_grid_flattened; inspect rendered page if needed')
            if root.xpath('.//w:ins | .//w:del',namespaces=NS):warnings.append('tracked_changes_present; deleted_text_marked_explicitly')
            if root.xpath('.//*[local-name()="chart" or local-name()="relIds"]'):warnings.append('charts_or_smartart_require_rendered_page_review')
            if container is None:continue
            nodes=list(_body_nodes(container))
            for node in nodes:
                tag=ET.QName(node).localname
                if tag not in {'p','tbl'}:
                    if tag not in {'sectPr','bookmarkStart','bookmarkEnd'}:warnings.append('unsupported_body_block: '+tag)
                    continue
                refs=_refs(zf,part,node)
                if any(not r['available'] for r in refs):warnings.append('external_or_missing_image_not_loaded')
                if tag=='p':
                    text=text_of(node)
                    style_node=node.find('w:pPr/w:pStyle',NS)
                    style=style_node.get(f'{{{W}}}val','Normal') if style_node is not None else 'Normal'
                    level=levels.get(style)
                    if level is None:
                        match=re.search(r'(?:heading|заголовок)\s*([1-9])',style,re.I)
                        level=int(match.group(1)) if match else None
                    kind='heading' if level is not None or style=='Title' else 'paragraph'
                    if node.find('w:pPr/w:numPr',NS) is not None or style.startswith('List'):kind='list_item'
                    if kind=='heading':heading=text[:120]
                    block={'id':f'b{len(blocks)+1}','kind':kind,'text':text,'style':style,'level':level,'images':refs,
                           'section':section,'heading':heading,'source_part':part}
                    parent=node.getparent()
                    if parent is not None and parent.get(f'{{{W}}}id') is not None:
                        block['note_id']=parent.get(f'{{{W}}}id')
                    blocks.append(block)
                    if node.find('w:pPr/w:sectPr',NS) is not None:section+=1
                else:
                    table_number+=1
                    for row_n,row in enumerate(node.findall('w:tr',NS),1):
                        cells=[];spans=[]
                        for tc in row.findall('w:tc',NS):
                            cells.append(text_of(tc))
                            span=tc.find('w:tcPr/w:gridSpan',NS); merge=tc.find('w:tcPr/w:vMerge',NS)
                            spans.append({'columns':int(span.get(f'{{{W}}}val','1')) if span is not None else 1,
                                          'vertical_merge':merge.get(f'{{{W}}}val','continue') if merge is not None else None})
                        blocks.append({'id':f'b{len(blocks)+1}','kind':'table_row','table_id':f'table-{table_number}',
                            'row':row_n,'text':' | '.join(cells),'cells':cells,'spans':spans,
                            'header':row.find('w:trPr/w:tblHeader',NS) is not None,'images':_refs(zf,part,row),
                            'section':section,'heading':heading,'source_part':part})
    # Keep paragraph/table-row order. Oversized blocks are split, with offsets for reconstruction.
    records=[];current=[];size=0
    def flush():
        nonlocal current,size
        if not current:return
        text='\n'.join(f"[{b['id']} {b['kind']}] {b['text']}" for b in current)
        images={r['id']:r for b in current for r in b.get('images',[])}
        meta=[]
        for b in current:
            brief={k:v for k,v in b.items() if k not in {'text','cells','images'}}
            brief['preview']=b['text'][:160];brief['image_ids']=[r['id'] for r in b.get('images',[])]
            if 'cells' in b:brief['cell_previews']=[c[:80] for c in b['cells'][:30]]
            meta.append(brief)
        records.append({'text':text,'chars':len(text),'broken':False,'empty':not text.strip(),
            'images':len(images),'image_refs':list(images.values()),'blocks':meta,
            'warnings':list(dict.fromkeys(warnings)),'unit':'logical_part'})
        current=[];size=0
    for block in blocks:
        text=block['text']
        for offset in range(0,max(1,len(text)),PART_CHARS):
            piece={**block,'text':text[offset:offset+PART_CHARS],'text_offset':offset,'continued':len(text)>PART_CHARS}
            if current and (size+len(piece['text'])>PART_CHARS or len(current)>=25):flush()
            current.append(piece);size+=len(piece['text'])
    flush()
    return records or [{'text':'','chars':0,'broken':False,'empty':True,'images':0,'image_refs':[],'blocks':[],'warnings':warnings,'unit':'logical_part'}]


def extract_text(path:Path):
    return '\n\n'.join(p['text'] for p in extract_parts(path))


def image_bytes(path:Path,ref:dict):
    if not ref.get('available') or not ref.get('target'):raise ValueError('Image is external or unavailable')
    with zipfile.ZipFile(path) as zf:
        info=zf.getinfo(ref['target'])
        if info.file_size>20*1024*1024:raise ValueError('Embedded image exceeds 20 MiB')
        return zf.read(info)
