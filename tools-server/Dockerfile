FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

# Legacy Office extractors (.doc) + rebuild tools metadata
RUN apt-get update \
    && apt-get install -y --no-install-recommends antiword catdoc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DATA_ROOT=/data
ENV PYTHONUNBUFFERED=1
ENV BROWSER_HEADLESS=true
ENV BROWSER_PROFILE_DIR=/tmp/tender-browser-profile
# browsers already in the base image
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
