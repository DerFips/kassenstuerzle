# Phase A — Nebenläufigkeit: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die drei reproduzierbaren Datenverlust-Szenarien aus der Spec beseitigen, indem alle Speicher-Endpunkte von „Replace-All" auf nicht-destruktive Einzelzeilen-Operationen umgestellt werden, plus Live-Hinweis auf parallele Bearbeitung.

**Architecture:** Alle fünf Speicher-Endpunkte erhalten ein einheitliches Payload-Format (`{v:2, rows, deleted_ids}`), antworten mit `id_map` für neu erzeugte Zeilen und löschen ausschließlich, was in `deleted_ids` steht. Eine `activity`-Tabelle zählt Änderungen pro Bereich, eine `presence`-Tabelle hält Heartbeats; das Frontend pollt beides alle 10 s. SQLite wird auf WAL umgestellt, damit die 4 Gunicorn-Threads sich nicht blockieren.

**Tech Stack:** Python 3.11, Flask 3.0.0, SQLite/SQLCipher (`sqlcipher3` mit Fallback auf `sqlite3`), Jinja2, Bootstrap 5.3.3, pytest (neu, nur Entwicklung)

## Global Constraints

- Referenz-Spec: `docs/superpowers/specs/2026-09-18-benutzerprofile-und-nebenlaeufigkeit-design.md`. Diese Phase setzt ausschließlich Abschnitt 3 (A1–A4) um.
- Payload-Version ist exakt `"v": 2`. Jeder Speicher-Endpunkt weist Payloads ohne diese Marke mit **HTTP 409** ab (Spec M8, „offener Browser-Tab beim Deploy").
- Gelöscht wird **nur** anhand von `deleted_ids`. Kein Endpunkt darf mehr Datensätze löschen, weil sie im Payload fehlen.
- `UPDATE`/`DELETE` auf `expenses` und `income` immer mit `AND year=? AND month=?` einschränken (verhindert Manipulation über Monatsgrenzen).
- Betrag 0 bei Zeile **mit** `id` → löschen; bei Zeile **ohne** `id` → ignorieren (erhält heutiges Verhalten).
- `id_map` (`client_key` → neue DB-`id`) ist zwingend im Response, sonst erzeugt der 800-ms-Autosave Duplikate.
- Kein optimistisches Sperren (Entscheidung E3). Die 409-Antwort ist eine Client-Versionsprüfung, **kein** Datenkonflikt-Check.
- Kein Auto-Reload, solange ein Eingabefeld fokussiert ist oder ungespeicherte Änderungen offen sind.
- Übersetzungen immer paarweise in `TRANSLATIONS['de']` **und** `TRANSLATIONS['en']` ergänzen.
- Tests laufen unverschlüsselt: `DB_ENCRYPTION_KEY` wird nicht gesetzt, `main.py` fällt dann auf `sqlite3` zurück (`main.py:13-15`). `sqlcipher3` ist für Tests nicht erforderlich.
- `DB_PATH` wird beim Modul-Import ausgewertet (`main.py:27`). `DATABASE_PATH` muss deshalb **vor** dem Import von `app` gesetzt werden.
- `requirements.txt` bleibt unverändert (Produktions-Image). Testabhängigkeiten gehören in `requirements-dev.txt`.
- **Das Projekt ist kein Git-Repository.** Jede Aufgabe endet daher mit einem Verifikationsschritt statt einem Commit. Ein `git init` vor Beginn wäre empfehlenswert, ist aber nicht Teil dieses Plans.

---

## File Structure

| Datei | Verantwortung | Änderung |
|---|---|---|
| `requirements-dev.txt` | Testabhängigkeiten, getrennt vom Produktions-Image | Neu |
| `tests/conftest.py` | Pytest-Fixtures: Env vor Import, App, eingeloggter Client, CSRF-Token, DB-Zugriff mit Aufräumen | Neu |
| `tests/test_smoke.py` | Grundvertrauen: App startet, Login funktioniert | Neu |
| `tests/test_db_hardening.py` | WAL-Modus, Restore räumt Sidecar-Dateien | Neu |
| `tests/test_activity.py` | `_bump_activity`, `POST /api/activity`, Anwesenheit | Neu |
| `tests/test_save_expenses.py` | Einzelzeilen-Upsert, `deleted_ids`, `id_map`, 409, Betrag 0 | Neu |
| `tests/test_save_income.py` | dito für Einnahmen | Neu |
| `tests/test_save_settings.py` | Personen, Kategorien, Daueraufträge nicht-destruktiv | Neu |
| `app/routes/main.py` | `_open_db`, `init_db`, `api_backup_restore`, fünf Speicher-Endpunkte, `_require_v2`, `_bump_activity`, `/api/activity`, `month_view` | Ändern |
| `app/translations.py` | Neue Schlüssel für 409-Meldung und Live-Hinweis | Ändern |
| `app/templates/base.html` | Gemeinsamer Helfer `initActivityWatch(scope, version, onChange)` | Ändern |
| `app/templates/month.html` | Zeilen-`id`s, `deleted_ids`, `id_map`, v2-Payload, Live-Hinweis | Ändern |
| `app/templates/settings.html` | v2-Payload, `deleted_ids`, Live-Hinweis | Ändern |

---

### Task 1: Test-Harness aufsetzen

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nichts
- Produces: Fixtures `app`, `client`, `auth_client`, `csrf` (str), `db` (offene Verbindung mit `row_factory`), die alle folgenden Tasks nutzen. `db` leert nach jedem Test die Tabellen `expenses, income, persons, categories, recurring_expenses, category_splits, month_status, activity, presence`.

- [ ] **Step 1: `requirements-dev.txt` anlegen**

```
-r requirements.txt
pytest==8.3.3
```

- [ ] **Step 2: `tests/conftest.py` anlegen**

```python
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
```

- [ ] **Step 3: `tests/test_smoke.py` anlegen**

```python
def test_login_page_reachable(client):
    res = client.get('/login')
    assert res.status_code == 200


def test_login_and_overview(auth_client):
    res = auth_client.get('/overview')
    assert res.status_code == 200


def test_csrf_token_endpoint(auth_client):
    token = auth_client.get('/api/csrf-token').get_json()['csrf_token']
    assert isinstance(token, str) and len(token) == 64
```

- [ ] **Step 4: Testumgebung einrichten und Tests ausführen**

```bash
python3.11 -m venv .venv-dev
.venv-dev/bin/pip install -q -r requirements-dev.txt
.venv-dev/bin/pytest tests/ -v
```

Erwartung: 3 Tests PASS. Schlägt die Installation an `sqlcipher3` fehl, stattdessen
`.venv-dev/bin/pip install -q pytest Flask==3.0.0 python-dotenv==1.0.1 APScheduler==3.10.4 Flask-Limiter==3.9.0`
verwenden — für Phase A werden OCR-/PDF-Pakete nicht gebraucht.

- [ ] **Step 5: Verifizieren**

`.venv-dev/bin/pytest tests/ -q` läuft grün. `git status` entfällt (kein Repository) — stattdessen prüfen, dass `requirements.txt` **unverändert** ist: `grep -c pytest requirements.txt` muss `0` ergeben.

---

### Task 2: SQLite-Härtung (WAL) und Restore-Bereinigung

**Files:**
- Modify: `app/routes/main.py:44-52` (`_open_db`)
- Modify: `app/routes/main.py` (`api_backup_restore`, Funktion `def api_backup_restore`)
- Test: `tests/test_db_hardening.py`

**Interfaces:**
- Consumes: Fixtures aus Task 1
- Produces: `_open_db` liefert Verbindungen im WAL-Modus mit `busy_timeout=5000`. Signatur unverändert: `_open_db(path: str) -> sqlite3.Connection`.

- [ ] **Step 1: Failing Test schreiben**

`tests/test_db_hardening.py`:

```python
import os

from app.routes import main as m


def test_connection_uses_wal_and_busy_timeout(db):
    mode = db.execute('PRAGMA journal_mode').fetchone()[0]
    assert mode.lower() == 'wal'
    timeout = db.execute('PRAGMA busy_timeout').fetchone()[0]
    assert timeout == 5000


def test_restore_removes_stale_wal_sidecars(app, auth_client, csrf, tmp_path, monkeypatch):
    """Nach dem Restore duerfen keine -wal/-shm der alten DB uebrig bleiben.

    Die blosse Existenzpruefung waere KEIN Regressionswaechter: SQLite verwirft
    ein Sidecar mit ungueltigem Header bereits beim Oeffnen der Datenbank, der
    Test waere also auch ohne den Fix gruen. Deshalb wird zusaetzlich geprueft,
    dass der Restore-Code beide Pfade aktiv zu entfernen versucht.
    """
    wal = m.DB_PATH + '-wal'
    shm = m.DB_PATH + '-shm'
    for path in (wal, shm):
        with open(path, 'wb') as fh:
            fh.write(b'stale')

    removed = []
    real_remove = os.remove

    def tracking_remove(path, *args, **kwargs):
        removed.append(str(path))
        return real_remove(path, *args, **kwargs)

    monkeypatch.setattr(m.os, 'remove', tracking_remove)

    # Gueltige SQLite-Datei als Upload erzeugen. Das Schema MUSS dem echten
    # persons-Schema entsprechen (inkl. UNIQUE): der Restore ersetzt die
    # komplette DB-Datei, und das init_db() im finally-Block repariert nur
    # additiv (CREATE TABLE IF NOT EXISTS / ALTER TABLE ADD COLUMN) - eine
    # fehlende UNIQUE-Bedingung kann es nicht nachtragen. Ein zu minimales
    # Test-Backup wuerde persons.name fuer den Rest der Testsession dauerhaft
    # ohne UNIQUE zuruecklassen und spaetere Konflikt-Tests entwerten.
    src = tmp_path / 'backup.db'
    con = m.sqlite3.connect(str(src))
    con.execute('CREATE TABLE persons (id INTEGER PRIMARY KEY AUTOINCREMENT,'
                ' name TEXT NOT NULL UNIQUE,'
                ' wallet_start_date TEXT DEFAULT NULL, wallet_start_amount REAL DEFAULT 0)')
    con.commit()
    con.close()

    try:
        with open(src, 'rb') as fh:
            res = auth_client.post('/api/backup/restore',
                                   data={'file': (fh, 'backup.db')},
                                   headers={'X-CSRF-Token': csrf},
                                   content_type='multipart/form-data')
        assert res.status_code == 200
        assert res.get_json()['success'] is True
        assert wal in removed, 'Restore hat das -wal nicht aktiv entfernt'
        assert shm in removed, 'Restore hat das -shm nicht aktiv entfernt'
        assert not os.path.exists(wal)
        assert not os.path.exists(shm)
    finally:
        # Der Restore hat die Test-Datenbank durch die minimale Upload-Datei
        # ersetzt. Ohne Wiederherstellung des Schemas scheitern alle folgenden
        # Tests an fehlenden Tabellen.
        with app.app_context():
            m.init_db()
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_db_hardening.py -v`
Erwartung: `test_connection_uses_wal_and_busy_timeout` FAIL (`assert 'delete' == 'wal'`), `test_restore_removes_stale_wal_sidecars` FAIL (`-wal wurde nicht entfernt`).

- [ ] **Step 3: `_open_db` härten**

`app/routes/main.py`, Funktion `_open_db` — nach dem `PRAGMA key` und vor `PRAGMA foreign_keys`:

```python
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
```

- [ ] **Step 4: Restore-Bereinigung ergänzen**

In `api_backup_restore`, direkt **nach** `shutil.copy(str(tmp), db_path)` und **vor** `tmp.unlink(...)`:

```python
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
```

- [ ] **Step 5: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -v`
Erwartung: alle Tests PASS (5 insgesamt).

- [ ] **Step 6: Verifizieren, dass die Produktions-DB weiter lesbar ist**

```bash
.venv-dev/bin/python -c "
from app.routes import main as m
c = m._open_db(m.DB_PATH)
print('journal_mode:', c.execute('PRAGMA journal_mode').fetchone()[0])
print('tabellen:', len(c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()))
c.close()"
```

Erwartung: `journal_mode: wal` und eine Tabellenzahl > 5.

---

### Task 3: Infrastruktur — Payload-Wächter, Activity und Presence

**Files:**
- Modify: `app/routes/main.py` (`init_db` Tabellenblock; neue Helfer `_require_v2`, `_bump_activity`; neue Route `POST /api/activity`; `month_view` Kontext)
- Modify: `app/translations.py` (Schlüssel `srv.stale_client`)
- Test: `tests/test_activity.py`

**Interfaces:**
- Consumes: Fixtures aus Task 1
- Produces:
  - `_require_v2(d) -> Optional[Response]` — gibt `None` zurück wenn `d` ein Dict mit `d['v'] == 2` ist, sonst eine fertige 409-Antwort.
  - `_bump_activity(conn, scope: str) -> int` — erhöht den Zähler und gibt die neue Version zurück. **Committet nicht**; der Aufrufer muss `conn.commit()` aufrufen.
  - `POST /api/activity` mit JSON `{"scope": "month:2026-09"}` → `{"success": true, "version": int, "updated_at": str|None, "updated_by": str|None, "others": [str]}`
  - `month_view` übergibt zusätzlich `activity_scope` (str) und `activity_version` (int) an das Template.

- [ ] **Step 1: Failing Test schreiben**

`tests/test_activity.py`:

```python
def test_activity_starts_at_zero(auth_client, csrf, db):
    res = auth_client.post('/api/activity', json={'scope': 'month:2026-09'},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    body = res.get_json()
    assert body['success'] is True
    assert body['version'] == 0
    assert body['others'] == []


def test_activity_requires_scope(auth_client, csrf):
    res = auth_client.post('/api/activity', json={}, headers={'X-CSRF-Token': csrf})
    assert res.status_code == 400


def test_bump_activity_increments_and_records_user(app, db):
    from app.routes import main as m
    with app.test_request_context():
        from flask import session
        session['username'] = 'alice'
        first = m._bump_activity(db, 'settings')
        second = m._bump_activity(db, 'settings')
        db.commit()
    assert (first, second) == (1, 2)
    row = db.execute("SELECT version, updated_by FROM activity WHERE scope='settings'").fetchone()
    assert row['version'] == 2
    assert row['updated_by'] == 'alice'


def test_presence_heartbeat_written(auth_client, csrf, db):
    auth_client.post('/api/activity', json={'scope': 'month:2026-09'},
                     headers={'X-CSRF-Token': csrf})
    rows = db.execute("SELECT * FROM presence WHERE scope='month:2026-09'").fetchall()
    assert len(rows) == 1


def test_require_v2_rejects_legacy_payload(app):
    from app.routes import main as m
    with app.test_request_context():
        assert m._require_v2({'v': 2, 'rows': []}) is None
        rejected = m._require_v2({'year': 2026, 'expenses': []})
        assert rejected is not None
        assert rejected[1] == 409
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_activity.py -v`
Erwartung: alle 5 Tests FAIL (404 für die Route, `AttributeError` für die Helfer).

- [ ] **Step 3: Tabellen in `init_db` ergänzen**

In `app/routes/main.py`, `init_db()`, direkt nach dem `CREATE TABLE ... push_subscriptions`-Block (vor dem `try: ALTER TABLE income ...`):

```python
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
```

- [ ] **Step 4: Helfer und Route ergänzen**

In `app/routes/main.py` die Helfer `_require_v2`, `_bump_activity` und `_month_scope` direkt nach der Funktion `_get_limiter()` einfügen. **Die Route `/api/activity` gehört dagegen hinter die Definition von `login_required`** (`main.py:184`) — der Decorator existiert vorher noch nicht und der Import würde mit `NameError` abbrechen:

```python
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
    uid = session.get('user_id') or -1
    conn.execute(
        'INSERT INTO presence (user_id, scope, last_seen) VALUES (?,?,?)'
        ' ON CONFLICT(user_id, scope) DO UPDATE SET last_seen = ?',
        (uid, scope, now_s, now_s))
    conn.commit()

    row = conn.execute('SELECT version, updated_at, updated_by FROM activity WHERE scope=?',
                       (scope,)).fetchone()
    cutoff = (now - timedelta(seconds=30)).isoformat(timespec='seconds')
    others = [r['username'] for r in conn.execute(
        'SELECT u.username FROM presence p JOIN users u ON u.id = p.user_id'
        ' WHERE p.scope=? AND p.last_seen >= ? AND p.user_id <> ?', (scope, cutoff, uid))]
    conn.close()
    return jsonify(success=True,
                   version=(row['version'] if row else 0),
                   updated_at=(row['updated_at'] if row else None),
                   updated_by=(row['updated_by'] if row else None),
                   others=others)
```

- [ ] **Step 5: Übersetzungen ergänzen**

In `app/translations.py` bei den `srv.`-Schlüsseln, deutscher Block:

```python
        'srv.stale_client': 'Diese Seite ist nicht mehr aktuell. Bitte neu laden.',
```

Englischer Block:

```python
        'srv.stale_client': 'This page is out of date. Please reload.',
```

- [ ] **Step 6: `month_view` gibt Scope und Version mit**

In `month_view`, direkt vor dem `return render_template(...)` (nach der `year_range`-Zeile) einfügen:

```python
    _scope = _month_scope(year, month)
    g.pop('db', None)   # conn.close() oben leert den g.db-Cache nicht
    _act_row = get_db().execute('SELECT version FROM activity WHERE scope=?', (_scope,)).fetchone()
    activity_version = _act_row['version'] if _act_row else 0
```

Und im `render_template`-Aufruf ergänzen:

```python
        activity_scope=_scope, activity_version=activity_version,
```

Hinweis: Weiter oben in `month_view` steht bereits ein `conn.close()`. `get_db()` cached die Verbindung aber in `g.db` — ohne das `g.pop` würde die bereits **geschlossene** Verbindung zurückgegeben. `close_db` räumt die neue Verbindung am Requestende auf.

- [ ] **Step 7: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -v`
Erwartung: alle Tests PASS (10 insgesamt).

- [ ] **Step 8: Verifizieren, dass die Monatsseite weiter rendert**

```bash
.venv-dev/bin/pytest tests/test_smoke.py -q
```

Zusätzlich manuell: `auth_client.get('/month/2026/9')` muss 200 liefern — als Test ergänzen:

```python
def test_month_view_renders(auth_client):
    res = auth_client.get('/month/2026/9')
    assert res.status_code == 200
```

(in `tests/test_smoke.py` anhängen, dann `.venv-dev/bin/pytest tests/ -q`)

---

### Task 4: `save_expenses` auf Einzelzeilen umstellen

**Files:**
- Modify: `app/routes/main.py:767-812` (`save_expenses`)
- Test: `tests/test_save_expenses.py`

**Interfaces:**
- Consumes: `_require_v2`, `_bump_activity`, `_month_scope` aus Task 3
- Produces: `POST /api/expenses/save` akzeptiert `{v:2, year, month, rows:[{id?|client_key?, day, description, person_id, category_id, amount, is_cash, paid_for_person_id, receipt_filename}], deleted_ids:[int]}` und antwortet zusätzlich mit `id_map: {client_key: id}` und `activity_version: int`. Die übrigen Antwortfelder (`ov_p`, `ov_c`, `settlement`, `wallet`, `savings_pp`, `ov_settlements`) bleiben unverändert.

- [ ] **Step 1: Failing Test schreiben**

`tests/test_save_expenses.py`:

```python
import pytest


@pytest.fixture()
def person(db):
    cur = db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    db.commit()
    return cur.lastrowid


def _save(client, csrf, **payload):
    body = {'v': 2, 'year': 2026, 'month': 9, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/expenses/save', json=body, headers={'X-CSRF-Token': csrf})


def test_legacy_payload_rejected(auth_client, csrf, db):
    res = auth_client.post('/api/expenses/save',
                           json={'year': 2026, 'month': 9, 'expenses': []},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409


def test_insert_returns_id_map(auth_client, csrf, db, person):
    res = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 3, 'description': 'Einkauf',
         'person_id': person, 'category_id': None, 'amount': 41.2, 'is_cash': True}])
    assert res.status_code == 200
    body = res.get_json()
    assert body['success'] is True
    new_id = body['id_map']['c1']
    row = db.execute('SELECT * FROM expenses WHERE id=?', (new_id,)).fetchone()
    assert row['description'] == 'Einkauf'
    assert row['amount'] == pytest.approx(41.2)


def test_update_keeps_id_and_does_not_duplicate(auth_client, csrf, db, person):
    created = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'Miete',
         'person_id': person, 'amount': 750.0}]).get_json()['id_map']['c1']

    _save(auth_client, csrf, rows=[
        {'id': created, 'day': 2, 'description': 'Miete korrigiert',
         'person_id': person, 'amount': 760.0}])

    rows = db.execute('SELECT * FROM expenses WHERE year=2026 AND month=9').fetchall()
    assert len(rows) == 1, 'Upsert hat ein Duplikat erzeugt'
    assert rows[0]['id'] == created
    assert rows[0]['description'] == 'Miete korrigiert'
    assert rows[0]['day'] == 2


def test_missing_row_is_not_deleted(auth_client, csrf, db, person):
    """Kernszenario: ein veralteter Payload darf fremde Zeilen nicht loeschen."""
    keep = _save(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'von User 1',
         'person_id': person, 'amount': 10.0}]).get_json()['id_map']['a']

    # User 2 speichert seinen alten Stand, in dem diese Zeile fehlt
    _save(auth_client, csrf, rows=[
        {'client_key': 'b', 'day': 5, 'description': 'von User 2',
         'person_id': person, 'amount': 20.0}])

    ids = {r['id'] for r in db.execute('SELECT id FROM expenses').fetchall()}
    assert keep in ids, 'Zeile von User 1 wurde geloescht'
    assert len(ids) == 2


def test_explicit_delete_removes_row(auth_client, csrf, db, person):
    rid = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'weg',
         'person_id': person, 'amount': 5.0}]).get_json()['id_map']['c1']

    _save(auth_client, csrf, deleted_ids=[rid])
    assert db.execute('SELECT COUNT(*) c FROM expenses').fetchone()['c'] == 0


def test_zero_amount_deletes_existing_row(auth_client, csrf, db, person):
    rid = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'x',
         'person_id': person, 'amount': 5.0}]).get_json()['id_map']['c1']

    _save(auth_client, csrf, rows=[
        {'id': rid, 'day': 1, 'description': 'x', 'person_id': person, 'amount': 0}])
    assert db.execute('SELECT COUNT(*) c FROM expenses').fetchone()['c'] == 0


def test_zero_amount_new_row_ignored(auth_client, csrf, db, person):
    res = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'leer',
         'person_id': person, 'amount': 0}])
    assert res.get_json()['id_map'] == {}
    assert db.execute('SELECT COUNT(*) c FROM expenses').fetchone()['c'] == 0


def test_update_cannot_cross_month_boundary(auth_client, csrf, db, person):
    rid = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'September',
         'person_id': person, 'amount': 5.0}]).get_json()['id_map']['c1']

    # Versuch, dieselbe id ueber den Oktober-Endpunkt zu aendern
    body = {'v': 2, 'year': 2026, 'month': 10, 'rows': [
        {'id': rid, 'day': 9, 'description': 'gekapert', 'person_id': person, 'amount': 99.0}],
        'deleted_ids': []}
    auth_client.post('/api/expenses/save', json=body, headers={'X-CSRF-Token': csrf})

    row = db.execute('SELECT * FROM expenses WHERE id=?', (rid,)).fetchone()
    assert row['description'] == 'September', 'Monatsgrenze wurde nicht durchgesetzt'


def test_activity_version_increases(auth_client, csrf, db, person):
    first = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'a',
         'person_id': person, 'amount': 1.0}]).get_json()['activity_version']
    second = _save(auth_client, csrf, rows=[]).get_json()['activity_version']
    assert second == first + 1
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_save_expenses.py -v`
Erwartung: `test_legacy_payload_rejected` FAIL (200 statt 409), alle übrigen FAIL mit `KeyError: 'id_map'` bzw. `KeyError: 'rows'`.

- [ ] **Step 3: `save_expenses` neu implementieren**

`app/routes/main.py` — die Funktion vollständig ersetzen:

```python
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
```

- [ ] **Step 4: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/test_save_expenses.py -v`
Erwartung: alle 10 Tests PASS.

- [ ] **Step 5: Gesamtsuite prüfen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS. Die Übersetzung `srv.save_lock_error` bleibt vorerst in `translations.py` stehen (Aufräumen in Task 7).

---

### Task 5: `save_income` auf Einzelzeilen umstellen

**Files:**
- Modify: `app/routes/main.py:1014-1056` (`save_income`)
- Test: `tests/test_save_income.py`

**Interfaces:**
- Consumes: `_require_v2`, `_bump_activity`, `_month_scope`
- Produces: `POST /api/income/save` akzeptiert `{v:2, year, month, rows:[{id?|client_key?, day, description, person_id, category_id, amount}], deleted_ids:[int]}` und antwortet mit `id_map`, `activity_version` sowie unverändert `income`, `income_total`, `expense_total`, `savings`, `savings_pp`.

- [ ] **Step 1: Failing Test schreiben**

`tests/test_save_income.py`:

```python
import pytest


@pytest.fixture()
def person(db):
    cur = db.execute("INSERT INTO persons (name) VALUES ('Bob')")
    db.commit()
    return cur.lastrowid


def _save(client, csrf, **payload):
    body = {'v': 2, 'year': 2026, 'month': 9, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/income/save', json=body, headers={'X-CSRF-Token': csrf})


def test_legacy_payload_rejected(auth_client, csrf):
    res = auth_client.post('/api/income/save',
                           json={'year': 2026, 'month': 9, 'income': []},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409


def test_insert_and_update(auth_client, csrf, db, person):
    rid = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'Gehalt',
         'person_id': person, 'amount': 2500.0}]).get_json()['id_map']['c1']

    _save(auth_client, csrf, rows=[
        {'id': rid, 'day': 1, 'description': 'Gehalt Maerz',
         'person_id': person, 'amount': 2600.0}])

    rows = db.execute('SELECT * FROM income WHERE year=2026 AND month=9').fetchall()
    assert len(rows) == 1
    assert rows[0]['description'] == 'Gehalt Maerz'
    assert rows[0]['amount'] == pytest.approx(2600.0)


def test_missing_row_is_not_deleted(auth_client, csrf, db, person):
    keep = _save(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'von User 1',
         'person_id': person, 'amount': 100.0}]).get_json()['id_map']['a']

    _save(auth_client, csrf, rows=[
        {'client_key': 'b', 'day': 2, 'description': 'von User 2',
         'person_id': person, 'amount': 200.0}])

    ids = {r['id'] for r in db.execute('SELECT id FROM income').fetchall()}
    assert keep in ids
    assert len(ids) == 2


