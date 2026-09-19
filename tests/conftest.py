"""Pytest-Fixtures für Kassenstürzle.

WICHTIG: DATABASE_PATH muss gesetzt sein, BEVOR `app` importiert wird —
main.py wertet den Pfad beim Modul-Import aus (main.py:27).
"""
import os
import pathlib
import sys
import tempfile

import pytest

_TMPDIR = tempfile.mkdtemp(prefix='kassenstuerzle-tests-')
os.environ['DATABASE_PATH'] = os.path.join(_TMPDIR, 'test.db')
os.environ['ADMIN_PASSWORD'] = 'test-password-123'
os.environ['DISABLE_SCHEDULER'] = '1'
# Rate-Limits in der Suite abschalten: viele Tests melden sich nacheinander an
# und wuerden sonst am 10/min-Limit des Logins scheitern. test_rate_limit.py
# schaltet sie fuer seine eigene App-Instanz gezielt wieder ein.
os.environ['DISABLE_RATE_LIMIT'] = '1'
os.environ.pop('DB_ENCRYPTION_KEY', None)   # Tests laufen unverschlüsselt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import create_app                      # noqa: E402
from app.routes import main as main_module       # noqa: E402

_CLEANUP_TABLES = ('expenses', 'income', 'persons', 'categories',
                   'recurring_expenses', 'category_splits', 'month_status',
                   'activity', 'presence')


@pytest.fixture(scope='session')
def app():
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture(autouse=True)
def _admin_ohne_erzwungenen_passwortwechsel(app):
    """Nimmt dem Test-Admin den erzwungenen Erstwechsel des Passworts ab.

    Befund I8: in der Produktion bekommt der Bootstrap-Admin bewusst
    `must_change_password=1` - das geloggte Initialpasswort ist damit ein
    Einmalpasswort, und login_required leitet bis zum Wechsel auf
    /password/aendern um. Die uebrige Testsuite meldet sich als 'admin' an,
    um ganz andere Dinge zu pruefen; ohne diesen Reset landete jede dieser
    Anfragen auf der Wechselseite.

    Bewusst NACH dem Bootstrap und pro Test, nicht einmalig: mehrere Tests
    (Restore, Migration) leeren die users-Tabelle und lassen init_db() erneut
    bootstrappen, wodurch das Flag mitten in der Session wieder gesetzt wird.

    Die Produktionslogik bleibt unangetastet - test_bootstrap_admin.py prueft
    sie an einer eigenen, frisch gebootstrappten Wegwerf-Datenbank.
    """
    conn = main_module._open_db(main_module.DB_PATH)
    try:
        conn.execute("UPDATE users SET must_change_password=0 WHERE username='admin'")
        conn.commit()
    except main_module.sqlite3.OperationalError:
        # Vor der Profil-Migration gibt es die Spalte nicht - dann gibt es
        # auch keinen erzwungenen Wechsel, der stoeren koennte.
        pass
    finally:
        conn.close()
    yield


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(app):
    c = app.test_client()
    res = c.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
    assert res.status_code == 302, 'Login fehlgeschlagen'
    return c


@pytest.fixture()
def csrf(auth_client):
    return auth_client.get('/api/csrf-token').get_json()['csrf_token']


# Kernschema einer echten Kassenstuerzle-Datenbank ohne die users-Tabelle
# (die je nach Test mit oder ohne die Phase-B-Spalten gebaut wird). Bewusst
# wortgleich zu init_db(): die Restore-Pruefung verlangt genau diese Tabellen
# und Spalten, ein kuenstlich reduziertes Test-Backup waere kein realistischer
# Stellvertreter fuer eine echte Sicherung.
_KERN_SCHEMA = (
    """CREATE TABLE persons (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        wallet_start_date TEXT DEFAULT NULL, wallet_start_amount REAL DEFAULT 0)""",
    """CREATE TABLE categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        color TEXT NOT NULL DEFAULT '#6c757d', is_split INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, year INTEGER NOT NULL,
        month INTEGER NOT NULL, day INTEGER NOT NULL, description TEXT DEFAULT '',
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        amount REAL NOT NULL DEFAULT 0, is_cash INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE income (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER NOT NULL, month INTEGER NOT NULL,
        day INTEGER NOT NULL DEFAULT 1, description TEXT DEFAULT '',
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        amount REAL NOT NULL DEFAULT 0)""",
    """CREATE TABLE month_status (
        year INTEGER NOT NULL, month INTEGER NOT NULL,
        settled INTEGER NOT NULL DEFAULT 0, settled_at TEXT,
        PRIMARY KEY (year, month))""",
    """CREATE TABLE recurring_expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day INTEGER NOT NULL DEFAULT 1, description TEXT NOT NULL DEFAULT '',
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        amount REAL NOT NULL DEFAULT 0, frequency TEXT NOT NULL DEFAULT 'monthly',
        start_month INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1)""",
    """CREATE TABLE category_splits (
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
        share REAL NOT NULL DEFAULT 1,
        PRIMARY KEY (category_id, person_id))""",
)

