
from functools import wraps
from flask import g, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for
import os
# SQLCipher Drop-in: verschlüsselte SQLite-Datenbank
# Fallback auf Standard-sqlite3 wenn sqlcipher3 nicht installiert ist
try:
    import sqlcipher3 as sqlite3
    _SQLCIPHER_AVAILABLE = True
except ImportError:
    import sqlite3
    _SQLCIPHER_AVAILABLE = False
from datetime import datetime
import logging
log = logging.getLogger(__name__)

from ..translations import translate as _translate, SUPPORTED_LANGUAGES

def t(key, **kwargs):
    """Übersetzt `key` in die Sprache der aktuellen Session (Server-seitige
    Texte: Flash-Meldungen, JSON-Fehlermeldungen). Siehe app/translations.py."""
    return _translate(session.get('lang', 'de'), key, **kwargs)

from flask import Blueprint, current_app
main_bp = Blueprint('main', __name__)
DB_PATH = os.path.abspath(os.environ.get('DATABASE_PATH',
          os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'kassenstuerzle.db')))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
RECEIPTS_DIR = os.path.join(os.path.dirname(DB_PATH), 'receipts')
os.makedirs(RECEIPTS_DIR, exist_ok=True)
ALLOWED_RECEIPT_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.pdf'}

# ── Verschlüsselungs-Schlüssel aus ENV ────────────────────────────────────────
# Setze DB_ENCRYPTION_KEY in .env / docker-compose.yml
# Leer lassen = keine Verschlüsselung (Rückwärtskompatibilität)
_DB_KEY = os.environ.get('DB_ENCRYPTION_KEY', '').strip()


def _pragma_key_sql(key: str) -> str:
    """Baut ein PRAGMA-key-Statement mit korrekt escapten einfachen Anführungszeichen."""
    return "PRAGMA key='{}'".format(key.replace("'", "''"))


def _open_db(path: str) -> sqlite3.Connection:
    """Öffnet eine SQLite/SQLCipher-Verbindung.
    Setzt automatisch PRAGMA key wenn DB_ENCRYPTION_KEY gesetzt ist.
    """
    conn = sqlite3.connect(path)
    if _SQLCIPHER_AVAILABLE and _DB_KEY:
        conn.execute(_pragma_key_sql(_DB_KEY))
    # Nebenläufigkeit: WAL erlaubt parallele Leser während ein Writer schreibt,
    # busy_timeout verhindert sofortige "database is locked"-Fehler bei den
    # 4 Gunicorn-Threads. journal_mode ist in der DB-Datei persistent,
    # busy_timeout gilt pro Verbindung.
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def _migrate_to_encrypted(path: str, key: str) -> None:
    """Einmalige Migration: konvertiert unverschlüsselte DB → SQLCipher-verschlüsselt.
    Wird beim App-Start aufgerufen wenn DB_ENCRYPTION_KEY gesetzt ist.
    """
    import logging
    log = logging.getLogger(__name__)

    if not _SQLCIPHER_AVAILABLE:
        log.warning("DB_ENCRYPTION_KEY gesetzt, aber sqlcipher3 nicht installiert – DB bleibt unverschlüsselt!")
        return

    # Prüfe ob die DB bereits verschlüsselt ist
    try:
        conn = sqlite3.connect(path)
        conn.execute(_pragma_key_sql(key))
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.close()
        return  # Bereits verschlüsselt – nichts zu tun
    except Exception:
        pass  # DB ist unverschlüsselt – Migration notwendig

    log.info("Migriere Datenbank zu SQLCipher-Verschlüsselung …")
    import shutil as _shutil, tempfile as _tempfile

    # Backup der unverschlüsselten DB
    backup_path = path + ".unencrypted.bak"
    _shutil.copy2(path, backup_path)
    log.info(f"Unverschlüsseltes Backup gespeichert: {backup_path}")

    # Neue verschlüsselte DB erstellen
    with _tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tf:
        tmp_path = tf.name

    try:
        src = sqlite3.connect(path)  # öffnet unverschlüsselt, kein PRAGMA key
        # HINWEIS: sqlite3.Connection.backup() (Online-Backup-API) kopiert Seiten
        # roh zwischen Pagern und funktioniert NICHT zuverlässig zwischen einer
        # unverschlüsselten Quelle und einem verschlüsselten Ziel (schlägt mit
        # "SQL logic error" fehl). Der von SQLCipher offiziell dokumentierte Weg,
        # um eine Klartext-DB zu verschlüsseln, ist ATTACH + sqlcipher_export():
        # https://www.zetetic.net/sqlcipher/sqlcipher-api/#sqlcipher_export
        src.execute("ATTACH DATABASE ? AS encrypted KEY ?", (tmp_path, key))
        src.execute("SELECT sqlcipher_export('encrypted')")
        src.execute("DETACH DATABASE encrypted")
        src.close()
        _shutil.move(tmp_path, path)
        log.info("Migration erfolgreich abgeschlossen.")
    except Exception as e:
        log.error(f"Migration fehlgeschlagen: {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
MONTH_NAMES = ['Januar','Februar','März','April','Mai','Juni',
               'Juli','August','September','Oktober','November','Dezember']
MONTH_NAMES_EN = ['January','February','March','April','May','June',
                   'July','August','September','October','November','December']

def month_name(month_idx: int, lang: str = 'de') -> str:
    names = MONTH_NAMES_EN if lang == 'en' else MONTH_NAMES
    return names[month_idx - 1]


def _highlight(text: str, q: str) -> str:
    """Hebt Suchbegriff q im text hervor (HTML <mark>). Case-insensitive."""
    if not text or not q:
        return text or ''
    import re as _re, html as _html
    escaped_text = _html.escape(text)
    escaped_q    = _html.escape(q)
    pattern = _re.compile(_re.escape(escaped_q), _re.IGNORECASE)
    return pattern.sub(
        lambda m: f'<mark class="px-0 bg-warning bg-opacity-50">{m.group(0)}</mark>',
        escaped_text
    )



# ── CSRF-Schutz (Double-Submit-Token für JSON-APIs) ───────────────────────────
import secrets as _secrets

def _get_csrf_token() -> str:
    """Erstellt oder gibt den CSRF-Token der aktuellen Session zurück."""
    if 'csrf_token' not in session:
        session['csrf_token'] = _secrets.token_hex(32)
    return session['csrf_token']

def _check_csrf():
    """Prüft CSRF-Token bei allen zustandsändernden Requests (POST/PUT/DELETE).
    JSON-APIs: Token im Header X-CSRF-Token.
    Form-Posts: Token im Formularfeld csrf_token.
    Ausnahme: Login-Route (noch kein Token in Session möglich).
    """
    if request.endpoint in ('main.login', 'main.logout', 'main.service_worker', 'main.pwa_offline'):
        return
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        token = (
            request.headers.get('X-CSRF-Token') or
            request.form.get('csrf_token') or
            (request.get_json(silent=True) or {}).get('csrf_token')
        )
        expected = session.get('csrf_token')
        if not expected or not _secrets.compare_digest(str(token or ''), str(expected)):
            return jsonify(success=False, error=t('srv.csrf_invalid')), 403

@main_bp.before_request
def csrf_protect():
    result = _check_csrf()
    if result is not None:
        return result

# ── Rate-Limiter Zugriff aus Blueprint ────────────────────────────────────────
def _get_limiter():
    return current_app.extensions.get('limiter')


# ── Nebenläufigkeit: Payload-Version und Änderungszähler ─────────────────────
def _require_v2(d):
    """Weist Payloads veralteter Browser-Tabs ab.

    Nach der Umstellung auf Einzelzeilen würde ein alter Payload (Zeilen ohne
    `id`, kein `deleted_ids`) lauter Duplikate anlegen. Deshalb Pflicht-Marke.
    Rückgabe: None wenn gültig, sonst fertige 409-Antwort.
    """
    if not isinstance(d, dict) or d.get('v') != 2:
        return jsonify(success=False, error=t('srv.stale_client')), 409
    return None


def _bump_activity(conn, scope: str) -> int:
    """Erhöht den Änderungszähler für `scope` und gibt die neue Version zurück.

    Committet absichtlich nicht - der Aufrufer bündelt das mit seinen Writes.
    """
    who = session.get('username', '')
    now = datetime.now().isoformat(timespec='seconds')
    conn.execute(
        'INSERT INTO activity (scope, version, updated_at, updated_by) VALUES (?,1,?,?)'
        ' ON CONFLICT(scope) DO UPDATE SET version = version + 1, updated_at = ?, updated_by = ?',
        (scope, now, who, now, who))
    return conn.execute('SELECT version FROM activity WHERE scope=?', (scope,)).fetchone()['version']


def _month_scope(year: int, month: int) -> str:
    return f'month:{int(year):04d}-{int(month):02d}'


def _rate_limit(limit_string: str):
    """Prüft Rate-Limit für den aktuellen Request manuell via Flask-Limiter."""
    from flask_limiter.errors import RateLimitExceeded
    limiter = _get_limiter()
    if limiter is None:
        return None  # Limiter nicht initialisiert - kein Fehler
    try:
        limiter._check_request_limit(limit_string)
    except RateLimitExceeded:
        return jsonify(success=False, error=t('srv.rate_limited', limit=limit_string)), 429
    return None


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('main.login'))
        return f(*args, **kwargs)
    return decorated_function


@main_bp.route('/api/activity', methods=['POST'])
@login_required
def api_activity():
    """Liefert Änderungszähler und Anwesenheit für einen Bereich, schreibt
    gleichzeitig den Heartbeat des aufrufenden Nutzers (daher POST)."""
    from datetime import timedelta
    d = request.get_json(silent=True) or {}
    scope = str(d.get('scope') or '').strip()
    if not scope or len(scope) > 40:
        return jsonify(success=False, error=t('srv.missing_data')), 400

    conn = get_db()
    now = datetime.now()
    now_s = now.isoformat(timespec='seconds')
    cutoff = (now - timedelta(seconds=30)).isoformat(timespec='seconds')
    uid = session.get('user_id') or -1
    conn.execute(
        'INSERT INTO presence (user_id, scope, last_seen) VALUES (?,?,?)'
        ' ON CONFLICT(user_id, scope) DO UPDATE SET last_seen = ?',
        (uid, scope, now_s, now_s))
    # Alte Presence-Zeilen aufräumen, sonst wächst die Tabelle unbegrenzt.
    conn.execute('DELETE FROM presence WHERE last_seen < ?', (cutoff,))
    conn.commit()

    row = conn.execute('SELECT version, updated_at, updated_by FROM activity WHERE scope=?',
                       (scope,)).fetchone()
    others = [r['username'] for r in conn.execute(
        'SELECT u.username FROM presence p JOIN users u ON u.id = p.user_id'
        ' WHERE p.scope=? AND p.last_seen >= ? AND p.user_id <> ?', (scope, cutoff, uid))]
    conn.close()
    return jsonify(success=True,
                   version=(row['version'] if row else 0),
                   updated_at=(row['updated_at'] if row else None),
                   updated_by=(row['updated_by'] if row else None),
                   others=others)


@main_bp.route('/api/csrf-token')
@login_required
def get_csrf_token():
    """Gibt den CSRF-Token für die aktuelle Session zurück."""
    return jsonify(csrf_token=_get_csrf_token())