def test_explicit_delete_and_zero_amount(auth_client, csrf, db, person):
    a = _save(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'a',
         'person_id': person, 'amount': 10.0}]).get_json()['id_map']['a']
    b = _save(auth_client, csrf, rows=[
        {'client_key': 'b', 'day': 2, 'description': 'b',
         'person_id': person, 'amount': 20.0}]).get_json()['id_map']['b']

    _save(auth_client, csrf, deleted_ids=[a],
          rows=[{'id': b, 'day': 2, 'description': 'b', 'person_id': person, 'amount': 0}])
    assert db.execute('SELECT COUNT(*) c FROM income').fetchone()['c'] == 0


def test_update_cannot_cross_month_boundary(auth_client, csrf, db, person):
    rid = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'September',
         'person_id': person, 'amount': 5.0}]).get_json()['id_map']['c1']

    body = {'v': 2, 'year': 2026, 'month': 10, 'rows': [
        {'id': rid, 'day': 9, 'description': 'gekapert', 'person_id': person, 'amount': 99.0}],
        'deleted_ids': []}
    auth_client.post('/api/income/save', json=body, headers={'X-CSRF-Token': csrf})
    row = db.execute('SELECT * FROM income WHERE id=?', (rid,)).fetchone()
    assert row['description'] == 'September'
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_save_income.py -v`
Erwartung: alle 6 Tests FAIL (409 fehlt, `id_map` fehlt).

- [ ] **Step 3: `save_income` neu implementieren**

```python
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
```

- [ ] **Step 4: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

---

### Task 6: `save_persons` und `save_categories` nicht-destruktiv

**Files:**
- Modify: `app/routes/main.py:814-836` (`save_persons`)
- Modify: `app/routes/main.py:838-874` (`save_categories`)
- Test: `tests/test_save_settings.py`

**Interfaces:**
- Consumes: `_require_v2`, `_bump_activity`
- Produces:
  - `POST /api/persons/save` akzeptiert `{v:2, rows:[{id?|client_key?, name, wallet_start_date, wallet_start_amount}], deleted_ids:[int]}` → `{success, persons:[...], id_map, activity_version, conflicts:[str]}`
  - `POST /api/categories/save` akzeptiert `{v:2, rows:[{id?|client_key?, name, color, is_split, splits:{person_id: share}}], deleted_ids:[int]}` → `{success, categories:[...], id_map, activity_version, conflicts:[str]}`
  - `conflicts` listet Namen, die wegen der `UNIQUE`-Bedingung **nicht** gespeichert wurden — sowohl beim Anlegen als auch beim Umbenennen. Leere Liste, wenn alles gespeichert wurde. Alle übrigen Zeilen desselben Requests werden trotzdem gespeichert; der Status bleibt `success: true`.
  - Beide nutzen den Scope `settings`.

- [ ] **Step 1: Failing Test schreiben**

`tests/test_save_settings.py`:

```python
def _save_persons(client, csrf, **payload):
    body = {'v': 2, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/persons/save', json=body, headers={'X-CSRF-Token': csrf})


def _save_cats(client, csrf, **payload):
    body = {'v': 2, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/categories/save', json=body, headers={'X-CSRF-Token': csrf})


def test_persons_legacy_array_rejected(auth_client, csrf):
    res = auth_client.post('/api/persons/save', json=[{'name': 'Alice'}],
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409


def test_person_added_by_other_user_survives(auth_client, csrf, db):
    """Das 'Charlie wird geloescht'-Szenario aus der Spec."""
    alice = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Alice'}]).get_json()['id_map']['a']
    bob = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'b', 'name': 'Bob'}]).get_json()['id_map']['b']
    charlie = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'c', 'name': 'Charlie'}]).get_json()['id_map']['c']

    # User 2 speichert seinen veralteten Stand (Alice + Bob, ohne Charlie)
    _save_persons(auth_client, csrf, rows=[
        {'id': alice, 'name': 'Alice neu'},
        {'id': bob, 'name': 'Bob'}])

    names = {r['name'] for r in db.execute('SELECT name FROM persons').fetchall()}
    assert 'Charlie' in names, 'Charlie wurde geloescht'
    assert 'Alice neu' in names
    assert charlie is not None


