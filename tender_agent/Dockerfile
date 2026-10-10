FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV TENDER_AGENT_DATA_DIR=/data/tenders
ENV TENDER_AGENT_HEADLESS=true

EXPOSE 8010

CMD ["uvicorn", "tender_agent.server:app", "--host", "0.0.0.0", "--port", "8010"]