def get_db():
    """Gibt eine pro-Request gecachte DB-Verbindung zurück (via Flask g)."""
    if 'db' not in g:
        g.db = _open_db(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@main_bp.teardown_request
def close_db(exception=None):
    """Schließt die DB-Verbindung am Ende jedes Requests automatisch."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def _ensure_admin(conn) -> None:
    """Garantiert mindestens einen aktiven Admin. Laeuft bei JEDEM Start.

    Hintergrund: die neue Spalte `role` bekommt per ALTER den Default
    'member'. Ohne diese Kaskade waere der einzige vorhandene Benutzer nach
    dem Update degradiert und niemand koennte mehr Benutzer verwalten.
    Reihenfolge: bestehender Admin -> Benutzer 'admin' -> aeltester Benutzer.
    """
    vorhanden = conn.execute(
        "SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1").fetchone()['c']
    if vorhanden:
        return
    ziel = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    if ziel is None:
        ziel = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if ziel is None:
        return   # leere Tabelle - der Bootstrap oben legt 'admin' an
    conn.execute("UPDATE users SET role='admin', is_active=1 WHERE id=?", (ziel['id'],))
    log.warning("Kein aktiver Admin vorhanden - Benutzer id=%s wurde zum Admin befoerdert.",
                ziel['id'])


def _autolink_single_person(conn) -> None:
    """Verknuepft Benutzer und Person nur, wenn es von beiden genau einen gibt.

    In jedem anderen Fall bleibt person_id leer: eine falsche Zuordnung wuerde
    spaeter dem falschen Menschen Buchungen und Erinnerungen zuordnen.
    """
    users = conn.execute('SELECT id, person_id FROM users').fetchall()
    persons = conn.execute('SELECT id FROM persons').fetchall()
    if len(users) == 1 and len(persons) == 1 and users[0]['person_id'] is None:
        conn.execute('UPDATE users SET person_id=? WHERE id=?',
                     (persons[0]['id'], users[0]['id']))


def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL
    )""")

    user = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    # Nur bei einer wirklich leeren Tabelle einen frischen 'admin' anlegen.
    # Sonst wuerde das Umbenennen des einzigen Benutzers (z.B. durch die
    # Admin-Kaskade weiter unten) bei jedem Neustart einen zweiten,
    # ueberfluessigen 'admin'-Account erzeugen.
    _user_count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()['c']
    if not user and _user_count == 0:
        from werkzeug.security import generate_password_hash
        import os as _os
        # Passwort aus ENV oder sicheres Zufalls-Passwort generieren
        _init_pwd = _os.environ.get('ADMIN_PASSWORD', '')
        if not _init_pwd:
            import secrets as _s
            _init_pwd = _s.token_urlsafe(12)
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "ACHTUNG: Kein ADMIN_PASSWORD gesetzt. "
                "Temporäres Passwort: %s — bitte sofort in den Einstellungen ändern!", _init_pwd
            )
        default_hash = generate_password_hash(_init_pwd)
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ('admin', default_hash))
    else:
        # Prüfe ob noch das alte Default-Passwort 'admin' aktiv ist
        from werkzeug.security import check_password_hash as _cph
        _row = conn.execute("SELECT password_hash FROM users WHERE username = 'admin'").fetchone()
        if _row and _cph(_row['password_hash'], 'admin'):
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "SICHERHEITSWARNUNG: Admin-Passwort ist noch 'admin'. "
                "Bitte sofort unter Einstellungen → Passwort ändern!"
            )

    conn.execute("""CREATE TABLE IF NOT EXISTS persons (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        wallet_start_date TEXT DEFAULT NULL, wallet_start_amount REAL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        color TEXT NOT NULL DEFAULT '#6c757d', is_split INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, year INTEGER NOT NULL,
        month INTEGER NOT NULL, day INTEGER NOT NULL, description TEXT DEFAULT '',
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        amount REAL NOT NULL DEFAULT 0, is_cash INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS month_status (
        year INTEGER NOT NULL, month INTEGER NOT NULL,
        settled INTEGER NOT NULL DEFAULT 0, settled_at TEXT,
        PRIMARY KEY (year, month))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS recurring_expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day INTEGER NOT NULL DEFAULT 1, description TEXT NOT NULL DEFAULT '',
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        amount REAL NOT NULL DEFAULT 0, frequency TEXT NOT NULL DEFAULT 'monthly',
        start_month INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS income (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER NOT NULL, month INTEGER NOT NULL,
        day INTEGER NOT NULL DEFAULT 1, description TEXT DEFAULT '',
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        amount REAL NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS category_splits (
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
        share REAL NOT NULL DEFAULT 1,
        PRIMARY KEY (category_id, person_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT NOT NULL UNIQUE,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        lang TEXT NOT NULL DEFAULT 'de',
        created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS activity (
        scope TEXT PRIMARY KEY,
        version INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT,
        updated_by TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS presence (
        user_id INTEGER NOT NULL,
        scope TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        PRIMARY KEY (user_id, scope))""")
    # Migrate income table: add category_id if missing
    try:
        conn.execute('ALTER TABLE income ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL')
        conn.commit()
    except Exception:
        pass  # column already exists

    # Einmalige Sicherungskopie, bevor die Profil-Spalten angelegt werden.
    # Die Schemapruefung ist die Marke: existiert 'role' schon, lief die
    # Migration bereits und es wird nichts erneut gesichert.
    _user_cols = {r[1] for r in conn.execute('PRAGMA table_info(users)')}
    if 'role' not in _user_cols:
        try:
            _dest = create_backup('pre-profiles')
            log.info("Sicherungskopie vor der Profil-Migration: %s", _dest.name)
        except Exception:
            log.exception("Sicherungskopie vor der Profil-Migration fehlgeschlagen "
                          "- Migration wird trotzdem fortgesetzt")

    for sql in [
        'ALTER TABLE categories ADD COLUMN is_split INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE expenses ADD COLUMN is_cash INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE persons ADD COLUMN wallet_start_date TEXT DEFAULT NULL',
        'ALTER TABLE persons ADD COLUMN wallet_start_amount REAL DEFAULT 0',
        'ALTER TABLE expenses ADD COLUMN paid_for_person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL',
        'ALTER TABLE expenses ADD COLUMN receipt_filename TEXT DEFAULT NULL',
        # ── Phase B: Benutzerprofile ──────────────────────────────────────
        'ALTER TABLE users ADD COLUMN person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL',
        "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'member'",
        'ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1',
        'ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE users ADD COLUMN created_at TEXT',
        'ALTER TABLE users ADD COLUMN last_login TEXT',
        'ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0',
    ]:
        try: conn.execute(sql)
        except: pass

    # 1:1-Verknuepfung Benutzer <-> Person; beide Seiten bleiben optional.
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_person'
                 ' ON users(person_id) WHERE person_id IS NOT NULL')
    conn.execute('CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)')

    _ensure_admin(conn)
    _autolink_single_person(conn)

    # ── Performance: Indices für häufig gefilterte Spalten ──────────────────
    for idx_sql in [
        'CREATE INDEX IF NOT EXISTS idx_expenses_year_month ON expenses(year, month)',
        'CREATE INDEX IF NOT EXISTS idx_expenses_person_id ON expenses(person_id)',
        'CREATE INDEX IF NOT EXISTS idx_expenses_category_id ON expenses(category_id)',
        'CREATE INDEX IF NOT EXISTS idx_income_year_month ON income(year, month)',
        'CREATE INDEX IF NOT EXISTS idx_income_person_id ON income(person_id)',
        'CREATE INDEX IF NOT EXISTS idx_month_status_ym ON month_status(year, month)',
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass

    conn.commit(); conn.close()
    # init_db() darf innerhalb desselben App-Context mehrfach laufen (Idempotenz-
    # Tests, evtl. manuelle Re-Migration). get_db() cached die Verbindung in `g` -
    # ohne diesen Pop wuerde ein erneuter Aufruf auf der bereits geschlossenen
    # Verbindung weiterarbeiten und mit "Cannot operate on a closed database" scheitern.
    g.pop('db', None)

def build_ov(conn, year, month, is_settled=False):
    rows = conn.execute("""
        SELECT e.person_id, e.paid_for_person_id,
               payer.name as payer_name, payee.name as payee_name,
               c.name as cname, c.color, SUM(e.amount) as total
        FROM expenses e
        LEFT JOIN persons payer ON e.person_id=payer.id
        LEFT JOIN persons payee ON e.paid_for_person_id=payee.id
        LEFT JOIN categories c ON e.category_id=c.id
        WHERE e.year=? AND e.month=?
        GROUP BY e.person_id, e.paid_for_person_id, e.category_id""",
        (year, month)).fetchall()
    pm, cs = {}, {}
    for r in rows:
        cn = r['cname'] or 'Sonstige'
        cs[cn] = r['color'] or '#6c757d'
        if r['paid_for_person_id'] and is_settled:
            pn = r['payee_name'] or 'Unbekannt'
        else:
            pn = r['payer_name'] or 'Unbekannt'
        pm.setdefault(pn, {})[cn] = pm.get(pn, {}).get(cn, 0) + r['total']
    # Einnahmen aus Split-Kategorien unverändert
    inc_rows = conn.execute("""
        SELECT p.name as pname, c.name as cname, c.color, SUM(i.amount) as total
        FROM income i LEFT JOIN persons p ON i.person_id=p.id
        LEFT JOIN categories c ON i.category_id=c.id
        WHERE i.year=? AND i.month=? AND c.is_split=1
        GROUP BY i.person_id, i.category_id""",
        (year, month)).fetchall()
    for r in inc_rows:
        pn = r['pname'] or 'Unbekannt'; cn = r['cname'] or 'Sonstige'
        cs[cn] = r['color'] or '#6c757d'
        pm.setdefault(pn, {})[cn] = pm.get(pn, {}).get(cn, 0) - r['total']
    return pm, sorted(cs.items())


def get_category_split_ratios(conn):
    """Liefert {category_id: {person_name: anteil}} mit anteil summiert zu 1.0,
    für Kategorien mit einem individuellen Aufteilungsverhältnis (category_splits).
    'share' ist ein relatives Gewicht (z.B. 60/40 oder 2/1) - wird hier normalisiert.
    Kategorien ohne Einträge fehlen im Ergebnis; Aufrufer sollen dann auf
    Gleichverteilung unter allen aktuellen Personen zurückfallen."""
    rows = conn.execute("""
        SELECT cs.category_id as cid, p.name as pname, cs.share
        FROM category_splits cs JOIN persons p ON cs.person_id = p.id
        WHERE cs.share > 0""").fetchall()
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r['cid'], {})[r['pname']] = r['share']
    result = {}
    for cid, shares in by_cat.items():
        total = sum(shares.values())
        if total > 0:
            result[cid] = {p: v / total for p, v in shares.items()}
    return result


def _split_ratio_for(ratios, cid, persons):
    """Anteil pro Person für Kategorie cid - individuelles Verhältnis falls
    konfiguriert, sonst Gleichverteilung unter allen aktuellen Personen."""
    custom = ratios.get(cid)
    if custom:
        return custom
    n = len(persons) or 1
    return {p: 1.0 / n for p in persons}


def calculate_settlement(year, month, conn):
    persons = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]
    status = conn.execute('SELECT settled,settled_at FROM month_status WHERE year=? AND month=?', (year,month)).fetchone()
    is_settled = bool(status and status['settled'])
    settled_at = status['settled_at'] if status else None
    if is_settled:
        return {'persons': persons, 'paid': {p:0 for p in persons}, 'fair_share':0,
                'total':0, 'balances': {p:0 for p in persons}, 'transactions': [],
                'cat_details': [], 'paid_for_items': [], 'is_settled': True, 'settled_at': settled_at}
    if len(persons) < 2: return None

    # split-category expenses (excluding paid-for rows)
    exp_rows = conn.execute("""
        SELECT p.name as pname, c.id as cid, c.name as cname, c.color,
               SUM(e.amount) as total
        FROM expenses e LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id
        WHERE e.year=? AND e.month=? AND c.is_split=1
          AND (e.paid_for_person_id IS NULL)
        GROUP BY e.person_id, e.category_id""", (year, month)).fetchall()

    # split-category income: income reduces that person's net contribution
    inc_rows = conn.execute("""
        SELECT p.name as pname, c.id as cid, c.name as cname, c.color, SUM(i.amount) as total
        FROM income i LEFT JOIN persons p ON i.person_id=p.id
        LEFT JOIN categories c ON i.category_id=c.id
        WHERE i.year=? AND i.month=? AND c.is_split=1
        GROUP BY i.person_id, i.category_id""", (year, month)).fetchall()

    cat_data = {}
    for r in exp_rows:
        cid = r['cid']
        if cid not in cat_data:
            cat_data[cid] = {'name': r['cname'], 'color': r['color'] or '#6c757d',
                             'expenses_by': {}, 'income_by': {}, 'paid_by': {}}
        pn = r['pname'] or 'Unbekannt'
        cat_data[cid]['expenses_by'][pn] = cat_data[cid]['expenses_by'].get(pn, 0) + r['total']
        cat_data[cid]['paid_by'][pn] = cat_data[cid]['paid_by'].get(pn, 0) + r['total']

    for r in inc_rows:
        cid = r['cid']
        if cid not in cat_data:
            cat_data[cid] = {'name': r['cname'], 'color': r['color'] or '#6c757d',
                             'expenses_by': {}, 'income_by': {}, 'paid_by': {}}
        pn = r['pname'] or 'Unbekannt'
        cat_data[cid]['income_by'][pn] = cat_data[cid]['income_by'].get(pn, 0) + r['total']
        # income reduces net contribution
        cat_data[cid]['paid_by'][pn] = cat_data[cid]['paid_by'].get(pn, 0) - r['total']

    paid = {p: 0.0 for p in persons}
    for cd in cat_data.values():
        for pn, amt in cd['paid_by'].items():
            if pn in paid: paid[pn] += amt
    total = sum(paid.values())

    # Ausgleich pro Kategorie mit deren individuellem Aufteilungsverhältnis
    # berechnen (statt einem einzigen Fair-Share über alle Kategorien hinweg) -
    # so wirkt sich ein 60/40-Verhältnis bei "Miete" nicht auf eine 50/50-
    # Kategorie wie "Lebensmittel" aus.
    split_ratios = get_category_split_ratios(conn)
    balances = {p: 0.0 for p in persons}
    for cid, cd in cat_data.items():
        cat_total = sum(cd['paid_by'].values())
        ratio = _split_ratio_for(split_ratios, cid, persons)
        for p in persons:
            balances[p] += cd['paid_by'].get(p, 0) - cat_total * ratio.get(p, 0)
    balances = {p: round(v, 2) for p, v in balances.items()}
    fair_share = total / len(persons) if persons else 0  # nur informativ (Durchschnitt über alle Kategorien)

    # "Bezahlt für" rows
    paid_for_rows = conn.execute("""
        SELECT payer.name as payer_name, payee.name as payee_name,
               e.description, e.amount
        FROM expenses e
        LEFT JOIN persons payer ON e.person_id=payer.id
        LEFT JOIN persons payee ON e.paid_for_person_id=payee.id
        WHERE e.year=? AND e.month=? AND e.paid_for_person_id IS NOT NULL
        ORDER BY e.day, e.id""", (year, month)).fetchall()

    paid_for_items = []
    for r in paid_for_rows:
        payer = r['payer_name'] or 'Unbekannt'
        payee = r['payee_name'] or 'Unbekannt'
        amt   = r['amount']
        paid_for_items.append({'payer': payer, 'payee': payee,
                               'amount': round(amt, 2), 'description': r['description'] or ''})
        if payer in balances: balances[payer] = round(balances[payer] + amt, 2)
        if payee in balances: balances[payee] = round(balances[payee] - amt, 2)

    if not exp_rows and not inc_rows and not paid_for_rows:
        return {'persons': persons, 'paid': {p: 0 for p in persons},
                'fair_share': 0, 'total': 0, 'balances': {p: 0 for p in persons},
                'transactions': [], 'cat_details': [], 'paid_for_items': [],
                'is_settled': False, 'settled_at': None}

    creds = sorted([[n,  b] for n, b in balances.items() if  b >  0.005], key=lambda x: -x[1])
    debts = sorted([[n, -b] for n, b in balances.items() if  b < -0.005], key=lambda x: -x[1])
    transactions = []
    ci = di = 0
    while ci < len(creds) and di < len(debts):
        transfer = min(creds[ci][1], debts[di][1])
        if transfer > 0.005:
            transactions.append({'from': debts[di][0], 'to': creds[ci][0], 'amount': round(transfer, 2)})
        creds[ci][1] -= transfer; debts[di][1] -= transfer
        if creds[ci][1] < 0.005: ci += 1
        if debts[di][1] < 0.005: di += 1

    return {'persons': persons, 'paid': {p: round(v,2) for p,v in paid.items()},
            'fair_share': round(fair_share, 2), 'total': round(total, 2),
            'balances': balances, 'transactions': transactions,
            'cat_details': list(cat_data.values()), 'paid_for_items': paid_for_items,
            'is_settled': False, 'settled_at': None}