def test_person_explicit_delete(auth_client, csrf, db):
    pid = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Weg'}]).get_json()['id_map']['a']
    _save_persons(auth_client, csrf, deleted_ids=[pid])
    assert db.execute('SELECT COUNT(*) c FROM persons').fetchone()['c'] == 0


def test_person_wallet_fields_roundtrip(auth_client, csrf, db):
    pid = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Alice',
         'wallet_start_date': '2026-01', 'wallet_start_amount': 200.5}]).get_json()['id_map']['a']
    row = db.execute('SELECT * FROM persons WHERE id=?', (pid,)).fetchone()
    assert row['wallet_start_date'] == '2026-01'
    assert row['wallet_start_amount'] == 200.5


def test_person_invalid_wallet_date_becomes_null(auth_client, csrf, db):
    pid = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Alice', 'wallet_start_date': 'quatsch'}]).get_json()['id_map']['a']
    row = db.execute('SELECT * FROM persons WHERE id=?', (pid,)).fetchone()
    assert row['wallet_start_date'] is None


def test_category_added_by_other_user_survives(auth_client, csrf, db):
    miete = _save_cats(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Miete', 'color': '#111111', 'is_split': True}]).get_json()['id_map']['a']
    _save_cats(auth_client, csrf, rows=[
        {'client_key': 'b', 'name': 'Freizeit', 'color': '#222222'}])

    _save_cats(auth_client, csrf, rows=[{'id': miete, 'name': 'Miete', 'color': '#111111'}])

    names = {r['name'] for r in db.execute('SELECT name FROM categories').fetchall()}
    assert names == {'Miete', 'Freizeit'}


def test_category_splits_are_replaced_per_category(auth_client, csrf, db):
    pid = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'p', 'name': 'Alice'}]).get_json()['id_map']['p']
    cid = _save_cats(auth_client, csrf, rows=[
        {'client_key': 'c', 'name': 'Lebensmittel', 'is_split': True,
         'splits': {str(pid): 3}}]).get_json()['id_map']['c']

    row = db.execute('SELECT share FROM category_splits WHERE category_id=? AND person_id=?',
                     (cid, pid)).fetchone()
    assert row['share'] == 3

    _save_cats(auth_client, csrf, rows=[
        {'id': cid, 'name': 'Lebensmittel', 'is_split': True, 'splits': {str(pid): 1}}])
    row = db.execute('SELECT share FROM category_splits WHERE category_id=?', (cid,)).fetchone()
    assert row['share'] == 1


