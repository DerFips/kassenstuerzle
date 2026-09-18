FROM python:3.11-slim
WORKDIR /app

# SQLCipher-Pakete kommen aus deb.debian.org/debian (main) - explizit sicherstellen
RUN echo "deb http://deb.debian.org/debian bookworm main" > /etc/apt/sources.list && \
    echo "deb http://deb.debian.org/debian-security bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb http://deb.debian.org/debian bookworm-updates main" >> /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-deu \
    ghostscript \
    poppler-utils \
    libsqlcipher-dev \
    libsqlcipher0 \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Datenverzeichnis anlegen
RUN mkdir -p /app/data

# Umgebungsvariablen (Defaults; per -e oder .env überschreibbar)
ENV DATABASE_PATH=/app/data/kassenstuerzle.db
ENV HTTPS=0

EXPOSE 5000

# Healthcheck: prüft alle 30s ob die App antwortet
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/login')" || exit 1

# Gunicorn mit 1 Worker (Single-User-App; Flask-Limiter memory:// funktioniert korrekt)
CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