def calculate_category_settlements(year, month, conn):
    """Per split-category settlement: settlement[person] = net_paid - anteiliger Fair-Share.
    net_paid = expenses - income. Positive = overpaid = receives. Negative = underpaid = pays.
    Nutzt ein individuelles Aufteilungsverhältnis falls für die Kategorie konfiguriert
    (category_splits), sonst Gleichverteilung unter allen aktuellen Personen."""
    persons = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]
    n = len(persons)
    if n < 2:
        return {}
    exp_rows = conn.execute("""
        SELECT c.id as cid, c.name as cname, p.name as pname, SUM(e.amount) as total
        FROM expenses e
        LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id
        WHERE e.year=? AND e.month=? AND c.is_split=1
        GROUP BY c.id, e.person_id""", (year, month)).fetchall()
    inc_rows = conn.execute("""
        SELECT c.id as cid, c.name as cname, p.name as pname, SUM(i.amount) as total
        FROM income i
        LEFT JOIN persons p ON i.person_id=p.id
        LEFT JOIN categories c ON i.category_id=c.id
        WHERE i.year=? AND i.month=? AND c.is_split=1
        GROUP BY c.id, i.person_id""", (year, month)).fetchall()
    if not exp_rows and not inc_rows:
        return {}

    split_ratios = get_category_split_ratios(conn)
    cat_paid = {}  # cid -> {'name': cname, 'paid': {pname: amt}}
    for r in exp_rows:
        cn = r['cname'] or 'Sonstige'; pn = r['pname'] or 'Unbekannt'
        d = cat_paid.setdefault(r['cid'], {'name': cn, 'paid': {}})
        d['paid'][pn] = d['paid'].get(pn, 0) + r['total']
    for r in inc_rows:
        cn = r['cname'] or 'Sonstige'; pn = r['pname'] or 'Unbekannt'
        d = cat_paid.setdefault(r['cid'], {'name': cn, 'paid': {}})
        d['paid'][pn] = d['paid'].get(pn, 0) - r['total']  # income reduces net contribution

    result = {}
    for cid, d in cat_paid.items():
        cat_total = sum(d['paid'].values())
        ratio = _split_ratio_for(split_ratios, cid, persons)
        result[d['name']] = {p: round(d['paid'].get(p, 0) - cat_total * ratio.get(p, 0), 2) for p in persons}
    return result

def calculate_savings_per_person(year, month, conn, is_settled=False):
    persons = [dict(r) for r in conn.execute('SELECT id, name FROM persons ORDER BY name')]
    inc_rows = conn.execute(
        'SELECT person_id, SUM(amount) as total FROM income WHERE year=? AND month=? GROUP BY person_id',
        (year, month)).fetchall()
    exp_rows = conn.execute(
        'SELECT person_id, paid_for_person_id, SUM(amount) as total FROM expenses WHERE year=? AND month=? GROUP BY person_id, paid_for_person_id',
        (year, month)).fetchall()
    inc_map = {r['person_id']: float(r['total'] or 0) for r in inc_rows}
    exp_map = {}
    for r in exp_rows:
        amt = float(r['total'] or 0)
        target_id = r['paid_for_person_id'] if (r['paid_for_person_id'] and is_settled) else r['person_id']
        exp_map[target_id] = exp_map.get(target_id, 0) + amt
    result = []
    for p in persons:
        pid = p['id']
        inc = round(inc_map.get(pid, 0), 2)
        exp = round(exp_map.get(pid, 0), 2)
        result.append({'name': p['name'], 'income': inc, 'expenses': exp, 'savings': round(inc - exp, 2)})
    return result or None


def calculate_wallet(year, month, conn):
    persons = conn.execute(
        'SELECT * FROM persons WHERE wallet_start_date IS NOT NULL AND wallet_start_date != "" ORDER BY name'
    ).fetchall()
    result = []
    for p in persons:
        sd = p['wallet_start_date'] or ''
        if not sd: continue
        try: sy, sm = map(int, sd.split('-'))
        except: continue
        if (year, month) < (sy, sm): continue
        sa = float(p['wallet_start_amount'] or 0)
        rows = conn.execute(
            'SELECT year, month, SUM(amount) as total FROM expenses'
            ' WHERE person_id=? AND is_cash=1 GROUP BY year, month', (p['id'],)).fetchall()
        prev_cash = curr_cash = 0.0
        for r in rows:
            ry, rm, rt = r['year'], r['month'], float(r['total'] or 0)
            if (ry, rm) == (year, month): curr_cash = rt
            elif (sy, sm) <= (ry, rm) < (year, month): prev_cash += rt
        start_bal = round(sa - prev_cash, 2)
        end_bal   = round(start_bal - curr_cash, 2)
        result.append({'name': p['name'], 'start_balance': start_bal,
                       'cash_expenses': round(curr_cash, 2), 'end_balance': end_bal})
    return result or None



def get_recurring_for_month(year, month, conn):
    rows = conn.execute('SELECT * FROM recurring_expenses WHERE active=1').fetchall()
    result = []
    for r in rows:
        freq, sm = r['frequency'], r['start_month']
        if freq == 'monthly':
            result.append(dict(r))
        elif freq == 'quarterly':
            applicable = {((sm-1+i*3) % 12)+1 for i in range(4)}
            if month in applicable: result.append(dict(r))
        elif freq == 'yearly':
            if month == sm: result.append(dict(r))
    return result

def apply_settlements_to_ov(ov_p, ov_settlements, all_person_names):
    """Net = paid - settlement = fair_share for every person in settled split categories.
    settlement[p] = paid[p] - fair_share  →  net = paid - settlement = fair_share"""
    result = {p: dict(cats) for p, cats in ov_p.items()}
    for cn, person_sett in ov_settlements.items():
        for p in all_person_names:
            paid = ov_p.get(p, {}).get(cn, 0)
            adj  = person_sett.get(p, 0)   # paid - fair_share
            net  = round(paid - adj, 2)    # = fair_share
            if net > 0.005:
                result.setdefault(p, {})[cn] = net
            elif p in result and cn in result[p]:
                del result[p][cn]
    return result


# In-Memory Brute-Force-Schutz (IP-basiert)
import time as _time
from collections import defaultdict as _defaultdict
_login_attempts: dict = _defaultdict(lambda: {'count': 0, 'locked_until': 0})
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 60


@main_bp.route('/login', methods=['GET', 'POST'])
def login():
    ip = request.remote_addr or 'unknown'
    state = _login_attempts[ip]

    # Sperre prüfen
    if state['locked_until'] > _time.time():
        remaining = int(state['locked_until'] - _time.time())
        flash(t('login.locked', seconds=remaining), 'danger')
        return render_template('login.html')

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        conn = get_db()
        user = conn.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            # Erfolg: Zähler zurücksetzen, Session neu aufbauen
            _login_attempts[ip] = {'count': 0, 'locked_until': 0}
            lang = session.get('lang')  # Sprachwahl übersteht session.clear()
            session.clear()
            session.permanent = True   # aktiviert PERMANENT_SESSION_LIFETIME
            session['logged_in'] = True
            session['user_id'] = user['id']
            session['username'] = username
            # CSRF-Token bei Login frisch erstellen
            session['csrf_token'] = _secrets.token_hex(32)
            if lang:
                session['lang'] = lang
            return redirect(url_for('main.index'))
        else:
            state['count'] += 1
            if state['count'] >= _MAX_LOGIN_ATTEMPTS:
                state['locked_until'] = _time.time() + _LOGIN_LOCKOUT_SECONDS
                state['count'] = 0
                flash(t('login.locked_now', seconds=_LOGIN_LOCKOUT_SECONDS), 'danger')
            else:
                remaining_attempts = _MAX_LOGIN_ATTEMPTS - state['count']
                flash(t('login.invalid', remaining=remaining_attempts), 'danger')

    return render_template('login.html')

@main_bp.route('/logout')
def logout():
    lang = session.get('lang')  # Sprachwahl übersteht session.clear()
    session.clear()
    if lang:
        session['lang'] = lang
    return redirect(url_for('main.login'))

@main_bp.route('/change_password', methods=['POST'])
@login_required
def change_password():
    old_pw = request.form.get('old_password')
    new_pw = request.form.get('new_password')

    conn = get_db()
    user = conn.execute("SELECT password_hash FROM users WHERE id = ?", (session['user_id'],)).fetchone()

    if not user or not check_password_hash(user['password_hash'], old_pw):
        conn.close()
        return jsonify({'success': False, 'error': t('srv.old_password_wrong')}), 400

    # Passwort-Stärke prüfen: min. 8 Zeichen, nicht leer
    if not new_pw or len(new_pw) < 8:
        conn.close()
        return jsonify({'success': False, 'error': t('srv.new_password_too_short')}), 400

    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                 (generate_password_hash(new_pw), session['user_id']))
    conn.commit()
    conn.close()

    return jsonify({'success': True})

@main_bp.route('/')
@login_required
def index():
    # Übersicht ist die Startseite; der aktuelle Monat bleibt über die
    # Navigation ("Monat") jederzeit einen Klick entfernt.
    return redirect(url_for('main.overview'))

@main_bp.route('/month/<int:year>/<int:month>')
@login_required
def month_view(year, month):
    conn = get_db()
    # Version VOR den Datenabfragen lesen: ein Save, das dazwischen landet,
    # darf höchstens einen unnötigen "jemand anderes hat geändert"-Banner
    # auslösen (harmlos, Nutzer lädt neu) - niemals einen verpassten.
    _scope = _month_scope(year, month)
    _act_row = conn.execute('SELECT version FROM activity WHERE scope=?', (_scope,)).fetchone()
    activity_version = _act_row['version'] if _act_row else 0
    income = [dict(r) for r in conn.execute(
        'SELECT * FROM income WHERE year=? AND month=? ORDER BY day,id', (year,month))]
    income_total  = round(sum(r['amount'] for r in income), 2)
    expense_total_mv = round(conn.execute('SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE year=? AND month=?',(year,month)).fetchone()['t'], 2)
    savings_mv    = round(income_total - expense_total_mv, 2)
    expenses = [dict(r) for r in conn.execute("""
        SELECT e.id, e.day, e.description, e.person_id, e.category_id, e.amount, e.is_cash,
               e.paid_for_person_id, e.receipt_filename
        FROM expenses e WHERE e.year=? AND e.month=? ORDER BY e.day, e.id""",
        (year, month))]
    persons    = [dict(r) for r in conn.execute('SELECT * FROM persons ORDER BY name')]
    categories = [dict(r) for r in conn.execute('SELECT * FROM categories ORDER BY name')]
    ms = conn.execute('SELECT settled,settled_at FROM month_status WHERE year=? AND month=?', (year,month)).fetchone()
    is_settled = bool(ms and ms['settled']); settled_at = ms['settled_at'] if ms else None
    ov_p, ov_c = build_ov(conn, year, month, is_settled)
    settlement = calculate_settlement(year, month, conn)
    ov_settlements = calculate_category_settlements(year, month, conn)
    wallet = calculate_wallet(year, month, conn)
    savings_pp = calculate_savings_per_person(year, month, conn, is_settled)
    if is_settled and ov_settlements:
        all_pnames = [p['name'] for p in persons]
        ov_p = apply_settlements_to_ov(ov_p, ov_settlements, all_pnames)
    conn.close()
    pm = month-1 if month>1 else 12; py = year if month>1 else year-1
    nm = month+1 if month<12 else 1; ny = year if month<12 else year+1
    lang = session.get('lang', 'de')
    month_names_list = [month_name(i, lang) for i in range(1, 13)]
    this_year = datetime.now().year
    year_range = sorted(set(range(this_year - 5, this_year + 3)) | {year})
    return render_template('month.html', active='month',
        year=year, month=month, month_name=month_name(month, lang),
        month_names=month_names_list, year_range=year_range,
        expenses=expenses, persons=persons, categories=categories,
        ov_p=ov_p, ov_c=ov_c, settlement=settlement,
        income=income, income_total=income_total,
        expense_total=expense_total_mv, savings=savings_mv,
        ov_settlements=ov_settlements, wallet=wallet, savings_pp=savings_pp,
        is_settled=is_settled, settled_at=settled_at,
        prev_year=py, prev_month=pm, next_year=ny, next_month=nm,
        activity_scope=_scope, activity_version=activity_version)

@main_bp.route('/api/expenses/save', methods=['POST'])
@login_required
def save_expenses():
    d = request.get_json(silent=True)
    stale = _require_v2(d)
    if stale:
        return stale
    year, month = int(d['year']), int(d['month'])
    rows = d.get('rows') or []
    deleted_ids = d.get('deleted_ids') or []
    conn = get_db()
    id_map = {}

    # 1) Ausdrücklich gelöschte Zeilen entfernen
    for raw in deleted_ids:
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        conn.execute('DELETE FROM expenses WHERE id=? AND year=? AND month=?', (rid, year, month))

    # 2) Zeilen anlegen bzw. aktualisieren (nie löschen, was hier fehlt)
    for e in rows:
        try:
            amt = float(e.get('amount') or 0)
        except (TypeError, ValueError):
            amt = 0
        try:
            rid = int(e['id']) if e.get('id') else None
        except (TypeError, ValueError):
            rid = None

        if amt == 0:
            # Geleerter Betrag: bestehende Zeile löschen, neue ignorieren
            if rid is not None:
                conn.execute('DELETE FROM expenses WHERE id=? AND year=? AND month=?',
                             (rid, year, month))
            continue

        pid     = int(e['person_id'])          if e.get('person_id')          else None
        cid     = int(e['category_id'])        if e.get('category_id')        else None
        pfpid   = int(e['paid_for_person_id']) if e.get('paid_for_person_id') else None
        is_cash = 1 if e.get('is_cash') else 0
        rfn     = e.get('receipt_filename') or None
        day     = int(e.get('day') or 1)
        desc    = e.get('description', '')

        if rid is not None:
            conn.execute(
                'UPDATE expenses SET day=?, description=?, person_id=?, category_id=?,'
                ' amount=?, is_cash=?, paid_for_person_id=?, receipt_filename=?'
                ' WHERE id=? AND year=? AND month=?',
                (day, desc, pid, cid, amt, is_cash, pfpid, rfn, rid, year, month))
        else:
            cur = conn.execute(
                'INSERT INTO expenses (year,month,day,description,person_id,category_id,'
                'amount,is_cash,paid_for_person_id,receipt_filename) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (year, month, day, desc, pid, cid, amt, is_cash, pfpid, rfn))
            ck = e.get('client_key')
            if ck:
                id_map[str(ck)] = cur.lastrowid

    activity_version = _bump_activity(conn, _month_scope(year, month))
    conn.commit()

    ms = conn.execute('SELECT settled FROM month_status WHERE year=? AND month=?', (year, month)).fetchone()
    is_settled = bool(ms and ms['settled'])
    ov_p, ov_c = build_ov(conn, year, month, is_settled)
    settlement = calculate_settlement(year, month, conn)
    ov_settlements = calculate_category_settlements(year, month, conn)
    wallet = calculate_wallet(year, month, conn)
    savings_pp = calculate_savings_per_person(year, month, conn, is_settled)
    if is_settled and ov_settlements:
        all_pnames = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]
        ov_p = apply_settlements_to_ov(ov_p, ov_settlements, all_pnames)
    conn.close()
    return jsonify({'success': True,
        'id_map': id_map, 'activity_version': activity_version,
        'ov_p': {k: dict(v) for k, v in ov_p.items()}, 'ov_c': ov_c,
        'settlement': settlement, 'wallet': wallet,
        'savings_pp': savings_pp, 'ov_settlements': ov_settlements})