def test_settings_bumps_activity(auth_client, csrf, db):
    v1 = _save_persons(auth_client, csrf, rows=[{'client_key': 'a', 'name': 'A'}]).get_json()['activity_version']
    v2 = _save_cats(auth_client, csrf, rows=[{'client_key': 'b', 'name': 'B'}]).get_json()['activity_version']
    assert v2 == v1 + 1


def test_no_conflicts_reported_when_all_saved(auth_client, csrf, db):
    body = _save_persons(auth_client, csrf, rows=[{'client_key': 'a', 'name': 'Alice'}]).get_json()
    assert body['conflicts'] == []


def test_duplicate_person_name_is_reported(auth_client, csrf, db):
    _save_persons(auth_client, csrf, rows=[{'client_key': 'a', 'name': 'Alice'}])
    body = _save_persons(auth_client, csrf, rows=[{'client_key': 'b', 'name': 'Alice'}]).get_json()
    assert body['success'] is True
    assert body['conflicts'] == ['Alice']
    assert db.execute('SELECT COUNT(*) c FROM persons').fetchone()['c'] == 1


def test_rename_to_taken_name_is_reported_not_500(auth_client, csrf, db):
    alice = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Alice'}]).get_json()['id_map']['a']
    _save_persons(auth_client, csrf, rows=[{'client_key': 'b', 'name': 'Bob'}])

    res = _save_persons(auth_client, csrf, rows=[{'id': alice, 'name': 'Bob'}])
    assert res.status_code == 200, 'Umbenennen auf vergebenen Namen darf nicht crashen'
    assert res.get_json()['conflicts'] == ['Bob']
    names = {r['name'] for r in db.execute('SELECT name FROM persons').fetchall()}
    assert names == {'Alice', 'Bob'}


