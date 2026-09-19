from flask import Flask
import os
import logging


def _ensure_data_dir_and_secret(data_dir):
    """Legt das Datenverzeichnis und den Signierschlüssel an (falls nötig) und
    liest ihn sonst ein. In eine eigene Funktion ausgelagert, damit sie
    unabhängig von create_app() gegen ein Wegwerf-Verzeichnis testbar ist.

    Sicherheit (Befund I2): das Datenverzeichnis darf nur vom eigenen
    Benutzer lesbar sein - es enthält u.a. diesen Signierschlüssel. Ohne die
    chmod-Aufrufe läge beides mit dem Standard-umask 644/755, jeder
    Lesezugriff auf den Datenträger würde den Flask-Signierschlüssel
    preisgeben und eine Admin-Sitzung fälschbar machen, ganz ohne Passwort.
    Nur chmod'en wenn WIR das Verzeichnis/die Datei gerade neu anlegen: ein
    bereits bestehendes Verzeichnis (z.B. mit absichtlich anderen Rechten
    fürs Bind-Mount) bzw. eine bereits bestehende Datei bleibt unangetastet.
    """
    _log = logging.getLogger(__name__)
    secret_file = os.path.join(data_dir, '.secret_key')

    _data_dir_existed = os.path.isdir(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    if not _data_dir_existed:
        try:
            os.chmod(data_dir, 0o700)
        except OSError as exc:
            _log.warning(f"Konnte Rechte des Datenverzeichnisses nicht auf 0700 setzen: {exc}")

    if os.path.exists(secret_file):
        with open(secret_file, 'rb') as f:
            secret = f.read()
    else:
        secret = os.urandom(32)
        with open(secret_file, 'wb') as f:
            f.write(secret)
        try:
            os.chmod(secret_file, 0o600)
        except OSError as exc:
            _log.warning(f"Konnte Rechte des Secret-Key nicht auf 0600 setzen: {exc}")
    return secret


# Rate-Limits fuer sensible Endpunkte. Bewusst auf Modulebene statt lokal in
# create_app(), damit Tests nachweisen koennen, dass ein Endpunkt hier gelistet
# ist - ein fehlender Eintrag faellt sonst erst im Betrieb auf.
RATE_LIMITS = {
    'main.login':              '10 per minute',
    'main.api_parse_pdf':      '5 per minute',
    'main.api_receipt_parse':  '10 per minute',
    # Befund I5: der Beleg-Upload war der einzige Upload-Endpunkt ohne Limit.
    # Jeder Request legt bis zu 20 MB ab, und weil der Dateiname aus dem
    # Inhalt (MD5) gebildet wird, ergibt jeder neue Inhalt auch eine neue
    # Datei. Ein eingeloggtes Mitglied konnte damit in einer Schleife die
    # Platte fuellen, bis SQLite-Schreibzugriffe und Sicherungen scheitern.
    # Dasselbe Limit wie beim Beleg-Scan nebenan - beide verarbeiten Fotos
    # aus derselben Oberflaeche.
    'main.api_receipt_upload': '10 per minute',
    'main.api_backup_create':  '5 per minute',
    'main.api_backup_restore': '3 per minute',
    'main.import_csv':         '20 per minute',
    'main.api_backup_download':'10 per minute',
    'main.export_csv':         '20 per minute',
}


def _laeuft_im_container() -> bool:
    """Erkennt, ob dieser Prozess in einem Container laeuft (Befund I7).

    Unterschieden wird an Merkmalen, die es nur im Container gibt:
      * `/.dockerenv` legt der Docker-Daemon in jedem Container an,
      * `/run/.containerenv` ist das Gegenstueck von Podman,
      * `KASSENSTUERZLE_IM_CONTAINER=1` setzt unser eigenes Dockerfile
        ausdruecklich - der Rueckfall, falls eine Laufzeit keine der beiden
        Dateien anlegt.
    """
    if os.environ.get('KASSENSTUERZLE_IM_CONTAINER', '').strip() == '1':
        return True
    return os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv')


def _secure_cookie_aktiv() -> bool:
    """Liefert den Wert fuer SESSION_COOKIE_SECURE (Befund I7).

    Frueher stand hier `os.environ.get('HTTPS', '0') == '1'`: der Default war
    UNSICHER. Wer `HTTPS` im Dockerfile oder in der .env vergass - und das
    Dockerfile setzte selbst `HTTPS=0` -, verschickte Sitzungscookies ohne
    `Secure`-Flag. Zusammen mit dem auf alle Interfaces veroeffentlichten Port
    5000 liess sich der TLS-terminierende Reverse-Proxy umgehen und das Cookie
    im Klartext mitlesen.

    Jetzt gilt: `HTTPS` ist die ausdrueckliche Entscheidung des Betreibers und
    hat immer Vorrang - `HTTPS=0` ist der bewusste Ausstieg fuer einen reinen
    Klartext-Aufbau. Ist die Variable NICHT gesetzt, ist der Default "sicher",
    sobald wir in einem Container laufen.

    Warum nicht bedingungslos `True`? Ein `python run.py` auf dem
    Entwicklungsrechner spricht Klartext-HTTP auf 127.0.0.1. Ein Secure-Cookie
    sendet der Browser ueber `http://` nicht zurueck - die Anmeldung wuerde
    endlos auf die Login-Seite zurueckfallen. Deshalb haengt der Default an
    `_laeuft_im_container()`: im Container (dort steht immer der Proxy davor)
    sicher, beim lokalen Lauf nicht. Wer lokal doch mit TLS testet, setzt
    `HTTPS=1`; wer im Container bewusst Klartext fahren will, setzt `HTTPS=0`.
    """
    wert = os.environ.get('HTTPS')
    if wert is not None and wert.strip() != '':
        return wert.strip() == '1'
    return _laeuft_im_container()


def _pruefe_db_verschluesselung() -> None:
    """Bricht den Start ab, wenn Verschluesselung verlangt, aber unmoeglich ist.

    Befund I9: `create_app()` rief `_migrate_to_encrypted()` nur auf, wenn
    `_SQLCIPHER_AVAILABLE` bereits wahr war. Die einzige Warnung fuer den
    Gegenfall steht aber INNERHALB dieser Funktion
    (`app/routes/main.py`, "DB_ENCRYPTION_KEY gesetzt, aber sqlcipher3 nicht
    installiert") - von dieser Aufrufstelle aus konnte sie also nie feuern.
    `_open_db()` ueberspringt `PRAGMA key` ebenfalls kommentarlos. Scheiterte
    bei einem Rebuild die Installation von `sqlcipher3`, lief die App damit
    vollstaendig im Klartext weiter, waehrend der Betreiber die Finanzdaten
    fuer verschluesselt hielt - und nichts an irgendeiner Stelle sagte das.

    Deshalb wird der Fall hier ausdruecklich und AUSSERHALB der Migration
    geprueft, bevor irgendetwas die Datenbank anfasst. Ein fehlgeschlagener
    Start ist lauter und damit harmloser als ein stiller Klartextbetrieb.

    Die Werte werden bewusst ueber das Modul gelesen (`_main._DB_KEY`) statt
    per `from ... import`: nur so sieht die Pruefung den aktuellen Stand, und
    nur so ist sie im Test ueberhaupt nachstellbar.

    Absichtlich eng: nur die Kombination "Schluessel gesetzt, Treiber fehlt"
    ist toedlich. Kein Schluessel und kein Treiber ist ein gueltiger,
    unverschluesselter Aufbau (so laeuft auch die Testsuite) und bleibt still.
    """
    _log = logging.getLogger(__name__)

    from .routes import main as _main
    if not _main._DB_KEY or _main._SQLCIPHER_AVAILABLE:
        return

    meldung = (
        "DB_ENCRYPTION_KEY ist gesetzt, aber der SQLCipher-Treiber (sqlcipher3) "
        "fehlt. Die Datenbank wuerde im KLARTEXT angelegt und beschrieben, "
        "obwohl die Konfiguration Verschluesselung verlangt."
    )

    if os.environ.get('ALLOW_UNENCRYPTED', '').strip() == '1':
        _log.error(
            "%s ALLOW_UNENCRYPTED=1 ist gesetzt - der Start wird auf "
            "ausdruecklichen Wunsch fortgesetzt. Die Finanzdaten liegen ab "
            "jetzt UNVERSCHLUESSELT auf dem Datentraeger.", meldung)
        return

    _log.error(
        "%s Der Start wird abgebrochen. Behebung: Image neu bauen, damit "
        "sqlcipher3 installiert wird (siehe Dockerfile), oder "
        "DB_ENCRYPTION_KEY entfernen. Wer die Daten wirklich im Klartext "
        "halten will, setzt ALLOW_UNENCRYPTED=1.", meldung)
    raise RuntimeError(
        meldung + " Der Start wird abgebrochen. Behebung: sqlcipher3 "
        "installieren oder DB_ENCRYPTION_KEY entfernen. Bewusster "
        "Klartextbetrieb: ALLOW_UNENCRYPTED=1 setzen."
    )


def create_app():
    # Befund I9: ganz am Anfang, bevor irgendetwas die Datenbank oeffnet.
    _pruefe_db_verschluesselung()

    app = Flask(__name__)

    # Security: Use a consistent secret key across all workers.
    # Das Verzeichnis wird aus DATABASE_PATH abgeleitet, nicht relativ zum
    # Quellcode verdrahtet. Vorher landete der Signierschluessel immer in
    # <repo>/data, auch wenn die Datenbank ganz woanders lag: die Testsuite
    # schrieb damit bei jedem Lauf in das echte Datenverzeichnis, und wer
    # DATABASE_PATH umsetzt, bekam Schluessel und Datenbank an getrennten
    # Orten. Im Container aendert sich nichts - dort ist dirname(DATABASE_PATH)
    # genau das /app/data von vorher.
    data_dir = os.path.dirname(os.environ.get(
        'DATABASE_PATH',
        os.path.join(os.path.dirname(__file__), '..', 'data', 'kassenstuerzle.db')))
    secret = _ensure_data_dir_and_secret(data_dir)
    app.secret_key = os.environ.get('SECRET_KEY', secret)

    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024

    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'   # CSRF-Schutz: verhindert Cross-Site Requests
    # Befund I7: Default ist jetzt "sicher" (siehe _secure_cookie_aktiv oben).
    app.config['SESSION_COOKIE_SECURE'] = _secure_cookie_aktiv()

    # Session-Lifetime: läuft nach Inaktivität ab (Standard 12h, via ENV konfigurierbar)
    from datetime import timedelta
    app.config['PERMANENT_SESSION'] = True
    _session_hours = int(os.environ.get('SESSION_LIFETIME_HOURS', '12'))
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=_session_hours)

    # Rate-Limiter initialisieren (In-Memory; für Multi-Worker: Redis-Backend)
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[],           # Kein globales Limit; selektiv per Decorator
        storage_uri="memory://",
        strategy="fixed-window",
        # In der Testsuite abschaltbar: dort melden sich viele Tests nacheinander
        # an und wuerden sonst reihenweise 429 bekommen. NUR fuer Tests gedacht -
        # im Betrieb ist die Variable nicht gesetzt und das Limit aktiv.
        enabled=os.environ.get('DISABLE_RATE_LIMIT') != '1',
    )
    app.extensions['limiter'] = limiter  # Für Zugriff in Blueprints

    from .routes.main import main_bp, init_db
    app.register_blueprint(main_bp)

    # Rate-Limits auf sensible Endpunkte anwenden (Tabelle: RATE_LIMITS oben)
    for endpoint, limit_str in RATE_LIMITS.items():
        view_fn = app.view_functions.get(endpoint)
        if view_fn:
            # Das Ergebnis MUSS zurueckgeschrieben werden. limiter.limit() gibt
            # eine umhuellte Funktion zurueck; wird sie verworfen, zeigt
            # app.view_functions weiter auf die ungebremste Originalfunktion und
            # die gesamte Rate-Limit-Schicht ist wirkungslos - nachgewiesen mit
            # 15 Login-Versuchen ohne einen einzigen 429.
            app.view_functions[endpoint] = limiter.limit(limit_str)(view_fn)

    @app.context_processor
    def inject_now():
        from datetime import datetime
        return {'now_dt': datetime.now()}

    @app.context_processor
    def inject_csrf():
        from flask import session
        import secrets as _sec
        if 'csrf_token' not in session:
            session['csrf_token'] = _sec.token_hex(32)
        return {'csrf_token': session.get('csrf_token', '')}

    # ── CSP-Nonce pro Request ─────────────────────────────────────────────
    # Jeder Request bekommt einen frischen Zufallswert. Templates schreiben ihn
    # per <script nonce="{{ csp_nonce() }}"> in ihre Inline-Bloecke. Der Wert
    # landet auf g, damit Header und Template garantiert denselben benutzen.
    #
    # Bewusst in before_request und nicht erst beim ersten Abruf aus dem
    # Template: der CSP-Header wird fuer JEDE Antwort gesetzt, auch fuer
    # JSON-Antworten und Weiterleitungen, die gar kein Template rendern. Ein
    # nur bei Bedarf erzeugter Wert waere dort nicht vorhanden und der Header
    # wuerde beim Zusammenbauen scheitern.
    @app.before_request
    def _erzeuge_csp_nonce():
        from flask import g
        import secrets as _sec
        g.csp_nonce = _sec.token_urlsafe(16)

    @app.context_processor
    def inject_csp_nonce():
        from flask import g

        def csp_nonce():
            # Fallback nur fuer Aufrufe ausserhalb eines Requests (z.B.
            # render_template in einem Skript) - im Normalfall steht der Wert
            # laengst aus before_request bereit.
            if not hasattr(g, 'csp_nonce'):
                import secrets as _sec2
                g.csp_nonce = _sec2.token_urlsafe(16)
            return g.csp_nonce

        return {'csp_nonce': csp_nonce}

    @app.context_processor
    def inject_i18n():
        from flask import session
        from .translations import TRANSLATIONS, DEFAULT_LANGUAGE, translate
        lang = session.get('lang', DEFAULT_LANGUAGE)
        if lang not in TRANSLATIONS:
            lang = DEFAULT_LANGUAGE

        def t(key, **kwargs):
            return translate(lang, key, **kwargs)

        return {
            't': t,
            'current_lang': lang,
            # Für JS: window.I18N wird in base.html daraus befüllt (siehe t() dort)
            'i18n_dict': TRANSLATIONS.get(lang, TRANSLATIONS[DEFAULT_LANGUAGE]),
        }

    with app.app_context():
        from .routes.main import _migrate_to_encrypted, DB_PATH, _DB_KEY, _SQLCIPHER_AVAILABLE
        if _SQLCIPHER_AVAILABLE and _DB_KEY and os.path.exists(DB_PATH):
            _migrate_to_encrypted(DB_PATH, _DB_KEY)
        init_db()

    # ── Erinnerungs-Job (offene Abrechnungen / fehlende Monatseinträge) ──────
    # Läuft täglich per APScheduler; setzt voraus, dass der Prozess NACH einem
    # eventuellen Gunicorn-Fork gestartet wird (siehe preload_app=False in
    # gunicorn.conf.py) - sonst überlebt der Scheduler-Thread den Fork nicht.
    if os.environ.get('DISABLE_SCHEDULER') != '1':
        from apscheduler.schedulers.background import BackgroundScheduler
        from .routes.main import check_and_send_reminders
        scheduler = BackgroundScheduler(daemon=True)
        reminder_hour = int(os.environ.get('PUSH_REMINDER_HOUR', '9'))
        scheduler.add_job(
            lambda: check_and_send_reminders(app),
            'cron', hour=reminder_hour, minute=0,
            id='push_reminders', replace_existing=True,
        )
        scheduler.start()
        app.extensions['scheduler'] = scheduler

    # ── Security Response Headers ─────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        from flask import g
        # Verhindert MIME-Type-Sniffing (z.B. JS in Bild-Upload)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Verhindert Clickjacking (App darf nicht in iframe eingebettet werden)
        response.headers['X-Frame-Options'] = 'DENY'
        # Begrenzt Referrer-Informationen an externe Seiten
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Deaktiviert Browser-Features die die App nicht braucht
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        # Content Security Policy: nur eigene Ressourcen + CDNs die die App nutzt
        #
        # script-src setzt auf eine Nonce pro Antwort statt auf 'unsafe-inline'
        # (Befund I6). 'unsafe-inline' erlaubte JEDES eingeschleuste Skript und
        # entwertete damit genau den Schutz, der hinter den XSS-Befunden K1 und K3
        # greifen soll.
        #
        # Zwei Eigenheiten, die beim Aendern dieser Zeile wichtig sind:
        # 1. Sobald eine Nonce (oder ein Hash) in der Quellenliste steht, ignoriert
        #    der Browser 'unsafe-inline' vollstaendig - beides gleichzeitig
        #    anzugeben bringt also nichts. Im Browser nachgemessen.
        # 2. Nonces gelten NICHT fuer on...=-Attribute. Ein einziges neues
        #    onclick="..." in einem Template ist ab hier tot und faellt in den
        #    Python-Tests nicht auf, weil die kein JavaScript ausfuehren. Deshalb
        #    wacht tests/test_csp_nonce.py darueber, dass keine Inline-Handler
        #    zurueckkommen - diesen Test nicht abschwaechen.
        csp = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{getattr(g, 'csp_nonce', '')}' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.simpleicons.org; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://api.fontshare.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        response.headers['Content-Security-Policy'] = csp
        return response

    return app