@main_bp.route('/api/persons/save', methods=['POST'])
@login_required
def save_persons():
    import re as _re
    d = request.get_json(silent=True)
    stale = _require_v2(d)
    if stale:
        return stale
    conn = get_db()
    id_map = {}
    conflicts = []

    for raw in (d.get('deleted_ids') or []):
        try:
            did = int(raw)
        except (TypeError, ValueError):
            continue
        conn.execute('DELETE FROM persons WHERE id=?', (did,))

    for p in (d.get('rows') or []):
        n = (p.get('name') or '').strip()
        if not n:
            continue
        wd_raw = (p.get('wallet_start_date') or '').strip()
        wd = wd_raw if _re.match(r'^\d{4}-\d{2}$', wd_raw) else None
        try:
            wa = float(p.get('wallet_start_amount') or 0)
        except (TypeError, ValueError):
            wa = 0.0
        try:
            rid = int(p['id']) if p.get('id') else None
        except (TypeError, ValueError):
            rid = None

        # Namen sind UNIQUE. Kollisionen werden gemeldet statt verschluckt,
        # die übrigen Zeilen desselben Requests trotzdem gespeichert.
        if rid is not None:
            try:
                conn.execute(
                    'UPDATE persons SET name=?, wallet_start_date=?, wallet_start_amount=? WHERE id=?',
                    (n, wd, wa, rid))
            except sqlite3.IntegrityError:
                # Jede IntegrityError wird hier als Namenskonflikt gemeldet.
                # Die einzige Constraint auf `persons` ist das UNIQUE auf
                # `name` - ein anderer Integritätsfehler kann hier nicht auftreten.
                conflicts.append(n)
        else:
            try:
                cur = conn.execute(
                    'INSERT INTO persons (name, wallet_start_date, wallet_start_amount) VALUES (?,?,?)',
                    (n, wd, wa))
                ck = p.get('client_key')
                if ck:
                    id_map[str(ck)] = cur.lastrowid
            except sqlite3.IntegrityError:
                # Jede IntegrityError wird hier als Namenskonflikt gemeldet.
                # Die einzige Constraint auf `persons` ist das UNIQUE auf
                # `name` - ein anderer Integritätsfehler kann hier nicht auftreten.
                conflicts.append(n)

    activity_version = _bump_activity(conn, 'settings')
    conn.commit()
    result = [dict(r) for r in conn.execute('SELECT * FROM persons ORDER BY name')]
    conn.close()
    return jsonify({'success': True, 'persons': result, 'id_map': id_map,
                    'activity_version': activity_version, 'conflicts': conflicts})

@main_bp.route('/api/categories/save', methods=['POST'])
@login_required
def save_categories():
    d = request.get_json(silent=True)
    stale = _require_v2(d)
    if stale:
        return stale
    conn = get_db()
    id_map = {}
    conflicts = []

    for raw in (d.get('deleted_ids') or []):
        try:
            did = int(raw)
        except (TypeError, ValueError):
            continue
        conn.execute('DELETE FROM categories WHERE id=?', (did,))

    for c in (d.get('rows') or []):
        n = (c.get('name') or '').strip()
        if not n:
            continue
        # `color` ist NOT NULL in der DB - c.get(key, default) greift nur wenn
        # der Key fehlt, nicht wenn der Client explizit `color: null` sendet.
        col = c.get('color') or '#6c757d'
        sp = 1 if c.get('is_split') else 0
        try:
            cid = int(c['id']) if c.get('id') else None
        except (TypeError, ValueError):
            cid = None

        # Namen sind UNIQUE. Kollisionen werden gemeldet statt verschluckt.
        if cid is not None:
            try:
                conn.execute('UPDATE categories SET name=?, color=?, is_split=? WHERE id=?',
                             (n, col, sp, cid))
            except sqlite3.IntegrityError:
                # Jede IntegrityError wird hier als Namenskonflikt gemeldet.
                # `color` ist oben gegen None abgesichert, daher bleibt
                # praktisch nur die UNIQUE-Verletzung auf `name` übrig.
                conflicts.append(n)
                cid = None   # Splits dieser Zeile nicht anfassen
        else:
            try:
                cur = conn.execute(
                    'INSERT INTO categories (name, color, is_split) VALUES (?,?,?)', (n, col, sp))
                cid = cur.lastrowid
                ck = c.get('client_key')
                if ck:
                    id_map[str(ck)] = cid
            except sqlite3.IntegrityError:
                # Jede IntegrityError wird hier als Namenskonflikt gemeldet.
                # `color` ist oben gegen None abgesichert, daher bleibt
                # praktisch nur die UNIQUE-Verletzung auf `name` übrig.
                conflicts.append(n)
                cid = None

        if cid is not None:
            # Aufteilungsverhältnis dieser einen Kategorie neu setzen. Bewusst
            # weiterhin Replace-All, aber auf die Kategorie begrenzt.
            conn.execute('DELETE FROM category_splits WHERE category_id=?', (cid,))
            for pid_str, share in (c.get('splits') or {}).items():
                try:
                    pid = int(pid_str)
                    share_v = float(share)
                except (TypeError, ValueError):
                    continue
                if share_v > 0:
                    conn.execute(
                        'INSERT INTO category_splits (category_id, person_id, share) VALUES (?,?,?)',
                        (cid, pid, share_v))

    activity_version = _bump_activity(conn, 'settings')
    conn.commit()
    result = [dict(r) for r in conn.execute('SELECT * FROM categories ORDER BY name')]
    conn.close()
    return jsonify({'success': True, 'categories': result, 'id_map': id_map,
                    'activity_version': activity_version, 'conflicts': conflicts})





@main_bp.route('/api/import/csv', methods=['POST'])
@login_required
def import_csv():
    import csv, io as _io
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': t('srv.no_file')}), 400
    overwrite = request.form.get('overwrite','false') == 'true'
    try:
        content = request.files['file'].stream.read().decode('utf-8-sig')
    except Exception as e:
        current_app.logger.exception("CSV-Datei konnte nicht gelesen werden")
        return jsonify({'success': False, 'error': t('srv.csv_read_error')}), 400
    rows = list(csv.reader(_io.StringIO(content), delimiter=';'))
    if len(rows) < 2:
        return jsonify({'success': False, 'error': t('srv.csv_empty')}), 400
    conn = get_db()
    persons = {r['name']: r['id'] for r in conn.execute('SELECT id,name FROM persons')}
    cats    = {r['name']: r['id'] for r in conn.execute('SELECT id,name FROM categories')}
    # collect affected months for optional overwrite
    affected = set()
    for row in rows[1:]:
        if len(row) < 4: continue
        try: affected.add((int(row[1]), int(row[2])))
        except: pass
    if overwrite:
        for y,m in affected:
            conn.execute('DELETE FROM expenses WHERE year=? AND month=?',(y,m))
            conn.execute('DELETE FROM income   WHERE year=? AND month=?',(y,m))
    imported = ok = 0; skipped = []
    for i, row in enumerate(rows[1:], 2):
        if len(row) < 8: skipped.append(f'Zeile {i}: zu wenig Spalten'); continue
        try:
            typ  = row[0].strip()
            year,month,day = int(row[1]),int(row[2]),int(row[3])
            person = row[4].strip(); category = row[5].strip()
            desc   = row[6].strip()
            amount = float(row[7].replace(',','.').replace(' ',''))
            pid = persons.get(person)
            if typ == 'Ausgabe':
                is_cash = len(row)>8 and row[8].strip()=='Ja'
                cid = cats.get(category)
                conn.execute('INSERT INTO expenses (year,month,day,description,person_id,category_id,amount,is_cash) VALUES (?,?,?,?,?,?,?,?)',
                             (year,month,day,desc,pid,cid,amount,1 if is_cash else 0))
                imported += 1
            elif typ == 'Einnahme':
                conn.execute('INSERT INTO income (year,month,day,description,person_id,amount) VALUES (?,?,?,?,?,?)',
                             (year,month,day,desc,pid,amount))
                imported += 1
            else: skipped.append(f'Zeile {i}: unbekannter Typ "{typ}"')
        except Exception as e: skipped.append(f'Zeile {i}: {e}')
    # Betroffene Monate koennen bei jemand anderem offen sein (v.a. bei
    # overwrite=true, das ihre Zeilen-ids unter der Hand entfernt) - Banner
    # ueber den Aenderungszaehler anstossen.
    for y, m in affected:
        _bump_activity(conn, _month_scope(y, m))
    conn.commit(); conn.close()
    month_links = sorted([{'year':y,'month':m} for y,m in affected], key=lambda x:(x['year'],x['month']))
    return jsonify({'success': True, 'imported': imported,
                    'months': len(affected), 'month_links': month_links,
                    'skipped': len(skipped),
                    'skipped_details': skipped[:10], 'overwrite': overwrite})