def test_other_rows_still_saved_despite_conflict(auth_client, csrf, db):
    _save_persons(auth_client, csrf, rows=[{'client_key': 'a', 'name': 'Alice'}])
    body = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'b', 'name': 'Alice'},
        {'client_key': 'c', 'name': 'Charlie'}]).get_json()
    assert body['conflicts'] == ['Alice']
    names = {r['name'] for r in db.execute('SELECT name FROM persons').fetchall()}
    assert names == {'Alice', 'Charlie'}


def test_duplicate_category_name_is_reported(auth_client, csrf, db):
    _save_cats(auth_client, csrf, rows=[{'client_key': 'a', 'name': 'Miete'}])
    body = _save_cats(auth_client, csrf, rows=[{'client_key': 'b', 'name': 'Miete'}]).get_json()
    assert body['conflicts'] == ['Miete']
    assert db.execute('SELECT COUNT(*) c FROM categories').fetchone()['c'] == 1
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_save_settings.py -v`
Erwartung: alle 8 Tests FAIL (kein 409, kein `id_map`).

- [ ] **Step 3: `save_persons` neu implementieren**

```python
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
                # NUR die UNIQUE-Verletzung gilt als Namenskonflikt. Ein
                # Sperr-Timeout o.ae. muss sichtbar bleiben statt dem Nutzer
                # faelschlich als "Name bereits vergeben" gemeldet zu werden.
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
                # NUR die UNIQUE-Verletzung gilt als Namenskonflikt. Ein
                # Sperr-Timeout o.ae. muss sichtbar bleiben statt dem Nutzer
                # faelschlich als "Name bereits vergeben" gemeldet zu werden.
                conflicts.append(n)

    activity_version = _bump_activity(conn, 'settings')
    conn.commit()
    result = [dict(r) for r in conn.execute('SELECT * FROM persons ORDER BY name')]
    conn.close()
    return jsonify({'success': True, 'persons': result, 'id_map': id_map,
                    'activity_version': activity_version, 'conflicts': conflicts})
```

- [ ] **Step 4: `save_categories` neu implementieren**

```python
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
        col = c.get('color', '#6c757d')
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
                # NUR die UNIQUE-Verletzung gilt als Namenskonflikt. Ein
                # Sperr-Timeout o.ae. muss sichtbar bleiben statt dem Nutzer
                # faelschlich als "Name bereits vergeben" gemeldet zu werden.
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
                # NUR die UNIQUE-Verletzung gilt als Namenskonflikt. Ein
                # Sperr-Timeout o.ae. muss sichtbar bleiben statt dem Nutzer
                # faelschlich als "Name bereits vergeben" gemeldet zu werden.
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
```

- [ ] **Step 5: Übersetzung für die Konfliktmeldung ergänzen**

`app/translations.py`, deutscher Block bei den `settings.`-Schlüsseln:

```python
        'settings.name_conflict': 'Name bereits vergeben, nicht gespeichert: {names}',
```

Englischer Block:

```python
        'settings.name_conflict': 'Name already taken, not saved: {names}',
