from ..raster import open_image, MAX_IMAGE_BYTES, MAX_FRAMES, MAX_PIXELS

def extract_parts(path):
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError('Image exceeds 64 MiB')
    with open_image(path) as image:
        count = getattr(image, 'n_frames', 1)
        if count > MAX_FRAMES:
            raise ValueError('Image exceeds 200 frames; split the scan first')
        parts = []
        for frame in range(1, count + 1):
            image.seek(frame - 1)
            if image.width * image.height > MAX_PIXELS:
                raise ValueError('Image frame exceeds 40 million pixels')
            # The primary text is deliberately empty: dimensions are not OCR content.
            parts.append({'text': '', 'chars': 0, 'broken': False, 'empty': True, 'images': 1,
                'unit': 'image_frame', 'image_refs': [], 'warnings': ['image_content_requires_visual_review'],
                'blocks': [{'kind': 'image', 'frame': frame, 'width': image.width, 'height': image.height,
                            'format': image.format, 'orientation': image.getexif().get(274, 1)}]})
        return parts