@main_bp.route('/api/export/csv')
@login_required
def export_csv():
    import csv, io
    year  = request.args.get('year',  type=int)
    month = request.args.get('month', type=int)
    conn  = get_db()
    q = """SELECT e.year, e.month, e.day,
               COALESCE(p.name,'')  as person,
               COALESCE(c.name,'Sonstige') as category,
               e.description, e.amount, e.is_cash
        FROM expenses e
        LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id"""
    params = []
    if year and month:   q += ' WHERE e.year=? AND e.month=?'; params=[year,month]
    elif year:           q += ' WHERE e.year=?';               params=[year]
    q += ' ORDER BY e.year,e.month,e.day'
    rows = conn.execute(q, params).fetchall()
    inc_q = 'SELECT i.year,i.month,i.day, COALESCE(p.name,\'\') as person, i.description, i.amount FROM income i LEFT JOIN persons p ON i.person_id=p.id'
    inc_params = []
    if year and month:   inc_q += ' WHERE i.year=? AND i.month=?'; inc_params=[year,month]
    elif year:           inc_q += ' WHERE i.year=?';               inc_params=[year]
    inc_rows = conn.execute(inc_q, inc_params).fetchall()
    conn.close()
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['Typ','Jahr','Monat','Tag','Person','Kategorie','Beschreibung','Betrag (€)','Barzahlung'])
    for r in rows:
        w.writerow(['Ausgabe',r['year'],r['month'],r['day'],r['person'],r['category'],
                    r['description'],str(r['amount']).replace('.',','),'Ja' if r['is_cash'] else 'Nein'])
    for r in inc_rows:
        w.writerow(['Einnahme',r['year'],r['month'],r['day'],r['person'],'',
                    r['description'],str(r['amount']).replace('.',','),''])
    out.seek(0)
    if year and month: fname=f'kassenstürzle_{year}_{month:02d}.csv'
    elif year:         fname=f'kassenstürzle_{year}.csv'
    else:              fname='kassenstürzle_export.csv'
    return Response(out.getvalue().encode('utf-8-sig'), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment;filename={fname}'})

@main_bp.route('/api/export/years')
@login_required
def export_years():
    conn = get_db()
    years = [r['year'] for r in conn.execute('SELECT DISTINCT year FROM expenses ORDER BY year DESC')]
    conn.close()
    return jsonify({'years': years})

@main_bp.route('/api/recurring/save', methods=['POST'])
@login_required
def save_recurring():
    d = request.get_json(silent=True)
    stale = _require_v2(d)
    if stale:
        return stale
    conn = get_db()
    id_map = {}

    for raw in (d.get('deleted_ids') or []):
        try:
            did = int(raw)
        except (TypeError, ValueError):
            continue
        conn.execute('DELETE FROM recurring_expenses WHERE id=?', (did,))

    for r in (d.get('rows') or []):
        try:
            amt = float(r.get('amount') or 0)
        except (TypeError, ValueError):
            amt = 0
        desc = (r.get('description') or '').strip()
        try:
            rid = int(r['id']) if r.get('id') else None
        except (TypeError, ValueError):
            rid = None

        if not desc and amt == 0:
            # Geleerte Zeile: bestehende Zeile löschen, nie ausgefüllte ignorieren
            if rid is not None:
                conn.execute('DELETE FROM recurring_expenses WHERE id=?', (rid,))
            continue
        pid  = int(r['person_id'])   if r.get('person_id')   else None
        cid  = int(r['category_id']) if r.get('category_id') else None
        freq = r.get('frequency', 'monthly')
        sm   = int(r.get('start_month', 1)) if freq != 'monthly' else 1
        day  = int(r.get('day') or 1)

        if rid is not None:
            conn.execute(
                'UPDATE recurring_expenses SET day=?, description=?, person_id=?, category_id=?,'
                ' amount=?, frequency=?, start_month=? WHERE id=?',
                (day, desc, pid, cid, amt, freq, sm, rid))
        else:
            cur = conn.execute(
                'INSERT INTO recurring_expenses (day,description,person_id,category_id,amount,'
                'frequency,start_month,active) VALUES (?,?,?,?,?,?,?,1)',
                (day, desc, pid, cid, amt, freq, sm))
            ck = r.get('client_key')
            if ck:
                id_map[str(ck)] = cur.lastrowid

    activity_version = _bump_activity(conn, 'settings')
    conn.commit()
    result = [dict(r) for r in conn.execute('SELECT * FROM recurring_expenses ORDER BY id')]
    conn.close()
    return jsonify({'success': True, 'recurring': result,
                    'id_map': id_map, 'activity_version': activity_version})

@main_bp.route('/api/recurring/for-month/<int:year>/<int:month>')
@login_required
def recurring_for_month(year, month):
    conn = get_db()
    result = get_recurring_for_month(year, month, conn)
    conn.close()
    return jsonify({'recurring': result})

@main_bp.route('/api/income/save', methods=['POST'])
@login_required
def save_income():
    d = request.get_json(silent=True)
    stale = _require_v2(d)
    if stale:
        return stale
    year, month = int(d['year']), int(d['month'])
    rows = d.get('rows') or []
    deleted_ids = d.get('deleted_ids') or []
    conn = get_db()
    id_map = {}

    for raw in deleted_ids:
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        conn.execute('DELETE FROM income WHERE id=? AND year=? AND month=?', (rid, year, month))

    for i in rows:
        try:
            amt = float(i.get('amount') or 0)
        except (TypeError, ValueError):
            amt = 0
        try:
            rid = int(i['id']) if i.get('id') else None
        except (TypeError, ValueError):
            rid = None

        if amt == 0:
            if rid is not None:
                conn.execute('DELETE FROM income WHERE id=? AND year=? AND month=?',
                             (rid, year, month))
            continue

        pid  = int(i['person_id'])   if i.get('person_id')   else None
        cid  = int(i['category_id']) if i.get('category_id') else None
        day  = int(i.get('day') or 1)
        desc = i.get('description', '')

        if rid is not None:
            conn.execute(
                'UPDATE income SET day=?, description=?, person_id=?, category_id=?, amount=?'
                ' WHERE id=? AND year=? AND month=?',
                (day, desc, pid, cid, amt, rid, year, month))
        else:
            cur = conn.execute(
                'INSERT INTO income (year,month,day,description,person_id,category_id,amount)'
                ' VALUES (?,?,?,?,?,?,?)',
                (year, month, day, desc, pid, cid, amt))
            ck = i.get('client_key')
            if ck:
                id_map[str(ck)] = cur.lastrowid

    activity_version = _bump_activity(conn, _month_scope(year, month))
    conn.commit()

    inc_sum = conn.execute('SELECT COALESCE(SUM(amount),0) as t FROM income   WHERE year=? AND month=?', (year, month)).fetchone()['t']
    exp_sum = conn.execute('SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE year=? AND month=?', (year, month)).fetchone()['t']
    result_rows = [dict(r) for r in conn.execute('SELECT * FROM income WHERE year=? AND month=? ORDER BY day,id', (year, month))]
    spp = calculate_savings_per_person(year, month, conn)
    conn.close()
    return jsonify({'success': True, 'income': result_rows,
                    'id_map': id_map, 'activity_version': activity_version,
                    'income_total': round(inc_sum, 2), 'expense_total': round(exp_sum, 2),
                    'savings': round(inc_sum - exp_sum, 2), 'savings_pp': spp})

@main_bp.route('/api/month/settle', methods=['POST'])
@login_required
def settle_month():
    d = request.json; year, month = d['year'], d['month']
    settled = 1 if d.get('settled') else 0
    from datetime import datetime as dt
    settled_at = dt.now().strftime('%d.%m.%Y %H:%M') if settled else None
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO month_status (year,month,settled,settled_at) VALUES (?,?,?,?)",
                 (year, month, settled, settled_at))
    _bump_activity(conn, _month_scope(int(year), int(month)))
    conn.commit()
    ov_p, ov_c = build_ov(conn, year, month, bool(settled))
    settlement = calculate_settlement(year, month, conn)
    ov_settlements = calculate_category_settlements(year, month, conn)
    if settled and ov_settlements:
        all_pnames = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]
        ov_p = apply_settlements_to_ov(ov_p, ov_settlements, all_pnames)
    wallet = calculate_wallet(year, month, conn)
    savings_pp = calculate_savings_per_person(
        year, month, conn, bool(settled)
    )
    conn.close()
    return jsonify({'success': True, 'settled': bool(settled), 'settled_at': settled_at,
        'ov_p': {k: dict(v) for k,v in ov_p.items()}, 'ov_c': ov_c,
        'settlement': settlement, 'ov_settlements': ov_settlements, 'wallet': wallet, 'savings_pp': savings_pp})

def get_category_splits_map(conn):
    """Liefert {category_id: {person_id: share}} für die Einstellungen-Seite
    (Vorbelegung der Anteils-Eingabefelder)."""
    rows = conn.execute('SELECT category_id, person_id, share FROM category_splits').fetchall()
    m = {}
    for r in rows:
        m.setdefault(r['category_id'], {})[r['person_id']] = r['share']
    return m


@main_bp.route('/settings')
@login_required
def settings():
    conn = get_db()
    # Version VOR den Datenabfragen lesen (siehe month_view): ein Save, das
    # dazwischen landet, darf höchstens einen unnötigen Banner auslösen,
    # niemals einen verpassten.
    _act_row = conn.execute("SELECT version FROM activity WHERE scope='settings'").fetchone()
    activity_version = _act_row['version'] if _act_row else 0
    persons   = [dict(r) for r in conn.execute('SELECT * FROM persons ORDER BY name')]
    cats      = [dict(r) for r in conn.execute('SELECT * FROM categories ORDER BY name')]
    recurring = [dict(r) for r in conn.execute('SELECT * FROM recurring_expenses ORDER BY id')]
    category_splits = get_category_splits_map(conn)
    conn.close()
    return render_template('settings.html', active='settings', persons=persons, categories=cats,
                            recurring=recurring, category_splits=category_splits,
                            activity_version=activity_version)



def compute_chart_data(conn, settled_ovs=None):
    """settled_ovs: optionales Cache-Dict {(year,month): calculate_category_settlements(...)}
    um wiederholte, teure Settlement-Berechnungen pro Monat zu vermeiden (siehe overview())."""
    rows = conn.execute("""
        SELECT e.year, e.month, e.day,
               COALESCE(p.name,'Unbekannt') as person,
               COALESCE(c.name,'Sonstige')  as category,
               COALESCE(c.color,'#6c757d')  as color,
               e.amount
        FROM expenses e
        LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id
        ORDER BY e.year, e.month, e.day""").fetchall()
    persons = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]
    cats    = [{'name':r['name'],'color':r['color']}
               for r in conn.execute('SELECT name,color FROM categories ORDER BY name')]
    months  = [(r['year'],r['month'])
               for r in conn.execute('SELECT DISTINCT year,month FROM expenses ORDER BY year,month')]
    settled_months = {(r['year'],r['month'])
               for r in conn.execute('SELECT year,month FROM month_status WHERE settled=1')}
    month_sett = {}
    for (y,m) in settled_months:
        month_sett[(y,m)] = (settled_ovs.get((y,m)) if settled_ovs is not None
                              else calculate_category_settlements(y, m, conn))
    from collections import defaultdict
    agg  = defaultdict(float); meta = {}
    for r in rows:
        k = (r['year'],r['month'],r['person'],r['category'])
        agg[k] += r['amount']
        meta[(r['year'],r['month'],r['category'])] = r['color']
    adjusted = []
    for (y,m,p,cn), total in agg.items():
        net = total
        if (y,m) in month_sett:
            adj = month_sett[(y,m)].get(cn,{}).get(p,0)
            net = round(total - adj, 2)
        if net > 0.005:
            adjusted.append({'year':y,'month':m,'day':1,'person':p,'category':cn,
                              'color':meta.get((y,m,cn),'#6c757d'),'amount':net})
    return {'rows':adjusted,'persons':persons,'categories':cats,
            'months':[list(m) for m in months],'month_names':MONTH_NAMES,
            'settled_months':[list(m) for m in settled_months]}

@main_bp.route('/api/chart-data')
@login_required
def chart_data():
    conn = get_db()
    data = compute_chart_data(conn)
    conn.close()
    return jsonify(data)
def debug_overview():
    conn = get_db()
    mk = [(r['year'],r['month']) for r in conn.execute(
        'SELECT DISTINCT year,month FROM expenses ORDER BY year DESC, month DESC')]
    data = conn.execute("""
        SELECT e.year, e.month, p.name as pname, c.name as cname, c.color, SUM(e.amount) as total
        FROM expenses e LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id
        GROUP BY e.year,e.month,e.person_id,e.category_id""").fetchall()
    structured, cats_map = {}, {}
    for r in data:
        pn = r['pname'] or 'Unbekannt'
        cn = r['cname'] or 'Sonstige'
        cats_map[cn] = r['color'] or '#6c757d'
        mk2 = f"{r['year']}-{r['month']}"
        structured.setdefault(pn, {}).setdefault(mk2, {})[cn] = r['total']
    conn.close()
    return jsonify({'status': 'use /overview for full data'})

