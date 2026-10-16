FROM python:3.13-slim

# tzdata serve perche' TZ abbia effetto sugli orari stampati nei log
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .

# app.py legge e scrive searches.tracked e telegram_api_credentials nella cartella corrente:
# lavorando in /data quei file finiscono nel volume montato
WORKDIR /data
VOLUME /data

CMD ["python", "/app/app.py"]