```

- [ ] **Step 6: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

---

### Task 7: `save_recurring` nicht-destruktiv und Aufräumen

**Files:**
- Modify: `app/routes/main.py:985-1004` (`save_recurring`)
- Modify: `app/translations.py` (Schlüssel `srv.save_lock_error` entfernen)
- Test: `tests/test_save_settings.py` (erweitern)

**Interfaces:**
- Consumes: `_require_v2`, `_bump_activity`
- Produces: `POST /api/recurring/save` akzeptiert `{v:2, rows:[{id?|client_key?, day, description, person_id, category_id, amount, frequency, start_month}], deleted_ids:[int]}` → `{success, recurring:[...], id_map, activity_version}`. Scope `settings`.

- [ ] **Step 1: Failing Test schreiben**

An `tests/test_save_settings.py` anhängen:

```python
def _save_recur(client, csrf, **payload):
    body = {'v': 2, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/recurring/save', json=body, headers={'X-CSRF-Token': csrf})


def test_recurring_legacy_array_rejected(auth_client, csrf):
    res = auth_client.post('/api/recurring/save', json=[{'description': 'Netflix'}],
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409


def test_recurring_added_by_other_user_survives(auth_client, csrf, db):
    netflix = _save_recur(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Netflix', 'amount': 17.99}]).get_json()['id_map']['a']
    _save_recur(auth_client, csrf, rows=[
        {'client_key': 'b', 'day': 5, 'description': 'Spotify', 'amount': 9.99}])

    # Veralteter Stand mit nur Netflix darf Spotify nicht loeschen
    _save_recur(auth_client, csrf, rows=[
        {'id': netflix, 'day': 1, 'description': 'Netflix', 'amount': 17.99}])

    descs = {r['description'] for r in db.execute('SELECT description FROM recurring_expenses').fetchall()}
    assert descs == {'Netflix', 'Spotify'}


def test_recurring_update_and_explicit_delete(auth_client, csrf, db):
    rid = _save_recur(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Netflix', 'amount': 17.99}]).get_json()['id_map']['a']

    _save_recur(auth_client, csrf, rows=[
        {'id': rid, 'day': 3, 'description': 'Netflix Premium', 'amount': 19.99,
         'frequency': 'yearly', 'start_month': 4}])
    row = db.execute('SELECT * FROM recurring_expenses WHERE id=?', (rid,)).fetchone()
    assert row['description'] == 'Netflix Premium'
    assert row['day'] == 3
    assert row['frequency'] == 'yearly'
    assert row['start_month'] == 4

    _save_recur(auth_client, csrf, deleted_ids=[rid])
    assert db.execute('SELECT COUNT(*) c FROM recurring_expenses').fetchone()['c'] == 0


def test_recurring_monthly_forces_start_month_one(auth_client, csrf, db):
    rid = _save_recur(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Miete', 'amount': 750.0,
         'frequency': 'monthly', 'start_month': 7}]).get_json()['id_map']['a']
    row = db.execute('SELECT start_month FROM recurring_expenses WHERE id=?', (rid,)).fetchone()
    assert row['start_month'] == 1
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_save_settings.py -v -k recurring`
Erwartung: alle 4 neuen Tests FAIL.

- [ ] **Step 3: `save_recurring` neu implementieren**

```python
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
        if not desc and amt == 0:
            continue
        pid  = int(r['person_id'])   if r.get('person_id')   else None
        cid  = int(r['category_id']) if r.get('category_id') else None
        freq = r.get('frequency', 'monthly')
        sm   = int(r.get('start_month', 1)) if freq != 'monthly' else 1
        day  = int(r.get('day') or 1)
        try:
            rid = int(r['id']) if r.get('id') else None
        except (TypeError, ValueError):
            rid = None

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
```

- [ ] **Step 4: Obsolete Übersetzung entfernen**

Die Notbremse `save_lock_error` existiert nach Task 4 und 5 nicht mehr. Beide Zeilen aus `app/translations.py` löschen (deutscher und englischer Block):

```bash
grep -n "save_lock_error" app/translations.py app/routes/main.py app/templates/*.html
```

Erwartung nach dem Löschen: keine Treffer mehr.

- [ ] **Step 5: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

---

### Task 8: Gemeinsamer Live-Hinweis-Helfer in `base.html`

**Files:**
- Modify: `app/templates/base.html` (neuer Script-Block vor `{% block extra_script %}`)
- Modify: `app/translations.py` (Schlüssel für den Hinweis)

**Interfaces:**
- Consumes: `POST /api/activity` aus Task 3, Übersetzungsfunktion `t()` aus `base.html`
- Produces: `window.initActivityWatch({scope, version, onRemoteChange, container})` — startet das 10-s-Polling, rendert das Anwesenheits-Badge selbst und ruft `onRemoteChange(info)` auf, wenn die Version durch einen **anderen** Nutzer gestiegen ist. `window.activityWatchVersion(v)` setzt die Basisversion neu (nach eigenem Speichern).

- [ ] **Step 1: Übersetzungen ergänzen**

`app/translations.py`, deutscher Block (bei den `month.`-Schlüsseln):

```python
        'activity.others_here': '{names} bearbeitet diese Seite gerade',
        'activity.changed_by': '{name} hat Änderungen gespeichert.',
        'activity.reload': 'Neu laden',
```

Englischer Block:

```python
        'activity.others_here': '{names} is editing this page right now',
        'activity.changed_by': '{name} saved changes.',
        'activity.reload': 'Reload',
```

- [ ] **Step 2: Helfer in `base.html` ergänzen**

In `app/templates/base.html` direkt **vor** `{% block extra_script %}{% endblock %}` einfügen:

```html
<script>
/* ── LIVE-HINWEIS AUF PARALLELE BEARBEITUNG ───────────────────────────────────
 * Pollt alle 10 s POST /api/activity. Bewusst Polling statt SSE: der Sync-Worker
 * hat 4 Threads, eine offene SSE-Verbindung wuerde dauerhaft einen belegen.
 * Meldet nur Aenderungen ANDERER Nutzer; eigenes Speichern hebt die Basisversion
 * ueber activityWatchVersion() an. */
(function () {
  const POLL_MS = 10000;
  let _scope = null, _baseVersion = 0, _onRemoteChange = null, _container = null, _timer = null;

  /** HTML-Escaping fuer Werte, die als innerHTML eingesetzt werden.
   * t() ersetzt Platzhalter ohne jede Maskierung, und Benutzernamen sind
   * frei waehlbar - ohne dieses Escaping waere ein Name wie
   * <img src=x onerror=...> gespeichertes XSS bei allen Mitbenutzern. */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function render(info) {
    if (!_container) return;
    const parts = [];
    if (info.others && info.others.length) {
      parts.push(`<span class="badge bg-secondary-subtle text-secondary-emphasis">
        <i class="bi bi-people me-1"></i>${t('activity.others_here', {names: info.others.map(esc).join(', ')})}</span>`);
    }
    if (info.version > _baseVersion) {
      parts.push(`<span class="badge bg-warning-subtle text-warning-emphasis">
        <i class="bi bi-exclamation-triangle me-1"></i>${t('activity.changed_by', {name: esc(info.updated_by) || '?'})}
        <button type="button" class="btn btn-sm btn-link p-0 ms-1 align-baseline"
                onclick="location.reload()">${t('activity.reload')}</button></span>`);
    }
    _container.innerHTML = parts.join(' ');
  }

  async function poll() {
    try {
      const res = await fetch('/api/activity', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scope: _scope})
      });
      const info = await res.json();
      if (!info.success) return;
      render(info);
      if (info.version > _baseVersion && typeof _onRemoteChange === 'function') {
        _onRemoteChange(info);
      }
    } catch (e) { /* Netzwerkfehler still ignorieren, naechster Tick versucht es erneut */ }
  }

  window.initActivityWatch = function (opts) {
    _scope = opts.scope;
    _baseVersion = opts.version || 0;
    _onRemoteChange = opts.onRemoteChange || null;
    _container = opts.container ? document.getElementById(opts.container) : null;
    clearInterval(_timer);
    _timer = setInterval(poll, POLL_MS);
    poll();
  };

  /** Nach eigenem Speichern aufrufen, damit die eigene Aenderung kein Banner ausloest. */
  window.activityWatchVersion = function (v) {
    if (typeof v === 'number' && v > _baseVersion) _baseVersion = v;
  };
})();
</script>
```

- [ ] **Step 3: Verifizieren, dass alle Seiten weiter rendern**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS (die Smoke-Tests rendern `/overview`, `/month/2026/9` und damit `base.html`).

- [ ] **Step 4: Prüfen, dass der Helfer eingebunden ist**

```bash
.venv-dev/bin/python -c "
import tests.conftest as c
app = c.create_app()
cl = app.test_client()
cl.post('/login', data={'username':'admin','password':'test-password-123'})
html = cl.get('/month/2026/9').get_data(as_text=True)
assert 'initActivityWatch' in html, 'Helfer fehlt im gerenderten HTML'
print('OK: initActivityWatch vorhanden')"
```

Erwartung: `OK: initActivityWatch vorhanden`

---

### Task 9: `month.html` auf Einzelzeilen und Live-Hinweis umstellen

**Files:**
- Modify: `app/templates/month.html:102-106` (Kopfzeile: Container für den Hinweis)
- Modify: `app/templates/month.html:366-378` (Konstanten)
- Modify: `app/templates/month.html:611-648` (`addRow`)
- Modify: `app/templates/month.html:650-680` (`addIncomeRow`)
- Modify: `app/templates/month.html:737-757` (`collectRows`, `getIncomeRows`)
- Modify: `app/templates/month.html:760-781` (`sortTable`, `clearTable`)
- Modify: `app/templates/month.html:788-844` (`saveExpenses`, `saveIncome`)
- Modify: `app/templates/month.html:1290-1300` (`DOMContentLoaded`)

**Interfaces:**
- Consumes: `id_map`/`activity_version` aus Tasks 4–5, `initActivityWatch`/`activityWatchVersion` aus Task 8, `activity_scope`/`activity_version` aus Task 3
- Produces: keine (Blattknoten)

- [ ] **Step 1: Container und Basisversion im Template**

In `app/templates/month.html` die Kopfzeile ergänzen — nach dem schließenden `</h2>` und vor dem „weiter"-Link:

```html
  <a href="/month/{{ next_year }}/{{ next_month }}" class="btn btn-outline-secondary btn-sm month-nav-btn"><i class="fas fa-chevron-right"></i></a>