@main_bp.route('/overview')
@login_required
def overview():
    conn = get_db()
    month_keys = [(r['year'],r['month']) for r in conn.execute(
        'SELECT DISTINCT year,month FROM expenses UNION SELECT DISTINCT year,month FROM income ORDER BY year DESC, month DESC')]
    settled_months = {(r['year'],r['month'])
        for r in conn.execute('SELECT year,month FROM month_status WHERE settled=1')}
    all_pnames = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]

    # ── Expense structured ───────────────────────────────────────────────────
    # Normal expenses (not paid-for)
    exp_data = conn.execute("""
        SELECT e.year, e.month, p.name as pname, c.name as cname, c.color, SUM(e.amount) as total
        FROM expenses e LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id
        WHERE e.paid_for_person_id IS NULL
        GROUP BY e.year,e.month,e.person_id,e.category_id""").fetchall()
    # Paid-for expenses: count under the payee (paid_for_person_id)
    exp_data_pf = conn.execute("""
        SELECT e.year, e.month, payee.name as pname, c.name as cname, c.color, SUM(e.amount) as total
        FROM expenses e LEFT JOIN persons payee ON e.paid_for_person_id=payee.id
        LEFT JOIN categories c ON e.category_id=c.id
        WHERE e.paid_for_person_id IS NOT NULL
        GROUP BY e.year,e.month,e.paid_for_person_id,e.category_id""").fetchall()

    cats_map = {}
    structured = {}
    for r in list(exp_data) + list(exp_data_pf):
        pn = r['pname'] or 'Unbekannt'; cn = r['cname'] or 'Sonstige'
        cats_map[cn] = r['color'] or '#6c757d'
        mk = f"{r['year']}-{r['month']}"
        prev = structured.setdefault(pn, {}).setdefault(mk, {}).get(cn, 0)
        structured[pn][mk][cn] = prev + r['total']

    # calculate_category_settlements() ist teuer (mehrere JOIN-Queries) und wird
    # weiter unten für gesamt_structured/chart_data/settled_sett_delta erneut
    # benötigt – daher einmal pro abgerechnetem Monat berechnen und cachen.
    settled_ovs = {(y, m): calculate_category_settlements(y, m, conn) for (y, m) in settled_months}

    # settlement adjustments – keep all values including 0 and negative
    for (y, m) in settled_months:
        ovs = settled_ovs[(y, m)]
        if not ovs: continue
        smk = f'{y}-{m}'
        for p in all_pnames:
            for cn, person_sett in ovs.items():
                paid = structured.get(p, {}).get(smk, {}).get(cn, 0)
                adj  = person_sett.get(p, 0)
                net  = round(paid - adj, 2)
                structured.setdefault(p, {}).setdefault(smk, {})[cn] = net

    # ── Income structured (by category) ─────────────────────────────────────
    inc_data = conn.execute("""
        SELECT i.year, i.month, p.name as pname, c.name as cname, c.color, SUM(i.amount) as total
        FROM income i LEFT JOIN persons p ON i.person_id=p.id
        LEFT JOIN categories c ON i.category_id=c.id
        GROUP BY i.year,i.month,i.person_id,i.category_id""").fetchall()

    inc_structured = {}
    for r in inc_data:
        pn = r['pname'] or 'Unbekannt'; cn = r['cname'] or 'Sonstige'
        cats_map[cn] = r['color'] or '#6c757d'
        mk = f"{r['year']}-{r['month']}"
        inc_structured.setdefault(pn, {}).setdefault(mk, {})[cn] = round(r['total'], 2)

    # ── Gesamt (settled months only): Ausgaben + Einnahmen per category ──────
    gesamt_structured = {}
    for (y, m) in settled_months:
        mk = f'{y}-{m}'
        for pn in all_pnames:
            exp_cats = structured.get(pn, {}).get(mk, {})
            inc_cats = inc_structured.get(pn, {}).get(mk, {})
            all_cn = set(exp_cats) | set(inc_cats)
            if not all_cn: continue
            for cn in all_cn:
                val = round(exp_cats.get(cn, 0) - inc_cats.get(cn, 0), 2)
                gesamt_structured.setdefault(pn, {}).setdefault(mk, {})[cn] = val

    # ── Income map for savings bar ───────────────────────────────────────────
    inc_rows = conn.execute("""
        SELECT i.year,i.month, COALESCE(p.name,'?') as pname, SUM(i.amount) as total
        FROM income i LEFT JOIN persons p ON i.person_id=p.id
        GROUP BY i.year,i.month,i.person_id""").fetchall()
    income_map = {}
    for r in inc_rows:
        income_map.setdefault((r['year'],r['month']),{})[r['pname']] = round(r['total'],2)

    exp_totals_by_month = {}
    for pn, months in structured.items():
        for mk3, cats in months.items():
            exp_totals_by_month[mk3] = exp_totals_by_month.get(mk3,0) + sum(cats.values())

    chart_data_obj = compute_chart_data(conn, settled_ovs)

    persons_list = sorted(set(list(structured.keys()) + list(inc_structured.keys())))

    # ── Personen-Gesamtübersicht fuer AP6 ─────────────────────────────────────
    person_totals = {}
    for pn in persons_list:
        inc_total = round(sum(
            sum(cats.values())
            for cats in inc_structured.get(pn, {}).values()
        ), 2)
        exp_total = round(sum(
            sum(cats.values())
            for cats in structured.get(pn, {}).values()
        ), 2)
        person_totals[pn] = {
            'inc': inc_total,
            'exp': exp_total,
            'savings': round(inc_total - exp_total, 2)
        }

    # ── Personen-Übersicht nach Jahren (AP6 erweitert) ─────────────────────
    # Settlement-Ausgleichsbeträge pro Person pro Monat (nur abgerechnete Monate)
    # calculate_category_settlements() arbeitet unabhängig vom is_settled-Flag
    # settlement_delta[pn] = paid - fair_share (positiv = mehr gezahlt = bekommt zurück)
    settled_sett_delta = {}  # (y,m) -> {pname: delta}
    for (y, m) in settled_months:
        ovs = settled_ovs[(y, m)]
        if not ovs:
            continue
        delta_by_person = {}
        for cn_s, person_map in ovs.items():
            for pn_s, val in person_map.items():
                delta_by_person[pn_s] = round(delta_by_person.get(pn_s, 0.0) + val, 2)
        settled_sett_delta[(y, m)] = delta_by_person

    person_totals_by_year = {}
    all_years = sorted({y for y, m in set(month_keys)}, reverse=True)
    for pn in persons_list:
        pn_years = {}
        for y in all_years:
            year_months = [m for (yy, m) in set(month_keys) if yy == y]
            if not year_months:
                continue
            year_months_sorted = sorted(year_months, reverse=True)
            month_rows = []
            ytot_inc = ytot_exp = 0.0
            for m in year_months_sorted:
                mk = f'{y}-{m}'
                # Einnahmen dieser Person in diesem Monat
                m_inc = round(sum(inc_structured.get(pn, {}).get(mk, {}).values()), 2)
                # Ausgaben dieser Person in diesem Monat (inkl. Settlement-Korrektur)
                m_exp = round(sum(structured.get(pn, {}).get(mk, {}).values()), 2)
                m_sav = round(m_inc - m_exp, 2)
                is_settled = (y, m) in settled_months
                # Settlement-Delta: was hat diese Person netto bekommen/gezahlt?
                # Positiv = hat mehr als fair share gezahlt = bekommt Geld zurück
                # Negativ = hat weniger gezahlt = musste Geld zahlen
                sett_delta = settled_sett_delta.get((y, m), {}).get(pn, None)
                month_rows.append({
                    'month': m,
                    'mk': mk,
                    'inc': m_inc,
                    'exp': m_exp,
                    'savings': m_sav,
                    'settled': is_settled,
                    'sett_delta': sett_delta,
                })
                ytot_inc += m_inc
                ytot_exp += m_exp
            if any(r['inc'] != 0 or r['exp'] != 0 for r in month_rows):
                pn_years[y] = {
                    'inc': round(ytot_inc, 2),
                    'exp': round(ytot_exp, 2),
                    'savings': round(ytot_inc - ytot_exp, 2),
                    'months': month_rows,
                }
        if pn_years:
            person_totals_by_year[pn] = pn_years

    # ── Jahresgrupierung fuer AP5 ──────────────────────────────────────────────
    months_by_year = {}
    for y, m in sorted(set(month_keys), reverse=True):
        mk = f'{y}-{m}'
        inc_total = round(sum(income_map.get((y,m), {}).values()), 2)
        exp_total = round(exp_totals_by_month.get(mk, 0), 2)
        months_by_year.setdefault(y, []).append({
            'month': m, 'mk': mk,
            'inc': inc_total, 'exp': exp_total,
            'savings': round(inc_total - exp_total, 2),
            'settled': (y, m) in settled_months,
        })
    # Sort years desc, months desc within each year
    months_by_year = {y: sorted(ms, key=lambda x: -x['month'])
                      for y, ms in sorted(months_by_year.items(), reverse=True)}

    # ── Kompakte Daten für die Diagramme auf der Startseite ──────────────────
    # (settlement-bereinigt, wie überall sonst in dieser Ansicht - daher aus
    # `structured`/`income_map`/`exp_totals_by_month` abgeleitet statt aus
    # compute_chart_data(), das nur rohe Ausgaben ohne Einnahmen liefert)
    category_by_month = {}
    for pn, pn_months in structured.items():
        for mk, cats in pn_months.items():
            bucket = category_by_month.setdefault(mk, {})
            for cn, val in cats.items():
                bucket[cn] = round(bucket.get(cn, 0) + val, 2)

    months_asc = sorted(set(month_keys))
    chart_months = [{
        'key': f'{y}-{m}',
        'label': f'{MONTH_NAMES[m-1][:3]} {y}',
        'year': y,
        'inc': round(sum(income_map.get((y, m), {}).values()), 2),
        'exp': round(exp_totals_by_month.get(f'{y}-{m}', 0), 2),
    } for y, m in months_asc]

    overview_chart_json = {
        'months': chart_months,
        'categories': [{'name': cn, 'color': col} for cn, col in sorted(cats_map.items())],
        'category_by_month': category_by_month,
    }

    grand_income  = round(sum(pt['inc'] for pt in person_totals.values()), 2)
    grand_expense = round(sum(pt['exp'] for pt in person_totals.values()), 2)
    grand_savings = round(grand_income - grand_expense, 2)
    open_settlements_count = len(set(month_keys) - settled_months)

    conn.close()
    return render_template('overview.html', active='overview',
        structured=structured,
        inc_structured=inc_structured,
        gesamt_structured=gesamt_structured,
        all_cats=sorted(cats_map.items()),
        month_keys=month_keys,
        month_names=MONTH_NAMES,
        persons_list=persons_list,
        settled_months=[(y,m) for y,m in settled_months],
        income_map={(f'{k[0]}-{k[1]}'):v for k,v in income_map.items()},
        exp_totals_by_month={k:round(v,2) for k,v in exp_totals_by_month.items()},
        chart_data=chart_data_obj,
        overview_chart_json=overview_chart_json,
        grand_income=grand_income,
        grand_expense=grand_expense,
        grand_savings=grand_savings,
        open_settlements_count=open_settlements_count,
        months_by_year=months_by_year,
        person_totals=person_totals,
        person_totals_by_year=person_totals_by_year)


# ── PDF Kontoauszug Import ─────────────────────────────────────────────────────

def parse_german_amount(s):
    """Convert German amount string like -1.234,56 or 1.234,56+ to float."""
    s = str(s or '').strip().replace('\xa0','').replace('\u202f','').replace(' ','')
    if not s: return None
    sign = 1
    if s.endswith('+'): s = s[:-1]
    elif s.endswith('-'): sign = -1; s = s[:-1]
    elif s.startswith('-'): sign = -1; s = s[1:]
    elif s.startswith('+'): s = s[1:]
    try:
        return sign * float(s.replace('.','').replace(',','.'))
    except:
        return None


def extract_bank_statement(pdf_bytes):
    """
    Parse a German DKB bank statement PDF via plain-text extraction.
    The table is malformed (entire page in one row), so we parse the text layer.
    Format per transaction:
      DD.MM.YYYYType [-Amount]
      counterpart / detail lines...
    """
    try:
        import pdfplumber
    except ImportError:
        return {'error': t('srv.pdf_lib_missing')}

    import re, io, gc
    from datetime import datetime

    AMT_RE      = re.compile(r'(\-?\d{1,3}(?:\.\d{3})*,\d{2})')
    ISO_DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')

    TYPE_KEYWORDS = [
        'Kartenzahlung onl', 'Kartenzahlung',
        'Basislastschrift', 'Zahlungseingang',
        'Überweisung', 'Ueberweisung', 'Dauerauftrag', 'Gutschrift',
        'LOHN, GEHALT, RENTE', 'LOHN', 'GEHALT', 'RENTE',
    ]
    # Regex to detect a transaction-start line: DDMMYYYYType...
    DATE_PREFIX_RE = re.compile(r'^(\d{2}\.\d{2}\.\d{4})(.*)')
    SKIP_PREFIXES  = ('Kontostand am', 'Abrechnung', 'Saldo', 'Anfangssaldo',
                      'Endsaldo', 'Kontonummer', 'BIC', 'IBAN', 'Debit',
                      'Gläubiger-ID', 'Zahl.System')

    # ── Read & wipe ──────────────────────────────────────────────────────────
    pdf_io = io.BytesIO(bytes(pdf_bytes))
    try:
        pdf_bytes[:] = b'\x00' * len(pdf_bytes)
    except Exception:
        pass
    del pdf_bytes; gc.collect()

    holder = ''; bank = 'DKB'
    full_text = ''

    try:
        with pdfplumber.open(pdf_io) as pdf:
            pages_text = []
            for page in pdf.pages:
                t = page.extract_text() or ''
                pages_text.append(t)
            full_text = '\n'.join(pages_text)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).exception("PDF-Text konnte nicht extrahiert werden")
        return {'error': t('srv.pdf_unreadable')}
    finally:
        pdf_io.close(); del pdf_io; gc.collect()

    # Detect holder from text
    for pat in [r'Herrn\s+([A-ZÄÖÜ][^\n]+)',
                r'Frau\s+([A-ZÄÖÜ][^\n]+)',
                r'Kontoinhaber[:\s]+([A-ZÄÖÜ][^\n]+)']:
        m = re.search(pat, full_text)
        if m: holder = m.group(1).strip()[:60]; break

    # Detect bank
    for b in ['DKB','Deutsche Kreditbank','Sparkasse','ING','Volksbank',
              'Raiffeisenbank','Commerzbank','Deutsche Bank','Postbank','N26']:
        if b.lower() in full_text.lower(): bank = b; break

    # ── Parse plain text line by line ────────────────────────────────────────
    # Each transaction starts with: DD.MM.YYYYType ... [-amount]
    # Subsequent lines until next date-prefixed line are detail/counterpart

    lines = full_text.splitlines()

    # Group into transaction blocks
    blocks = []   # each: {'date': str, 'type': str, 'amount': float|None,
                  #         'is_income': bool, 'detail_lines': [str]}
    current = None

    for line in lines:
        line = line.strip()
        if not line: continue

        m = DATE_PREFIX_RE.match(line)
        if m:
            date_str = m.group(1)
            rest     = m.group(2).strip()

            # Try to match type keyword
            matched_type = None
            remainder    = rest
            for kw in TYPE_KEYWORDS:
                if rest.upper().startswith(kw.upper()):
                    matched_type = kw
                    remainder    = rest[len(kw):].strip()
                    break

            if not matched_type:
                # Could be "Kontostand am..." or page header – skip
                if current: blocks.append(current)
                current = None
                continue

            # Extract amount from remainder (at end of line)
            amt = None
            amt_m = AMT_RE.search(remainder)
            if amt_m:
                amt = parse_german_amount(amt_m.group(1))
                remainder = remainder[:amt_m.start()].strip()

            if current: blocks.append(current)
            current = {
                'date': date_str, 'type': matched_type,
                'amount': amt, 'remainder': remainder,
                'detail_lines': []
            }
        else:
            # Continuation line
            if current is None: continue
            # Skip pure noise lines
            if any(line.startswith(s) for s in SKIP_PREFIXES): continue
            # If no amount yet, try to extract from this line
            if current['amount'] is None:
                amt_m = AMT_RE.search(line)
                if amt_m:
                    current['amount'] = parse_german_amount(amt_m.group(1))
                    rest_line = line[:amt_m.start()].strip()
                    if rest_line: current['detail_lines'].append(rest_line)
                    continue
            current['detail_lines'].append(line)

    if current: blocks.append(current)

    # ── Convert blocks to transactions ───────────────────────────────────────
    # Ob eine Buchung eine Einnahme oder Ausgabe ist, entscheidet ausschließlich
    # das Vorzeichen des im Kontoauszug gedruckten Betrags (negativ = Ausgabe,
    # positiv = Einnahme) - NICHT der Buchungstyp. Banken wie die DKB verwenden
    # denselben Typ ("Überweisung", "Dauerauftrag") für ein- UND ausgehende
    # Buchungen; eine Zuordnung anhand des Typs würde eingehende Überweisungen
    # (z.B. Rückerstattungen, Gutschriften von Dritten) fälschlich als Ausgabe
    # einstufen.
    transactions  = []
    skipped_types = {}

    for blk in blocks:
        if blk['amount'] is None: continue

        tl      = blk['type']
        tl_up   = tl.upper().strip()
        amt     = abs(round(blk['amount'], 2))
        is_inc  = blk['amount'] > 0

        try:
            col1_dt = datetime.strptime(blk['date'], '%d.%m.%Y')
        except: continue

        day, month, year = col1_dt.day, col1_dt.month, col1_dt.year
        counterpart = ''
        description = ''
        detail_all  = [blk['remainder']] + blk['detail_lines']
        detail_all  = [d for d in detail_all if d]

        # ── Kartenzahlung ─────────────────────────────────────────────────────
        if tl_up.startswith('KARTENZAHLUNG'):
            content = ' '.join(detail_all)
            parts   = [p.strip() for p in content.split('/')]
            counterpart = parts[0] if parts else ''
            location    = parts[1] if len(parts) > 1 else ''
            # Use ISO date from content as real transaction date
            iso_m = ISO_DATE_RE.search(content)
            if iso_m:
                try:
                    dt2 = datetime.strptime(iso_m.group(1), '%Y-%m-%d')
                    day, month, year = dt2.day, dt2.month, dt2.year
                except: pass
            description = counterpart + (f' / {location}' if location else '')

        # ── Basislastschrift ──────────────────────────────────────────────────
        elif tl_up.startswith('BASISLASTSCHRIFT'):
            counterpart = detail_all[0] if detail_all else ''
            description = ' '.join(detail_all[1:])

        # ── Zahlungseingang / Gutschrift ──────────────────────────────────────
        elif tl_up.startswith('ZAHLUNGSEINGANG') or tl_up.startswith('GUTSCHRIFT'):
            first = ' '.join(detail_all)
            counterpart = first.split('/')[0].strip()
            description = first

        # ── LOHN / GEHALT / RENTE ─────────────────────────────────────────────
        elif any(tl_up.startswith(t.upper()) for t in ['LOHN','GEHALT','RENTE']):
            first = ' '.join(detail_all)
            counterpart = first.split('/')[0].strip()
            description = first

        # ── Überweisung / Dauerauftrag ────────────────────────────────────────
        elif any(tl_up.startswith(t.upper())
                 for t in ['ÜBERWEISUNG','UEBERWEISUNG','DAUERAUFTRAG']):
            counterpart = detail_all[0] if detail_all else ''
            description = ' '.join(detail_all[1:])

        else:
            skipped_types[tl[:40]] = skipped_types.get(tl[:40], 0) + 1
            continue

        transactions.append({
            'date':        f'{day:02d}.{month:02d}.{year}',
            'day':         day, 'month': month, 'year': year,
            'counterpart': counterpart[:80],
            'description': description[:150],
            'amount':      amt,
            'is_income':   is_inc,
            'type':        tl[:40],
        })

    return {'holder': holder, 'bank': bank, 'transactions': transactions}



