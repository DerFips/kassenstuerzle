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

# Befund I10: requirements.lock ist der verbindliche, vollstaendig gepinnte und
# hashgesicherte Stand (erzeugen mit `make requirements.lock`, braucht
# Netzzugang). Die eckige Klammer ist der uebliche Kniff fuer ein OPTIONALES
# COPY: passt das Muster auf nichts, bleibt die Datei einfach weg - COPY
# scheitert nicht, weil requirements.txt immer trifft.
COPY requirements.txt requirements.loc[k] ./
RUN pip install --no-cache-dir --upgrade pip && \
    if [ -f requirements.lock ]; then \
        pip install --no-cache-dir -r requirements.lock; \
    else \
        echo "HINWEIS: requirements.lock fehlt - Build ist nicht reproduzierbar (Befund I10)." >&2; \
        pip install --no-cache-dir -r requirements.txt; \
    fi

COPY . .

# Datenverzeichnis anlegen
RUN mkdir -p /app/data

# Sicherheit (Befund I2): nicht als root laufen. Eigener, unprivilegierter
# Benutzer, dem das Arbeits- und Datenverzeichnis gehört (nötig, damit er
# Datenbank/Backups/Belege/Secret-Key im Bind-Mount ./data:/app/data anlegen
# und lesen kann).
RUN groupadd -r kassenstuerzle && \
    useradd -r -g kassenstuerzle -d /app -s /usr/sbin/nologin kassenstuerzle && \
    chown -R kassenstuerzle:kassenstuerzle /app
USER kassenstuerzle

# Umgebungsvariablen (Defaults; per -e oder .env überschreibbar)
ENV DATABASE_PATH=/app/data/kassenstuerzle.db
# Sicherheit (Befund I7): HTTPS=1, damit das Sitzungscookie das Secure-Flag
# bekommt. Hier stand vorher HTTPS=0 und docker-compose.yml überschrieb es
# nicht - die Sitzungscookies gingen also ohne Secure-Flag hinaus und waren
# auf jedem Klartext-Weg zum Container mitlesbar. Der Container läuft
# ausschließlich hinter dem TLS-terminierenden Reverse-Proxy; wer bewusst
# reines HTTP fahren will, setzt HTTPS=0 in seiner .env.
ENV HTTPS=1
# Rückfall-Kennzeichen für app/__init__.py:_laeuft_im_container(): setzt der
# Betreiber HTTPS gar nicht, gilt im Container trotzdem "Secure", bei einem
# lokalen `python run.py` dagegen nicht (sonst sperrt das Secure-Cookie die
# Entwicklung über http://127.0.0.1 aus).
ENV KASSENSTUERZLE_IM_CONTAINER=1

EXPOSE 5000

# Healthcheck: prüft alle 30s ob die App antwortet
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/login')" || exit 1

# Gunicorn mit 1 Worker (Single-User-App; Flask-Limiter memory:// funktioniert korrekt)
CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
