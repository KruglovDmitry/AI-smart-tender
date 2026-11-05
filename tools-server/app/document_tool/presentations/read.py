from pathlib import Path
import zipfile
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

def source(path):
    from ..sniff import detect_extension
    if detect_extension(path) == '.pptx':
        return path
    from ..office import convert
    from ...document_index import INDEX_ROOT
    # A content key avoids using an unrelated converted file after source changes.
    import hashlib
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return convert(path, INDEX_ROOT / ('office-' + digest), '.pptx')

def load(path):
    work = source(path)
    with zipfile.ZipFile(work) as package:
        if sum(i.file_size for i in package.infolist()) > 256 * 1024 * 1024:
            raise ValueError('Presentation expanded package exceeds 256 MiB')
        if any(i.file_size > 64 * 1024 * 1024 for i in package.infolist()):
            raise ValueError('Presentation member exceeds 64 MiB')
    return Presentation(work)

def shapes(items):
    for shape in items:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from shapes(shape.shapes)

def extract_parts(path):
    prs = load(path)
    parts = []
    for number, slide in enumerate(prs.slides, 1):
        blocks, refs, warnings, texts = [], [], [], []
        for shape in shapes(slide.shapes):
            block = {'id': f'slide-{number}-shape-{shape.shape_id}', 'name': shape.name,
                     'kind': 'shape', 'bbox_emu': [shape.left, shape.top, shape.width, shape.height]}
            if shape.has_text_frame:
                block.update(kind='text', paragraphs=[{'text': p.text, 'level': p.level} for p in shape.text_frame.paragraphs])
                if shape.text_frame.text:
                    texts.append(shape.text_frame.text)
            if shape.has_table:
                rows = []
                for row in shape.table.rows:
                    cells = [{'text': c.text, 'merge_origin': c.is_merge_origin, 'spanned': c.is_spanned,
                              'span_width': c.span_width, 'span_height': c.span_height} for c in row.cells]
                    rows.append(cells)
                    texts.append(' | '.join(c['text'] for c in cells))
                block.update(kind='table', rows=rows)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                image_id = f'slide-{number}-image-{shape.shape_id}'
                refs.append({'id': image_id, 'shape_id': shape.shape_id, 'available': True, 'source_part': f'slide-{number}'})
                block.update(kind='image', image_id=image_id)
            if shape.has_chart:
                block['kind'] = 'chart'
                warnings.append('chart_requires_rendered_slide_visual_review')
                try:
                    chart = shape.chart
                    if chart.has_title:
                        texts.append(chart.chart_title.text_frame.text)
                    for series in chart.series:
                        texts.append(str(series.name) + ': ' + ', '.join(str(v) for v in series.values))
                except (AttributeError, ValueError):
                    warnings.append('chart_data_not_fully_extracted')
            if shape.shape_type in {MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT, MSO_SHAPE_TYPE.LINKED_OLE_OBJECT, MSO_SHAPE_TYPE.MEDIA}:
                warnings.append('embedded_object_requires_manual_review')
            if shape._element.xpath('.//*[local-name()="relIds"]'):
                warnings.append('smartart_requires_rendered_slide_visual_review')
            blocks.append(block)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                texts.append('[Speaker notes]\n' + notes.text)
                blocks.append({'kind': 'speaker_notes', 'text': notes.text})
        if refs:
            warnings.append('slide_images_require_visual_review')
        text = '\n\n'.join(texts)
        # Coordinates are EMU in the parent group, not guaranteed visual reading order.
        parts.append({'text': text, 'chars': len(text), 'broken': False, 'empty': not text.strip(),
            'images': len(refs), 'image_refs': refs, 'blocks': blocks,
            'warnings': list(dict.fromkeys(warnings)), 'unit': 'slide',
            'slide_size_emu': [prs.slide_width, prs.slide_height]})
    if not parts:
        raise ValueError('Presentation has no slides')
    return parts

def extract_text(path):
    return '\n\n'.join(f'[Slide {n}]\n' + part['text'] for n, part in enumerate(extract_parts(path), 1))

def image_bytes(path, slide_number, image_id):
    prs = load(path)
    if not 1 <= slide_number <= len(prs.slides):
        raise ValueError('Slide out of range')
    for shape in shapes(prs.slides[slide_number - 1].shapes):
        if f'slide-{slide_number}-image-{shape.shape_id}' == image_id and shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            data = shape.image.blob
            if len(data) > 20 * 1024 * 1024:
                raise ValueError('Embedded presentation image exceeds 20 MiB')
            return data
    raise ValueError('Presentation image ID not found')