@main_bp.route('/import/pdf')
@login_required
def import_pdf_page():
    conn = get_db()
    persons = [dict(r) for r in conn.execute('SELECT id,name FROM persons ORDER BY name')]
    conn.close()
    return render_template('import_pdf.html', active='import_pdf', persons=persons)


@main_bp.route('/api/import/pdf/parse', methods=['POST'])
@login_required
def api_parse_pdf():
    import gc, traceback
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': t('srv.no_file')}), 400
    f = request.files['file']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'success': False, 'error': t('srv.only_pdf_allowed')}), 400
    pdf_bytes = None
    try:
        pdf_bytes = bytearray(f.read())
        result = extract_bank_statement(pdf_bytes)
        if 'error' in result:
            return jsonify({'success': False, 'error': result['error']})
        return jsonify({'success': True, **result})
    except Exception as e:
        import uuid as _uuid
        err_id = _uuid.uuid4().hex[:8]
        current_app.logger.exception("PDF-Parse Fehler [%s]", err_id)
        return jsonify({'success': False,
                        'error': t('srv.pdf_error', ref=err_id)})
    finally:
        if pdf_bytes is not None:
            pdf_bytes[:] = b'\x00' * len(pdf_bytes)
            del pdf_bytes
        gc.collect()


@main_bp.route('/api/import/pdf/commit', methods=['POST'])
@login_required
def api_commit_pdf():
    """Save confirmed transactions from PDF import."""
    data      = request.json
    # Frontend sendet 'personid' (ohne Unterstrich) – beide Varianten akzeptieren
    person_id = data.get('person_id') or data.get('personid')
    txns      = data.get('transactions', [])
    if not person_id or not txns:
        current_app.logger.warning("PDF-Commit: Fehlende Daten – person_id=%r, txns_count=%d", person_id, len(txns))
        return jsonify({'success': False, 'error': t('srv.missing_data')}), 400
    conn = get_db()
    imported_exp = imported_inc = 0
    affected_months = set()
    for txn in txns:
        if not txn.get('include'): continue
        try:
            amt  = float(txn['amount'])
            day  = int(txn['day'])
            mon  = int(txn['month'])
            yr   = int(txn['year'])
            desc = str(txn.get('counterpart') or txn.get('description') or '')[:200]
            if txn.get('is_income'):
                conn.execute(
                    'INSERT INTO income (year,month,day,description,person_id,amount) VALUES (?,?,?,?,?,?)',
                    (yr, mon, day, desc, person_id, amt))
                imported_inc += 1
            else:
                conn.execute(
                    'INSERT INTO expenses (year,month,day,description,person_id,amount) VALUES (?,?,?,?,?,?)',
                    (yr, mon, day, desc, person_id, amt))
                imported_exp += 1
            affected_months.add((yr, mon))
        except Exception as e:
            current_app.logger.exception("PDF-Commit Fehler beim Einfügen einer Zeile")
            conn.close()
            return jsonify({'success': False, 'error': t('srv.save_transaction_error')}), 400
    # Betroffene Monate koennen bei jemand anderem offen sein - Banner ueber
    # den Aenderungszaehler anstossen.
    for y, m in affected_months:
        _bump_activity(conn, _month_scope(y, m))
    conn.commit()
    conn.close()
    return jsonify({'success': True,
                   'imported_expenses': imported_exp, 'importedexpenses': imported_exp,
                   'imported_income': imported_inc,  'importedincome':   imported_inc})

def debug_income():
    conn = get_db()
    rows = conn.execute("""
        SELECT i.year, i.month, i.day, i.description, i.amount,
               i.person_id, i.category_id,
               p.name as pname, c.name as cname, c.is_split
        FROM income i
        LEFT JOIN persons p ON i.person_id=p.id
        LEFT JOIN categories c ON i.category_id=c.id
        ORDER BY i.year DESC, i.month DESC, i.day DESC LIMIT 20
    """).fetchall()
    return jsonify([dict(r) for r in rows])

def debug_settlement(year, month):
    conn = get_db()
    result = calculate_settlement(year, month, conn)
    conn.close()
    return jsonify(result)


if __name__ == '__main__':
    init_db()
    # app.run() should not be called from a Blueprint - use run.py

@main_bp.route('/set_language/<lang>')
@login_required
def set_language(lang):
    if lang in SUPPORTED_LANGUAGES:
        session['lang'] = lang
    return redirect(request.referrer or url_for('main.index'))





# --- START BACKUP ENDPOINTS ---
import shutil
from pathlib import Path
# HINWEIS: Hier bewusst KEIN "import sqlite3" - das würde den Namen "sqlite3"
# (der oben ggf. an sqlcipher3 gebunden ist) überschreiben und die
# Verschlüsselung für die gesamte App stillschweigend deaktivieren.

def create_backup(label: str = "") -> Path:
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    suffix = f"_{label}" if label else ""
    db_path = DB_PATH
    backup_dir = Path(os.path.dirname(db_path)) / 'backups'
    backup_dir.mkdir(exist_ok=True)
    dest = backup_dir / f"kassenstuerzle_{ts}{suffix}.db"
    src = _open_db(db_path)
    bak = sqlite3.connect(str(dest))
    if _SQLCIPHER_AVAILABLE and _DB_KEY:
        # Backup ebenfalls verschlüsseln (gleicher Schlüssel wie Hauptdatenbank)
        bak.execute(_pragma_key_sql(_DB_KEY))
    src.backup(bak)
    bak.close()
    src.close()
    existing = sorted(backup_dir.glob("kassenstuerzle_*.db"))
    for old in existing[:-30]:
        old.unlink(missing_ok=True)
    return dest

@main_bp.route('/api/backup/create', methods=['POST'])
@login_required
def api_backup_create():
    import re
    label = request.json.get('label', '')[:50]
    label = re.sub(r'[^a-zA-Z0-9_-]', '_', label)
    dest = create_backup(label)
    stat = dest.stat()
    return jsonify({"success": True, "filename": dest.name, "size_kb": round(stat.st_size / 1024, 1)})

@main_bp.route('/api/backup/list')
@login_required
def api_backup_list():
    from datetime import datetime
    db_path = DB_PATH
    backup_dir = Path(os.path.dirname(db_path)) / 'backups'
    backups = []
    if backup_dir.exists():
        for f in sorted(backup_dir.glob("kassenstuerzle_*.db"), reverse=True):
            stat = f.stat()
            backups.append({
                "filename": f.name,
                "size_kb": round(stat.st_size / 1024, 1),
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M")
            })
    return jsonify({"backups": backups})

@main_bp.route('/api/backup/download/<filename>')
@login_required
def api_backup_download(filename):
    import re
    from flask import send_file
    if not re.match(r'^kassenstuerzle_.*\.db$', filename):
        return t('srv.invalid_filename_download'), 400
    db_path = DB_PATH
    backup_dir = Path(os.path.dirname(db_path)) / 'backups'
    path = backup_dir / filename
    if not path.exists() or not path.resolve().is_relative_to(backup_dir.resolve()):
        return t('srv.file_not_found'), 404
    return send_file(str(path), as_attachment=True, download_name=filename, mimetype="application/x-sqlite3")

@main_bp.route('/api/backup/restore', methods=['POST'])
@login_required
def api_backup_restore():
    if 'file' not in request.files:
        return jsonify(success=False, error=t('srv.no_file')), 400
    f = request.files['file']
    if not f.filename.lower().endswith('.db'):
        return jsonify(success=False, error=t('srv.only_db_allowed')), 400
    db_path = DB_PATH
    import tempfile as _tempfile
    _tf = _tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    _tf.close()
    tmp = Path(_tf.name)
    f.save(str(tmp))
    try:
        # Erst verschlüsselt prüfen, dann unverschlüsselt
        con = _open_db(str(tmp))
        con.row_factory = sqlite3.Row
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        con.close()
    except Exception:
        try:
            con = sqlite3.connect(str(tmp))
            con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            con.close()
        except Exception as e:
            current_app.logger.exception("Backup-Restore: hochgeladene Datei ist keine gültige SQLite-DB")
            tmp.unlink(missing_ok=True)
            return jsonify(success=False, error=t('srv.invalid_db_upload')), 400
    create_backup("pre-restore")
    shutil.copy(str(tmp), db_path)
    # WAL-Sidecars der alten Datenbank entfernen: ein übrig gebliebenes -wal
    # würde Teile der gerade wiederhergestellten DB zurückrollen.
    for _sidecar in (db_path + '-wal', db_path + '-shm'):
        try:
            os.remove(_sidecar)
        except FileNotFoundError:
            pass
    tmp.unlink(missing_ok=True)
    return jsonify(success=True)

@main_bp.route('/api/backup/delete/<filename>', methods=['DELETE'])
@login_required
def api_backup_delete(filename):
    import re
    if not re.match(r'^kassenstuerzle_.*\.db$', filename):
        return "", 400
    db_path = DB_PATH
    backup_dir = Path(os.path.dirname(db_path)) / 'backups'
    path = backup_dir / filename
    if path.exists() and path.resolve().is_relative_to(backup_dir.resolve()):
        path.unlink()
    return jsonify(success=True)

def parse_receipt_text(text: str) -> dict:
    """Extrahiert Datum, Betrag und eine Beschreibung aus OCR-Rohtext eines Kassenbons."""
    import re
    from datetime import datetime as _dt

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # ── Datum: erstes plausibles TT.MM.JJ(JJ)-Muster im Text ────────────────
    day = month = year = None
    date_re = re.compile(r'(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{2,4})')
    for line in lines:
        m = date_re.search(line)
        if not m:
            continue
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        except ValueError:
            continue
        if y < 100:
            y += 2000
        if 1 <= d <= 31 and 1 <= mo <= 12 and 2015 <= y <= 2100:
            day, month, year = d, mo, y
            break
    if day is None:
        now = _dt.now()
        day, month, year = now.day, now.month, now.year

    # ── Betrag: Zeile mit Summen-Schlüsselwort bevorzugen, sonst größter
    #    im Text gefundener Betrag (Gesamtsumme ist auf Kassenbons meist
    #    der größte Einzelbetrag) ─────────────────────────────────────────
    # Nur echtes deutsches Zahlenformat (Komma als Dezimaltrennzeichen) zulassen -
    # sonst werden Datumsfragmente wie "03.09" (aus "03.09.2026") fälschlich als
    # Betrag "309,00" gelesen (Punkt würde sonst auch als Tausendertrenner gelten).
    amount_re = re.compile(r'(\d{1,3}(?:\.\d{3})*,\d{2})')
    TOTAL_KEYWORDS = ('summe', 'gesamt', 'zu zahlen', 'zuzahlen', 'total',
                       'endbetrag', 'betrag', 'bar', 'ec-cash', 'kartenzahlung')
    amount = None
    for line in lines:
        low = line.lower()
        if any(kw in low for kw in TOTAL_KEYWORDS):
            m = amount_re.search(line)
            if m:
                cand = parse_german_amount(m.group(1))
                if cand:
                    amount = abs(cand)
                    break
    if amount is None:
        all_amounts = [abs(a) for a in
                       (parse_german_amount(m) for m in amount_re.findall(text)) if a]
        if all_amounts:
            amount = max(all_amounts)

    # ── Beschreibung: erste sinnvolle Zeile (meist der Geschäftsname) ───────
    description = ''
    for line in lines[:6]:
        cleaned = re.sub(r'[^A-Za-zÄÖÜäöüß0-9&.\-\s]', '', line).strip()
        if len(cleaned) >= 3 and not cleaned.replace(' ', '').isdigit():
            description = cleaned[:60]
            break

    return {
        'day': day, 'month': month, 'year': year,
        'description': description,
        'amount': round(amount, 2) if amount else None,
    }


@main_bp.route('/api/import/receipt-parse', methods=['POST'])
@login_required
def api_receipt_parse():
    """Liest einen fotografierten Kassenbon per OCR aus und extrahiert
    Datum, Betrag und eine Beschreibung als Vorschlag für einen neuen Ausgaben-Eintrag."""
    if 'file' not in request.files:
        return jsonify(success=False, error=t('srv.no_file')), 400
    f = request.files['file']
    if not f.filename:
        return jsonify(success=False, error=t('srv.no_file')), 400

    data = f.read()
    if not data:
        return jsonify(success=False, error=t('srv.file_empty')), 400
    if len(data) > 20 * 1024 * 1024:  # 20 MB Limit
        return jsonify(success=False, error="Datei zu groß (max. 20 MB)"), 400

    ext = os.path.splitext(f.filename)[1].lower()
    text = ''
    try:
        if ext == '.pdf':
            if not data.startswith(b'%PDF-'):
                return jsonify(success=False, error=t('srv.invalid_pdf')), 400
            import pdfplumber, io as _io
            with pdfplumber.open(_io.BytesIO(data)) as pdf:
                text = '\n'.join((p.extract_text() or '') for p in pdf.pages)
        else:
            from PIL import Image, UnidentifiedImageError
            import pytesseract, io as _io
            try:
                img = Image.open(_io.BytesIO(data))
                img.load()
            except UnidentifiedImageError:
                return jsonify(success=False, error=t('srv.invalid_image')), 400
            img = img.convert('L')  # Graustufen verbessern die OCR-Erkennungsrate
            try:
                text = pytesseract.image_to_string(img, lang='deu+eng')
            except pytesseract.TesseractNotFoundError:
                current_app.logger.error("Tesseract-OCR ist nicht installiert")
                return jsonify(success=False,
                                error=t('srv.ocr_unavailable')), 503
    except Exception:
        current_app.logger.exception("Beleg-OCR fehlgeschlagen")
        return jsonify(success=False, error=t('srv.ocr_unreadable')), 400

    if not text.strip():
        return jsonify(success=False,
                        error=t('srv.ocr_no_text')), 200

    parsed = parse_receipt_text(text)
    return jsonify(success=True, **parsed)
