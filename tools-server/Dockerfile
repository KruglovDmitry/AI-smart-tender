FROM python:3.12-slim-bookworm

WORKDIR /app

# antiword/catdoc — старые .doc; bsdtar (libarchive) — rar и 7z, GNU tar их не читает.
RUN apt-get update \
    && apt-get install -y --no-install-recommends antiword catdoc libarchive-tools \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Модели OCR (rapidocr, кириллица) скачиваются при первом создании движка — делаем это при сборке,
# чтобы OCR работал и на сервере без интернета.
RUN DATA_ROOT=/tmp/ocr-warmup python -c "from app.doc_index import ocr; assert ocr.available(), ocr._unavailable" \
    && rm -rf /tmp/ocr-warmup

ENV DATA_ROOT=/data
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
