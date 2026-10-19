# Библиотека ai_vision (репозиторий AI-vision) для запасного шага по скриншотам.
# docker compose подставляет её как дополнительный контекст сборки «ai_vision»
# (build.additional_contexts). Без него этот этап пустой и образ собирается без зрения.
FROM scratch AS ai_vision

FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

# Корень Минцифры: zakupki.gov.ru выпускает сертификат от Russian Trusted Root CA,
# которого нет в хранилище Chromium. Доверяем только этому корню.
COPY certs/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/russian_trusted_root_ca.crt
RUN apt-get update \
 && apt-get install -y --no-install-recommends libnss3-tools \
 && rm -rf /var/lib/apt/lists/* \
 && update-ca-certificates \
 && mkdir -p /root/.pki/nssdb /root/.local/share/pki/nssdb \
 && certutil --empty-password -d sql:/root/.pki/nssdb -N \
 && certutil -d sql:/root/.pki/nssdb -A -t "C,," -n "Russian Trusted Root CA" -i /usr/local/share/ca-certificates/russian_trusted_root_ca.crt \
 && certutil --empty-password -d sql:/root/.local/share/pki/nssdb -N \
 && certutil -d sql:/root/.local/share/pki/nssdb -A -t "C,," -n "Russian Trusted Root CA" -i /usr/local/share/ca-certificates/russian_trusted_root_ca.crt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY --from=ai_vision / /opt/ai-vision/
RUN if [ -f /opt/ai-vision/pyproject.toml ]; then pip install --no-cache-dir /opt/ai-vision; \
    else echo "ai_vision не передан в сборку: vision-шаг будет недоступен"; fi

ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV TENDER_AGENT_DATA_DIR=/data/tenders
ENV TENDER_AGENT_HEADLESS=true

EXPOSE 8010

CMD ["uvicorn", "tender_agent.server:app", "--host", "0.0.0.0", "--port", "8010"]