# --- END BACKUP ENDPOINTS ---


# --- START PAKET 3 (Calendar & Notifications) ---
from flask import send_from_directory, current_app

@main_bp.route('/sw.js')
def service_worker():
    return send_from_directory(os.path.join(current_app.root_path, 'static'), 'sw.js', mimetype='application/javascript')

@main_bp.route('/offline')
def pwa_offline():
    """Offline-Fallback-Seite für den Service Worker (muss ohne Login erreichbar sein)."""
    return render_template('offline.html')

@main_bp.route('/api/notifications/vapidPublicKey')
@login_required
def get_vapid_key():
    """Gibt den VAPID Public Key zurück (für Web-Push Notifications).
    Der Schlüssel wird beim ersten Aufruf automatisch generiert und in
    data/.vapid_private_key.pem gespeichert (analog zu SECRET_KEY / DB-Verschlüsselung).
    """
    try:
        return jsonify({"publicKey": get_vapid_public_key_b64()})
    except Exception:
        current_app.logger.exception("VAPID-Key konnte nicht erzeugt/geladen werden")
        return jsonify({"publicKey": None, "error": t('srv.push_unavailable')}), 503

@main_bp.route('/api/notifications/register', methods=['POST'])
@login_required
def register_notification():
    sub_data = request.json or {}
    endpoint = sub_data.get('endpoint')
    keys = sub_data.get('keys') or {}
    p256dh, auth = keys.get('p256dh'), keys.get('auth')
    if not endpoint or not p256dh or not auth:
        return jsonify(success=False, error=t('srv.missing_data')), 400

    from datetime import datetime as _dt
    conn = get_db()
    conn.execute(
        """INSERT INTO push_subscriptions (endpoint, p256dh, auth, lang, created_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, lang=excluded.lang""",
        (endpoint, p256dh, auth, session.get('lang', 'de'), _dt.now().isoformat())
    )
    conn.commit()
    return jsonify(success=True)


@main_bp.route('/api/notifications/unregister', methods=['POST'])
@login_required
def unregister_notification():
    """Entfernt eine Push-Subscription (z.B. wenn der Nutzer Push im Browser deaktiviert)."""
    endpoint = (request.json or {}).get('endpoint')
    if not endpoint:
        return jsonify(success=False, error=t('srv.missing_data')), 400
    conn = get_db()
    conn.execute('DELETE FROM push_subscriptions WHERE endpoint=?', (endpoint,))
    conn.commit()
    return jsonify(success=True)


# ── Web-Push: VAPID-Schlüssel & Versand ───────────────────────────────────────
_VAPID_KEY_PATH = os.path.join(os.path.dirname(DB_PATH), '.vapid_private_key.pem')


def _get_vapid():
    """Lädt den VAPID-Schlüssel aus data/.vapid_private_key.pem und generiert ihn
    beim allerersten Aufruf automatisch (py_vapid.Vapid.from_file macht das selbst)."""
    from py_vapid import Vapid
    return Vapid.from_file(_VAPID_KEY_PATH)


def get_vapid_public_key_b64() -> str:
    """Public Key im 'applicationServerKey'-Format (roher EC-Punkt, base64url ohne Padding),
    wie es PushManager.subscribe() im Browser erwartet."""
    from cryptography.hazmat.primitives import serialization
    import base64 as _base64
    vapid = _get_vapid()
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _send_one_push(sub_row, title: str, body: str, url: str = '/') -> bool:
    """Sendet eine einzelne Push-Nachricht. Gibt False zurück wenn die Subscription
    ungültig/abgelaufen ist (Aufrufer sollte sie dann aus der DB löschen)."""
    from pywebpush import webpush, WebPushException
    import json as _json
    subscription_info = {
        "endpoint": sub_row['endpoint'],
        "keys": {"p256dh": sub_row['p256dh'], "auth": sub_row['auth']},
    }
    payload = _json.dumps({"title": title, "body": body, "url": url})
    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=_VAPID_KEY_PATH,
            vapid_claims={"sub": os.environ.get('VAPID_SUBJECT', 'mailto:admin@localhost')},
        )
        return True
    except WebPushException as e:
        status = getattr(e.response, 'status_code', None)
        if status in (404, 410):
            return False
        current_app.logger.warning("Push-Zustellung fehlgeschlagen: %s", e)
        return True  # transienter Fehler - Subscription trotzdem behalten
    except Exception:
        current_app.logger.exception("Push-Zustellung fehlgeschlagen")
        return True


def send_push_to_all(build_message, url: str = '/') -> None:
    """Sendet eine Push-Nachricht an alle registrierten Geräte.
    build_message(lang) -> (title, body) - wird pro Subscription in deren Sprache aufgerufen.
    Subscriptions, die der Browser als ungültig meldet (404/410), werden entfernt."""
    conn = get_db()
    rows = conn.execute('SELECT * FROM push_subscriptions').fetchall()
    stale_ids = []
    for row in rows:
        title, body = build_message(row['lang'] or 'de')
        if not _send_one_push(row, title, body, url):
            stale_ids.append(row['id'])
    for rid in stale_ids:
        conn.execute('DELETE FROM push_subscriptions WHERE id=?', (rid,))
    if stale_ids:
        conn.commit()


def check_and_send_reminders(app):
    """Täglich per APScheduler ausgeführt: erinnert an offene Abrechnungen aus
    vergangenen Monaten und an fehlende Einträge für den laufenden Monat."""
    from datetime import date
    import logging
    with app.app_context():
        conn = _open_db(DB_PATH)
        if _SQLCIPHER_AVAILABLE and _DB_KEY:
            conn.execute(_pragma_key_sql(_DB_KEY))
        conn.row_factory = sqlite3.Row
        try:
            if conn.execute('SELECT COUNT(*) as c FROM push_subscriptions').fetchone()['c'] == 0:
                return  # niemand abonniert - nichts zu tun

            today = date.today()
            cur_y, cur_m = today.year, today.month

            month_keys = {(r['year'], r['month']) for r in conn.execute(
                'SELECT DISTINCT year, month FROM expenses UNION SELECT DISTINCT year, month FROM income')}
            settled = {(r['year'], r['month']) for r in conn.execute(
                'SELECT year, month FROM month_status WHERE settled=1')}
            open_past = sorted(mk for mk in month_keys if mk < (cur_y, cur_m) and mk not in settled)

            if open_past:
                oy, om = open_past[0]
                count = len(open_past)

                def _build_settlement_msg(lang, om=om, oy=oy, count=count):
                    return (
                        _translate(lang, 'push.open_settlement_title'),
                        _translate(lang, 'push.open_settlement_body',
                                   count=count, month=month_name(om, lang), year=oy),
                    )
                send_push_to_all(_build_settlement_msg, url='/overview')

            if (cur_y, cur_m) not in month_keys and today.day >= 5:
                def _build_missing_msg(lang):
                    return (
                        _translate(lang, 'push.missing_entries_title'),
                        _translate(lang, 'push.missing_entries_body',
                                   month=month_name(cur_m, lang), year=cur_y),
                    )
                send_push_to_all(_build_missing_msg, url=f'/month/{cur_y}/{cur_m}')
        except Exception:
            logging.getLogger(__name__).exception("Erinnerungs-Job fehlgeschlagen")
        finally:
            conn.close()




# ════════════════════════════════════════════════════════════════════════════════
# BELEG-UPLOAD
# ════════════════════════════════════════════════════════════════════════════════

@main_bp.route('/api/receipt/upload', methods=['POST'])
@login_required
def api_receipt_upload():
    """Lädt einen Beleg hoch. Gibt den MD5-Hash-Dateinamen zurück."""
    import hashlib, mimetypes
    f = request.files.get('file')
    if not f:
        return jsonify(success=False, error=t('srv.no_file')), 400

    ext = os.path.splitext(f.filename or '')[1].lower()
    if ext not in ALLOWED_RECEIPT_EXTENSIONS:
        return jsonify(success=False,
                       error=t('srv.invalid_file_type', allowed=', '.join(ALLOWED_RECEIPT_EXTENSIONS))), 400

    data = f.read()
    if len(data) > 20 * 1024 * 1024:  # 20 MB Limit
        return jsonify(success=False, error=t('srv.file_too_large')), 400

    # Sicherheit: Dateiinhalt muss zur (durch Endung vorgegebenen) Art passen –
    # verhindert z.B. getarnte HTML/Skript-Dateien mit Bild-Endung.
    if ext == '.pdf':
        if not data.startswith(b'%PDF-'):
            return jsonify(success=False, error=t('srv.invalid_pdf')), 400
    else:
        try:
            from PIL import Image
            import io as _io
            Image.open(_io.BytesIO(data)).verify()
        except Exception:
            return jsonify(success=False, error=t('srv.invalid_image')), 400

    md5 = hashlib.md5(data).hexdigest()
    filename = md5 + ext
    dest = os.path.join(RECEIPTS_DIR, filename)
    with open(dest, 'wb') as out:
        out.write(data)

    return jsonify(success=True, filename=filename)


@main_bp.route('/api/receipt/<filename>')
@login_required
def api_receipt_get(filename):
    """Liefert einen gespeicherten Beleg aus."""
    import mimetypes
    # Sicherheit: Kein Path-Traversal
    safe = os.path.basename(filename)
    if safe != filename:
        return '', 404
    path = os.path.join(RECEIPTS_DIR, safe)
    if not os.path.isfile(path):
        return '', 404
    mime, _ = mimetypes.guess_type(path)
    mime = mime or 'application/octet-stream'
    from flask import send_file
    return send_file(path, mimetype=mime)


@main_bp.route('/api/receipt/<filename>', methods=['DELETE'])
@login_required
def api_receipt_delete(filename):
    """Löscht einen Beleg."""
    safe = os.path.basename(filename)
    if safe != filename:
        return jsonify(success=False, error=t('srv.invalid_filename')), 400
    path = os.path.join(RECEIPTS_DIR, safe)
    if os.path.isfile(path):
        os.unlink(path)
    return jsonify(success=True)

@main_bp.route('/search')
@login_required
def search():
    """Globale Volltextsuche über Ausgaben und Einnahmen."""
    q = request.args.get('q', '').strip()
    results = []
    total_count = 0

    if q and len(q) >= 2:
        db = get_db()
        like = f'%{q}%'

        # Ausgaben suchen
        expenses = db.execute("""
            SELECT
                e.id, e.year, e.month, e.day,
                e.description, e.amount, e.is_cash,
                p.name  AS person_name,
                c.name  AS category_name,
                c.color AS category_color,
                'expense' AS row_type
            FROM expenses e
            LEFT JOIN persons    p ON p.id = e.person_id
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE e.description LIKE ?
               OR p.name        LIKE ?
               OR c.name        LIKE ?
            ORDER BY e.year DESC, e.month DESC, e.day DESC
            LIMIT 500
        """, (like, like, like)).fetchall()

        # Einnahmen suchen
        income = db.execute("""
            SELECT
                i.id, i.year, i.month, i.day,
                i.description, i.amount, 0 AS is_cash,
                p.name  AS person_name,
                c.name  AS category_name,
                c.color AS category_color,
                'income' AS row_type
            FROM income i
            LEFT JOIN persons    p ON p.id = i.person_id
            LEFT JOIN categories c ON c.id = i.category_id
            WHERE i.description LIKE ?
               OR p.name        LIKE ?
               OR c.name        LIKE ?
            ORDER BY i.year DESC, i.month DESC, i.day DESC
            LIMIT 500
        """, (like, like, like)).fetchall()

        # Zusammenführen und nach Datum sortieren
        from markupsafe import Markup
        all_rows = [dict(r) for r in expenses] + [dict(r) for r in income]
        # Beschreibung mit Highlighting vorverarbeiten (sicher, kein XSS)
        for row in all_rows:
            row['description_hl'] = Markup(_highlight(row.get('description') or '', q))
        all_rows.sort(key=lambda r: (r['year'], r['month'], r['day']), reverse=True)
        total_count = len(all_rows)

        # Nach Jahr/Monat gruppieren
        from collections import OrderedDict
        grouped = OrderedDict()
        for row in all_rows:
            key = (row['year'], row['month'])
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(row)

        results = list(grouped.items())

    return render_template('search.html',
                           q=q,
                           results=results,
                           total_count=total_count,
                           month_names=MONTH_NAMES,
                           active='search')

@main_bp.route('/api/calendar/export')
@login_required
def export_calendar():
    """Exportiert offene (noch nicht abgerechnete) Ausgleichszahlungen als iCal-Datei."""
    from datetime import datetime

    conn = get_db()
    month_keys = conn.execute(
        'SELECT DISTINCT year, month FROM expenses '
        'UNION SELECT DISTINCT year, month FROM income'
    ).fetchall()

    cal_content = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Kassenstuerzle//NONSGML v1.0//EN"
    ]
    dtstamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")

    for row in month_keys:
        year, month = row['year'], row['month']
        settlement = calculate_settlement(year, month, conn)
        if not settlement or settlement.get('is_settled'):
            continue
        # Fälligkeit: Ende des jeweiligen Monats
        due_date = f"{year}{month:02d}28T120000Z"
        for i, t in enumerate(settlement.get('transactions', [])):
            cal_content.extend([
                "BEGIN:VEVENT",
                f"UID:settlement-{year}-{month:02d}-{i}@kassenstuerzle",
                f"DTSTAMP:{dtstamp}",
                f"DTSTART:{due_date}",
                f"SUMMARY:Ausgleich: {t['from']} -> {t['to']} ({t['amount']} EUR)",
                f"DESCRIPTION:Monatsabrechnung {month}/{year}",
                "END:VEVENT"
            ])

    cal_content.append("END:VCALENDAR")

    from flask import Response
    return Response(
        "\r\n".join(cal_content),
        mimetype='text/calendar',
        headers={"Content-disposition": "attachment; filename=kassenstuerzle.ics"}
    )
# --- END PAKET 3 ---
