"""Shared bounded raster preparation for embedded and standalone images."""
import io
from PIL import Image, ImageOps

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp', '.gif'}
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_FRAMES = 200

def open_image(source):
    image = Image.open(source)
    if image.width * image.height > MAX_PIXELS:
        image.close()
        raise ValueError('Image exceeds 40 million pixels; use a smaller source image')
    return image

def prepare_png(data, detail='auto', frame=1, bbox=None):
    if detail not in {'auto', 'low', 'high'}:
        raise ValueError('Invalid image detail')
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Image exceeds 64 MiB')
    with open_image(io.BytesIO(data)) as source:
        if not 1 <= frame <= getattr(source, 'n_frames', 1):
            raise ValueError('Image frame out of range')
        source.seek(frame - 1)
        if source.width * source.height > MAX_PIXELS:
            raise ValueError('Image frame exceeds pixel limit')
        oriented = ImageOps.exif_transpose(source)
        if 'A' in oriented.getbands() or 'transparency' in oriented.info:
            # Dropping alpha turns transparent black into a black background and loses dark labels.
            rgba = oriented.convert('RGBA')
            image = Image.new('RGB', rgba.size, 'white')
            image.paste(rgba, mask=rgba.getchannel('A'))
        else:
            image = oriented.convert('RGB')
        if bbox is not None:
            if len(bbox) != 4:
                raise ValueError('bbox must be [left, top, right, bottom] in oriented image pixels')
            left, top, right, bottom = bbox
            if not 0 <= left < right <= image.width or not 0 <= top < bottom <= image.height:
                raise ValueError('Image bbox outside bounds')
            image = image.crop((left, top, right, bottom))
        image.thumbnail((1024, 1024) if detail == 'low' else (4096, 4096))
        out = io.BytesIO()
        image.save(out, format='PNG')
        return out.getvalue()
