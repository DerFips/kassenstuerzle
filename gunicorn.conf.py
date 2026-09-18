"""
Gunicorn Konfiguration für Kassenstürzle
=========================================
1 Worker: Flask-Limiter (memory://) und APScheduler funktionieren korrekt.
Für Multi-Worker-Betrieb: Flask-Limiter auf Redis-Backend umstellen.
"""
import os
import multiprocessing

# ── Server ────────────────────────────────────────────────────────────────────
bind      = os.environ.get("GUNICORN_BIND", "0.0.0.0:5000")
# Single-User-App: 1 Worker reicht; Rate-Limits und In-Memory-State bleiben korrekt
workers   = int(os.environ.get("GUNICORN_WORKERS", "1"))
threads   = int(os.environ.get("GUNICORN_THREADS", "4"))   # async innerhalb eines Workers
worker_class = "sync"

# ── Timeouts ──────────────────────────────────────────────────────────────────
timeout       = int(os.environ.get("GUNICORN_TIMEOUT", "120"))   # OCR/PDF kann länger dauern
keepalive     = 5
graceful_timeout = 30

# ── Logging ───────────────────────────────────────────────────────────────────
accesslog  = "-"           # stdout → Docker/systemd logs fangen das auf
errorlog   = "-"           # stderr
loglevel   = os.environ.get("LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(D)sµs'

# ── Sicherheit ────────────────────────────────────────────────────────────────
limit_request_line   = 4096   # Max. URL-Länge in Bytes
limit_request_fields = 100    # Max. HTTP-Header-Felder
forwarded_allow_ips  = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

# ── Prozess ───────────────────────────────────────────────────────────────────
# WICHTIG: preload_app MUSS False sein, da die App einen APScheduler-Hintergrund-
# Thread startet (Push-Erinnerungen). Threads überleben einen fork() nicht - bei
# preload_app=True würde der Scheduler nur im (nicht bedienenden) Master-Prozess
# laufen, nie in den tatsächlichen Workern. Bei 1 Worker kostet preload_app=False
# ohnehin nichts (der "geteilter Speicher"-Vorteil greift erst ab mehreren Workern).
preload_app = False
daemon      = False   # Im Vordergrund laufen (Docker-Standard)