_USERS_PHASE_B = """CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        role TEXT NOT NULL DEFAULT 'member',
        is_active INTEGER NOT NULL DEFAULT 1,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        created_at TEXT, last_login TEXT,
        session_epoch INTEGER NOT NULL DEFAULT 0)"""

_USERS_VOR_PHASE_B = """CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL)"""


@pytest.fixture()
def sicherung_bauen(tmp_path):
    """Baut eine realistische Kassenstuerzle-Sicherung als Datei.

    `benutzer` ist eine Liste (name, passwort[, rolle]); voreingestellt ist der
    Admin, mit dem die Tests angemeldet sind - eine echte Sicherung DIESER
    Instanz enthaelt ihn. `phase_b=False` erzeugt eine Sicherung von vor der
    Profil-Migration (users ohne role/is_active/...), `zusatz_sql` haengt
    weitere Anweisungen an (z.B. einen Trigger fuer die Missbrauchsfaelle).

    `instanz_id` steuert den Herkunftsnachweis (Blocker 1):
      * 'eigen'  - die Kennung der laufenden Testinstanz, also eine echte
                   Sicherung DIESER Installation (Voreinstellung),
      * None     - gar keine app_meta-Tabelle, wie jede Sicherung, die vor
                   dieser Aenderung erstellt wurde,
      * ein Text - eine fremde Kennung, also eine andere Installation.
    """
    import sqlite3 as _sqlite3
    from werkzeug.security import generate_password_hash

    zaehler = {'n': 0}

    def _eigene_instanz_id():
        con = main_module._open_db(main_module.DB_PATH)
        try:
            return main_module._lies_instanz_id(con)
        finally:
            con.close()

    def _bauen(name='sicherung.db', benutzer=(('admin', 'test-password-123', 'admin'),),
               phase_b=True, zusatz_sql=(), tabellen_weglassen=(),
               instanz_id='eigen'):
        zaehler['n'] += 1
        pfad = tmp_path / f"{zaehler['n']}_{name}"
        con = _sqlite3.connect(str(pfad))
        if instanz_id is not None:
            # Eine echte Sicherung ist eine Dateikopie der Datenbank und
            # traegt app_meta deshalb immer mit.
            wert = _eigene_instanz_id() if instanz_id == 'eigen' else instanz_id
            con.execute('CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT)')
            con.execute("INSERT INTO app_meta (key, value) VALUES ('instance_id', ?)",
                        (wert,))
        con.execute(_USERS_PHASE_B if phase_b else _USERS_VOR_PHASE_B)
        for sql in _KERN_SCHEMA:
            tabelle = sql.split('CREATE TABLE ', 1)[1].split(' ', 1)[0].strip()
            if tabelle in tabellen_weglassen:
                continue
            con.execute(sql)
        for eintrag in benutzer:
            benutzername, passwort = eintrag[0], eintrag[1]
            rolle = eintrag[2] if len(eintrag) > 2 else 'member'
            if phase_b:
                con.execute('INSERT INTO users (username, password_hash, role)'
                            ' VALUES (?,?,?)',
                            (benutzername, generate_password_hash(passwort), rolle))
            else:
                con.execute('INSERT INTO users (username, password_hash) VALUES (?,?)',
                            (benutzername, generate_password_hash(passwort)))
        for sql in zusatz_sql:
            con.execute(sql)
        con.commit()
        con.close()
        return pfad

    return _bauen


@pytest.fixture()
def db(app):
    """Direkte DB-Verbindung; raeumt nach jedem Test alle Nutzdaten weg."""
    conn = main_module._open_db(main_module.DB_PATH)
    conn.row_factory = main_module.sqlite3.Row
    yield conn
    for table in _CLEANUP_TABLES:
        try:
            conn.execute(f'DELETE FROM {table}')
        except main_module.sqlite3.OperationalError as exc:
            # 'activity' und 'presence' entstehen erst in Task 3 - bis dahin
            # duerfen sie fehlen. Jeder ANDERE Fehler muss sichtbar bleiben,
            # sonst bricht die Testisolation still zusammen.
            if 'no such table' not in str(exc).lower():
                raise
    conn.commit()
    conn.close()
