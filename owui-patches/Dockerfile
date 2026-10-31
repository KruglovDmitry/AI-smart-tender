# Open WebUI для Docker-варианта — та же версия и те же правки, что у OpenWebUI.exe в desktop-сборке.
# Версию держать равной "open_webui" в desktop/versions.json.
ARG OPEN_WEBUI_VERSION=0.12.2
FROM ghcr.io/open-webui/open-webui:v${OPEN_WEBUI_VERSION}

COPY backend_patches.py /tmp/backend_patches.py
RUN python /tmp/backend_patches.py /app/backend/open_webui && rm /tmp/backend_patches.py

# Многоязычная модель векторов для «Знаний» внутри образа (как resources/models в desktop): путь вместо имени —
# Open WebUI ничего не скачивает при старте и работает без доступа к Hugging Face.
RUN HF_HUB_OFFLINE=0 python -c "from sentence_transformers import SentenceTransformer as S; \
S('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2').save('/app/models/paraphrase-multilingual-MiniLM-L12-v2')"
