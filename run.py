import logging
import logging.handlers
import os

try:
    from dotenv import load_dotenv
    load_dotenv()  # lädt .env für lokale Entwicklung; in Docker via env_file gesetzt
except ImportError:
    pass

from app import create_app

def _setup_logging():
    log_level = getattr(logging, os.environ.get('LOG_LEVEL', 'INFO').upper(), logging.INFO)
    fmt = '%(asctime)s %(levelname)-8s [%(name)s] %(message)s'
    handlers = [logging.StreamHandler()]  # stdout → Docker/gunicorn fängt es auf

    # Optionale Datei-Rotation (max 5 MB, 3 Backups)
    log_file = os.environ.get('LOG_FILE', '')
    if log_file:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
            )
        )

    logging.basicConfig(level=log_level, format=fmt, handlers=handlers)

    # Werkzeug-Access-Log auf WARNING reduzieren (Gunicorn loggt selbst)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)

_setup_logging()
app = create_app()

if __name__ == '__main__':
    # Entwicklungs-Server – für Produktion: gunicorn --config gunicorn.conf.py run:app
    app.run(host='127.0.0.1', port=5000, debug=False)