</div>
<div id="activityBar" class="mb-3 d-flex flex-wrap gap-2"></div>
```

Bei den Konstanten (nach `const INCOME_INIT = ...`):

```javascript
const ACTIVITY_SCOPE   = {{ activity_scope | tojson }};
const ACTIVITY_VERSION = {{ activity_version | tojson }};
let _pendingExpDeletes = [];
let _pendingIncDeletes = [];
let _ckSeq = 0;
```

- [ ] **Step 2: `addRow` und `addIncomeRow` tragen die DB-`id`**

`addRow`: Signatur um einen **letzten** Parameter erweitern und die id ablegen:

```javascript
function addRow(day='', desc='', pid='', cid='', amt='', isCash=false, pfpid='', receiptFn='', focusRow=true, rowId=null) {
  const tr = document.createElement('tr');
  if (rowId) tr.dataset.rowId = rowId;
```

Am Ende von `addRow` (nach `reapplyFilters();`) nichts weiter nötig.

`addIncomeRow` analog:

```javascript
function addIncomeRow(day='', desc='', pid='', cid='', amt='', focusRow=true, rowId=null) {
  const tr = document.createElement('tr');
  if (rowId) tr.dataset.rowId = rowId;
```

- [ ] **Step 3: Löschen-Buttons merken die `id` vor**

In `addRow` die Löschen-Zelle ersetzen:

```javascript
    <td><button type="button" class="btn btn-sm btn-outline-danger px-2" onclick="removeExpenseRow(this)"><i class="fas fa-times"></i></button></td>`;
```

In `addIncomeRow`:

```javascript
    <td><button type="button" class="btn btn-sm btn-outline-danger px-2" onclick="removeIncomeRow(this)"><i class="fas fa-times"></i></button></td>`;
```

Und beide Helfer direkt nach `addIncomeRow` ergänzen:

```javascript
/* ── ZEILEN ENTFERNEN ─────────────────────────────────────────
 * Die DB-id muss vor dem Entfernen gemerkt werden, sonst weiss der Server
 * nicht, dass diese Zeile geloescht werden soll (nicht-destruktives Speichern). */
function removeExpenseRow(btn) {
  const tr = btn.closest('tr');
  if (tr.dataset.rowId) _pendingExpDeletes.push(parseInt(tr.dataset.rowId, 10));
  tr.remove();
  updateFooter();
  scheduleAutoSaveExpense();
}

function removeIncomeRow(btn) {
  const tr = btn.closest('tr');
  if (tr.dataset.rowId) _pendingIncDeletes.push(parseInt(tr.dataset.rowId, 10));
  tr.remove();
  updateIncomeFooter();
  scheduleAutoSaveIncome();
}
```

- [ ] **Step 4: `collectRows` und `getIncomeRows` liefern `id` bzw. `client_key`**

```javascript
/* ── DATEN EINSAMMELN ─────────────────────────────────────────
 * Zeilen mit dataset.rowId werden serverseitig aktualisiert, Zeilen ohne
 * erhalten einen client_key, unter dem der Server die neue id zurueckmeldet. */
function rowIdentity(r) {
  if (r.dataset.rowId) return { id: parseInt(r.dataset.rowId, 10) };
  if (!r.dataset.clientKey) r.dataset.clientKey = 'c' + (++_ckSeq);
  return { client_key: r.dataset.clientKey };
}

function collectRows() {
  return Array.from(document.querySelectorAll('#expenseBody tr')).map(r => ({
    ...rowIdentity(r),
    day: r.querySelector('[name=day]').value,
    description: r.querySelector('[name=description]').value,
    person_id: r.querySelector('[name=person_id]').value,
    paid_for_person_id: r.querySelector('[name=paid_for_person_id]').value || '',
    category_id: r.querySelector('[name=category_id]').value,
    amount: parseAmtVal(r.querySelector('[name=amount]')),
    is_cash: r.querySelector('.cash-check')?.checked || false,
    receipt_filename: r.querySelector('[name=receipt_filename]')?.value || ''
  }));
}

function getIncomeRows() {
  return Array.from(document.querySelectorAll('#incomeBody tr')).map(r => ({
    ...rowIdentity(r),
    day: r.querySelector('[name=inc_day]').value,
    description: r.querySelector('[name=inc_desc]').value,
    person_id: r.querySelector('[name=inc_pid]').value,
    category_id: r.querySelector('[name=inc_cid]').value,
    amount: parseAmtVal(r.querySelector('[name=amount]'))
  }));
}
```

- [ ] **Step 5: `sortTable` und `clearTable` erhalten die `id`**

```javascript
/* ── TABELLE SORTIEREN ────────────────────────────────────── */
function sortTable() {
  const tbody = document.getElementById('expenseBody');
  const data = Array.from(tbody.querySelectorAll('tr')).map(tr => ({
    rowId: tr.dataset.rowId || null,
    day: parseInt(tr.querySelector('[name=day]').value) || 0,
    description: tr.querySelector('[name=description]').value,
    person_id: tr.querySelector('[name=person_id]').value,
    paid_for_person_id: tr.querySelector('[name=paid_for_person_id]').value || '',
    category_id: tr.querySelector('[name=category_id]').value,
    amount: tr.querySelector('[name=amount]').dataset.formula || tr.querySelector('[name=amount]').value,
    is_cash: tr.querySelector('.cash-check')?.checked || false,
    receipt_filename: tr.querySelector('[name=receipt_filename]')?.value || ''
  }));
  data.sort((a, b) => a.day !== b.day ? a.day - b.day : a.description.localeCompare(b.description, 'de', {sensitivity: 'base'}));
  tbody.innerHTML = '';
  data.forEach(r => addRow(r.day || '', r.description, r.person_id, r.category_id, r.amount, r.is_cash, r.paid_for_person_id, r.receipt_filename, false, r.rowId));
}

/* ── TABELLE LEEREN ───────────────────────────────────────── */
function clearTable(bodyId, isExpense) {
  const body = document.getElementById(bodyId);
  Array.from(body.querySelectorAll('tr')).forEach(tr => {
    if (!tr.dataset.rowId) return;
    const id = parseInt(tr.dataset.rowId, 10);
    if (isExpense) _pendingExpDeletes.push(id); else _pendingIncDeletes.push(id);
  });
  body.innerHTML = '';
  if (isExpense) updateFooter(); else updateIncomeFooter();
}
```

- [ ] **Step 6: `saveExpenses` und `saveIncome` auf v2 umstellen**

In `saveExpenses` den `fetch`-Block und die Erfolgsbehandlung ersetzen:

```javascript
    const sentDeletes = _pendingExpDeletes.slice();
    const res = await fetch('/api/expenses/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
      body: JSON.stringify({ v: 2, year: YEAR, month: MONTH,
                             rows: collectRows(), deleted_ids: sentDeletes })
    });
    const data = await res.json();
    if (data.success) {
      // Neue ids einstempeln, BEVOR sortiert wird - sonst legt der naechste
      // Autosave dieselben Zeilen erneut an.
      Object.entries(data.id_map || {}).forEach(([ck, id]) => {
        const tr = document.querySelector(`#expenseBody tr[data-client-key="${ck}"]`);
        if (tr) { tr.dataset.rowId = id; delete tr.dataset.clientKey; }
      });
      _pendingExpDeletes = _pendingExpDeletes.filter(id => !sentDeletes.includes(id));
      if (typeof activityWatchVersion === 'function') activityWatchVersion(data.activity_version);
      _suppressAutoSave = true;
```

Der restliche Erfolgsblock (`if (!silent) sortTable(); ... renderSavings(...)`) bleibt unverändert.

In `saveIncome` analog:

```javascript
    const sentDeletes = _pendingIncDeletes.slice();
    const res = await fetch('/api/income/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
      body: JSON.stringify({ v: 2, year: YEAR, month: MONTH,
                             rows: getIncomeRows(), deleted_ids: sentDeletes })
    });
    const data = await res.json();
    if (data.success) {
      Object.entries(data.id_map || {}).forEach(([ck, id]) => {
        const tr = document.querySelector(`#incomeBody tr[data-client-key="${ck}"]`);
        if (tr) { tr.dataset.rowId = id; delete tr.dataset.clientKey; }
      });
      _pendingIncDeletes = _pendingIncDeletes.filter(id => !sentDeletes.includes(id));
      if (typeof activityWatchVersion === 'function') activityWatchVersion(data.activity_version);
      _suppressAutoSave = true;
```

- [ ] **Step 7: Initialisierung — ids durchziehen und Watch starten**

Im `DOMContentLoaded`-Block die beiden `forEach`-Zeilen ersetzen und den Watch starten:

```javascript
  EXISTING.forEach(e => addRow(e.day, e.description, e.person_id, e.category_id, e.amount, !!e.is_cash, e.paid_for_person_id || '', e.receipt_filename || '', false, e.id));
  INCOME_INIT.forEach(i => addIncomeRow(i.day, i.description, i.person_id, i.category_id, i.amount, false, i.id));
```

Am Ende desselben Blocks ergänzen:

```javascript
  initActivityWatch({
    scope: ACTIVITY_SCOPE,
    version: ACTIVITY_VERSION,
    container: 'activityBar'
    // Absichtlich kein onRemoteChange: kein Auto-Reload waehrend der Eingabe.
    // Der Nutzer entscheidet ueber den "Neu laden"-Button im Banner.
  });
```

- [ ] **Step 8: Manuell im Browser prüfen**

```bash
.venv-dev/bin/python -c "
import os
os.environ.setdefault('DATABASE_PATH', '/tmp/kst-manual/kassenstuerzle.db')
os.environ.setdefault('ADMIN_PASSWORD', 'test-admin-password-123')
os.environ.setdefault('DISABLE_SCHEDULER', '1')
os.makedirs('/tmp/kst-manual', exist_ok=True)
import sys; sys.path.insert(0, '.')
from app import create_app
create_app().run(host='127.0.0.1', port=5060)"
```

Im Browser auf `http://127.0.0.1:5060` prüfen:
1. Person anlegen, Ausgabenzeile eintragen, speichern → Zeile bleibt nach Reload erhalten.
2. Dieselbe Zeile bearbeiten, 2 s warten (Autosave) → nach Reload genau **eine** Zeile, kein Duplikat.
3. Zeile löschen, speichern, Reload → Zeile ist weg.
4. Betrag leeren, speichern, Reload → Zeile ist weg.
5. Zweites Browserfenster öffnen, dort speichern → im ersten Fenster erscheint binnen 10 s das Banner „… hat Änderungen gespeichert" mit „Neu laden".

- [ ] **Step 9: Gesamtsuite prüfen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

---

### Task 10: `settings.html` auf v2 und Live-Hinweis umstellen

**Files:**
- Modify: `app/templates/settings.html:499-520` (Personen: Löschen + Speichern)
- Modify: `app/templates/settings.html:537-570` (Kategorien: Löschen + Speichern)
- Modify: `app/templates/settings.html:576-610` (Daueraufträge: Löschen + Speichern)
- Modify: `app/templates/settings.html` (Container `settingsActivityBar`, Watch-Start)
- Modify: `app/routes/main.py` (`settings`-Route: `activity_version` übergeben)

**Interfaces:**
- Consumes: `id_map`/`activity_version` aus Tasks 6–7, `initActivityWatch` aus Task 8
- Produces: keine (Blattknoten)

- [ ] **Step 1: `activity_version` in der Settings-Route**

In `app/routes/main.py`, Funktion `settings` (`main.py:1097-1105`), den Block ab `category_splits` ersetzen — die Abfrage muss **vor** `conn.close()` stehen:

```python
    category_splits = get_category_splits_map(conn)
    _act_row = conn.execute("SELECT version FROM activity WHERE scope='settings'").fetchone()
    activity_version = _act_row['version'] if _act_row else 0
    conn.close()
    return render_template('settings.html', active='settings', persons=persons, categories=cats,
                            recurring=recurring, category_splits=category_splits,
                            activity_version=activity_version)
```

- [ ] **Step 2: Container und Merklisten im Template**

Direkt nach der Überschrift „Einstellungen" einfügen:

```html
<div id="settingsActivityBar" class="mb-3 d-flex flex-wrap gap-2"></div>
```

Im `extra_script`-Block oben ergänzen:

```javascript
const ACTIVITY_VERSION = {{ activity_version | tojson }};
let _pendingPersonDeletes = [];
let _pendingCatDeletes = [];
let _pendingRecurDeletes = [];
let _ckSeq = 0;
function nextClientKey(el) {
  if (!el.dataset.clientKey) el.dataset.clientKey = 'c' + (++_ckSeq);
  return el.dataset.clientKey;
}
function rowIdentity(el) {
  return el.dataset.id ? { id: parseInt(el.dataset.id, 10) } : { client_key: nextClientKey(el) };
}
function applyIdMap(idMap, selector) {
  Object.entries(idMap || {}).forEach(([ck, id]) => {
    const el = document.querySelector(`${selector}[data-client-key="${ck}"]`);
    if (el) { el.dataset.id = id; delete el.dataset.clientKey; }
  });
}
```

- [ ] **Step 3: Personen — Löschen merken und v2 senden**

Die Entfernen-Zeile ersetzen:

```javascript
  if (e.target.closest('.remove-person-btn')) {
    const row = e.target.closest('.person-row');
    if (row.dataset.id) _pendingPersonDeletes.push(parseInt(row.dataset.id, 10));
    row.remove();
  }
```

Den Speichern-Handler (`settings.html:509-519`) vollständig ersetzen:

```javascript
document.getElementById('save-persons-btn').addEventListener('click', async () => {
  const rows = [...document.querySelectorAll('.person-row')].map(r => ({
    ...rowIdentity(r),
    name: r.querySelector('.person-name').value.trim(),
    wallet_start_date: r.querySelector('.person-wallet-date').value || null,
    wallet_start_amount: parseFloat(r.querySelector('.person-wallet-amount').value) || 0
  })).filter(p => p.name);
  const sentDeletes = _pendingPersonDeletes.slice();
  const res = await fetch('/api/persons/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ v: 2, rows, deleted_ids: sentDeletes })
  });
  const d = await res.json();
  if (d.success) {
    applyIdMap(d.id_map, '.person-row');
    _pendingPersonDeletes = _pendingPersonDeletes.filter(id => !sentDeletes.includes(id));
    if (typeof activityWatchVersion === 'function') activityWatchVersion(d.activity_version);
    if (d.conflicts && d.conflicts.length) {
      showToast(t('settings.name_conflict', {names: d.conflicts.join(', ')}), 'warning');
    } else {
      showToast(t('settings.toast_persons_saved'), 'success');
    }
  } else {
    showToast(t('settings.toast_error', {error: d.error||''}), 'danger');
  }
});
```

- [ ] **Step 4: Kategorien — Löschen merken und v2 senden**

```javascript
  if (e.target.closest('.remove-cat-btn')) {
    const wrap = e.target.closest('.cat-row-wrap');
    if (wrap.dataset.id) _pendingCatDeletes.push(parseInt(wrap.dataset.id, 10));
    wrap.remove();
  }
```

Den Speichern-Handler (`settings.html:551-569`) vollständig ersetzen:

```javascript
document.getElementById('save-cats-btn').addEventListener('click', async () => {
  const rows = [...document.querySelectorAll('.cat-row-wrap')].map(r => {
    const splits = {};
    r.querySelectorAll('.cat-split-share').forEach(inp => {
      const v = parseFloat(inp.value);
      if (v > 0) splits[inp.dataset.personId] = v;
    });
    return {
      ...rowIdentity(r),
      name: r.querySelector('.cat-name').value.trim(),
      color: r.querySelector('.cat-color').value.replace('#',''),
      is_split: r.querySelector('.cat-split').checked ? 1 : 0,
      splits,
    };
  }).filter(c => c.name);
  const sentDeletes = _pendingCatDeletes.slice();
  const res = await fetch('/api/categories/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ v: 2, rows, deleted_ids: sentDeletes })
  });
  const d = await res.json();
  if (d.success) {
    applyIdMap(d.id_map, '.cat-row-wrap');
    _pendingCatDeletes = _pendingCatDeletes.filter(id => !sentDeletes.includes(id));
    if (typeof activityWatchVersion === 'function') activityWatchVersion(d.activity_version);
    if (d.conflicts && d.conflicts.length) {
      showToast(t('settings.name_conflict', {names: d.conflicts.join(', ')}), 'warning');
    } else {
      showToast(t('settings.toast_categories_saved'), 'success');
    }
  } else {
    showToast(t('settings.toast_error', {error: d.error||''}), 'danger');
  }
});
```

- [ ] **Step 5: Daueraufträge — Löschen merken und v2 senden**

```javascript
  if (e.target.closest('.remove-rec-btn')) {
    const row = e.target.closest('.recurring-row');
    if (row.dataset.id) _pendingRecurDeletes.push(parseInt(row.dataset.id, 10));
    row.remove();
  }
```

Den Speichern-Handler (`settings.html:594-609`, Button-id ist `save-recurring-btn`) vollständig ersetzen:

```javascript
document.getElementById('save-recurring-btn').addEventListener('click', async () => {
  const rows = [...document.querySelectorAll('.recurring-row')].map(r => ({
    ...rowIdentity(r),
    day: parseInt(r.querySelector('.rec-day').value) || 1,
    description: r.querySelector('.rec-desc').value.trim(),
    person_id: r.querySelector('.rec-person').value || null,
    category_id: r.querySelector('.rec-cat').value || null,
    amount: parseFloat(r.querySelector('.rec-amount').value) || 0,
    frequency: r.querySelector('.rec-freq').value,
    start_month: 1
  })).filter(r => r.description || r.amount > 0);
  const sentDeletes = _pendingRecurDeletes.slice();
  const res = await fetch('/api/recurring/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ v: 2, rows, deleted_ids: sentDeletes })
  });
  const d = await res.json();
  if (d.success) {
    applyIdMap(d.id_map, '.recurring-row');
    _pendingRecurDeletes = _pendingRecurDeletes.filter(id => !sentDeletes.includes(id));
    if (typeof activityWatchVersion === 'function') activityWatchVersion(d.activity_version);
  }
  showToast(d.success ? t('settings.toast_recurring_saved') : t('settings.toast_error', {error: d.error||''}),
            d.success ? 'success' : 'danger');
});
```

- [ ] **Step 6: Watch starten**

Am Ende des `extra_script`-Blocks:

```javascript
initActivityWatch({ scope: 'settings', version: ACTIVITY_VERSION, container: 'settingsActivityBar' });
```

- [ ] **Step 7: Manuell im Browser prüfen**

Testserver wie in Task 9 Step 8 starten, dann auf `/settings`:
1. Person anlegen, speichern, Reload → Person ist da.
2. Person umbenennen, speichern, Reload → Name geändert, **keine zweite Person**.
3. Person löschen, speichern, Reload → Person ist weg.
4. Kategorie mit Split-Verhältnis anlegen, speichern, Reload → Werte erhalten.
5. Dauerauftrag anlegen, dann einen zweiten, dann den ersten bearbeiten und speichern → beide vorhanden.
6. Zweites Fenster: dort Kategorie anlegen → im ersten Fenster erscheint binnen 10 s das Banner.

- [ ] **Step 8: Gesamtsuite und Abschlussprüfung**

Run: `.venv-dev/bin/pytest tests/ -v`
Erwartung: alle Tests PASS.

Abschließend sicherstellen, dass keine Altlasten verbleiben:

```bash
grep -rn "expenses: collectRows\|income: getIncomeRows\|save_lock_error\|existing - incoming\|DELETE FROM recurring_expenses'" app/ | grep -v ".pyc"
```

Erwartung: keine Treffer.

---

## Abschluss Phase A

Nach Task 10 sind alle drei Datenverlust-Szenarien aus der Spec behoben und der Live-Hinweis aktiv. Erst danach beginnt Phase B (Benutzerprofile) mit einem eigenen Plan; der Migrationsprozess M0–M8 der Spec gehört vollständig dorthin.
