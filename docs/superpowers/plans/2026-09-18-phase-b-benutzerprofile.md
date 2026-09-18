# Phase B — Benutzerprofile: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jedes Haushaltsmitglied bekommt einen eigenen Login, der optional mit einer Person verknüpft ist, plus die Rollen `admin` und `member` — ohne dass eine bestehende Instanz beim Update ausgesperrt wird oder Daten verliert.

**Architecture:** Die vorhandene `users`-Tabelle wird um `person_id`, `role`, `is_active`, `must_change_password`, `created_at`, `last_login` und `session_epoch` erweitert. `login_required` prüft künftig zusätzlich `is_active` und `session_epoch` (der Hebel, um Cookie-Sessions serverseitig zu beenden); ein neuer `admin_required` schützt Benutzerverwaltung und Backup. Ein Einrichtungsassistent führt den Admin einmalig durch die Zuordnung, erzwingt aber nichts — der bestehende gemeinsame Login funktioniert unverändert weiter.

**Tech Stack:** Python 3.11, Flask 3.0.0, SQLite/SQLCipher (`sqlcipher3` mit Fallback auf `sqlite3`), Jinja2, Bootstrap 5.3.3, pytest, `werkzeug.security` (scrypt)

## Global Constraints

- Referenz-Spec: `docs/superpowers/specs/2026-09-18-benutzerprofile-und-nebenlaeufigkeit-design.md`, Abschnitte 4 und 5. Phase A (Abschnitt 3) ist bereits umgesetzt und in `main` gemerged.
- **Eine bestehende Instanz darf nach dem Update nie ohne erreichbaren Admin dastehen.** Die Admin-Kaskade läuft bei jedem Start, ist idempotent und selbstheilend.
- **Der bestehende Login muss nach dem Update unverändert funktionieren.** Es wird nichts erzwungen außer dem Passwortwechsel für *neu angelegte* Konten.
- Rollen werden **serverseitig am Endpunkt** geprüft, niemals nur durch Ausblenden im UI.
- Rollentrennung: Nur Admin darf Benutzerverwaltung und `/api/backup/*`. Buchungen, Einstellungen, CSV-/PDF-Import bleiben für alle offen (Entscheidungen E6 und Q2).
- Bestehende Personen werden **nicht** automatisch zu Logins. Einzige Ausnahme: genau ein Benutzer und genau eine Person → eindeutig, wird verknüpft.
- Initialpasswörter werden genau einmal angezeigt (Entscheidung P1) und erzwingen `must_change_password`.
- Die Fehlermeldung beim Login bleibt unspezifisch, um Account-Enumeration zu verhindern.
- Übersetzungen immer paarweise in `TRANSLATIONS['de']` **und** `TRANSLATIONS['en']`.
- Payload-Version `"v": 2` und `_require_v2` gelten unverändert für alle Speicher-Endpunkte aus Phase A.
- **Kein `innerHTML` mit Benutzernamen.** Neue UI baut Zeilen über `textContent`/DOM-Methoden. Grund: Das Abschluss-Review von Phase A hat eine bestehende XSS-Klasse in der Namensdarstellung gefunden; die wird in der anschließenden Sicherheitsprüfung behandelt — dieser Plan darf keine neuen Instanzen hinzufügen.
- Tests laufen unverschlüsselt gegen eine temporäre DB; `DATABASE_PATH` muss vor dem Import von `app` gesetzt sein (`tests/conftest.py`). Suite: `.venv-dev/bin/pytest tests/ -q`.
- **Niemals aus `data_/` oder `data/` lesen oder dorthin schreiben** — dort liegen die echten Finanzdaten und der Schlüssel.

---

## File Structure

| Datei | Verantwortung | Änderung |
|---|---|---|
| `app/routes/main.py` | Schema-Migration, Admin-Kaskade, `login_required`/`admin_required`, Login-Sperre, Benutzer-API, Assistent-Endpunkte | Ändern |
| `app/templates/settings.html` | Karte „Benutzer", Assistent-Banner und -Dialog | Ändern |
| `app/templates/force_password_change.html` | Eigenständige Seite für den erzwungenen Passwortwechsel | Neu |
| `app/translations.py` | Neue Schlüssel für Rollen, Benutzerverwaltung, Assistent | Ändern |
| `tests/test_profiles_migration.py` | Schema, Admin-Kaskade, Auto-Verknüpfung, Idempotenz | Neu |
| `tests/test_auth_roles.py` | `is_active`, `session_epoch`, `admin_required`, Backup-Schutz | Neu |
| `tests/test_login_lockout.py` | Sperre pro Benutzername statt pro IP | Neu |
| `tests/test_users_api.py` | Benutzer-CRUD und Schutzgeländer | Neu |
| `tests/test_setup_wizard.py` | Namensableitung, Vorschläge, Abschluss-Flag | Neu |

---

### Task 1: Schema-Migration und Admin-Kaskade

**Files:**
- Modify: `app/routes/main.py` (`init_db`, neue Helfer `_ensure_admin`, `_autolink_single_person`)
- Test: `tests/test_profiles_migration.py`

**Interfaces:**
- Consumes: `create_backup(label)` (bereits vorhanden), `init_db()`
- Produces:
  - `users` hat zusätzlich `person_id`, `role`, `is_active`, `must_change_password`, `created_at`, `last_login`, `session_epoch`
  - Partieller Unique-Index `idx_users_person` auf `users(person_id)`
  - Tabelle `app_meta(key TEXT PRIMARY KEY, value TEXT)`
  - `_ensure_admin(conn) -> None` — garantiert mindestens einen aktiven Admin, idempotent
  - `_autolink_single_person(conn) -> None` — verknüpft nur im eindeutigen Fall

- [ ] **Step 1: Failing Test schreiben**

`tests/test_profiles_migration.py`:

```python
from app.routes import main as m


def _spalten(db, tabelle):
    return {r[1] for r in db.execute(f'PRAGMA table_info({tabelle})')}


def test_users_hat_neue_spalten(db):
    spalten = _spalten(db, 'users')
    for name in ('person_id', 'role', 'is_active', 'must_change_password',
                 'created_at', 'last_login', 'session_epoch'):
        assert name in spalten, f'Spalte {name} fehlt'


def test_app_meta_existiert(db):
    assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_meta'").fetchone()


def test_bestehender_admin_behaelt_adminrolle(db):
    """Der ALTER-Default ist 'member' - ohne die Kaskade waere der einzige
    Benutzer degradiert und niemand koennte mehr Benutzer verwalten."""
    row = db.execute("SELECT role, is_active FROM users WHERE username='admin'").fetchone()
    assert row['role'] == 'admin'
    assert row['is_active'] == 1


def test_kaskade_befoerdert_aeltesten_benutzer(app, db):
    """Kein Benutzer heisst 'admin' und keiner ist Admin -> der aelteste wird es."""
    db.execute("UPDATE users SET role='member'")
    db.execute("UPDATE users SET username='philipp' WHERE username='admin'")
    db.commit()
    with app.app_context():
        m.init_db()
    row = db.execute("SELECT username, role FROM users ORDER BY id LIMIT 1").fetchone()
    assert row['role'] == 'admin'
    db.execute("UPDATE users SET username='admin' WHERE username='philipp'")
    db.commit()


def test_kaskade_ist_idempotent(app, db):
    with app.app_context():
        m.init_db()
        m.init_db()
    anzahl = db.execute("SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1").fetchone()['c']
    assert anzahl == 1


def test_autolink_nur_bei_eindeutigkeit(app, db):
    """Genau ein Benutzer und genau eine Person -> verknuepfen."""
    db.execute("UPDATE users SET person_id=NULL")
    cur = db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    pid = cur.lastrowid
    db.commit()
    with app.app_context():
        m.init_db()
    assert db.execute('SELECT person_id FROM users').fetchone()['person_id'] == pid


def test_kein_autolink_bei_mehreren_personen(app, db):
    db.execute("UPDATE users SET person_id=NULL")
    db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    db.execute("INSERT INTO persons (name) VALUES ('Bob')")
    db.commit()
    with app.app_context():
        m.init_db()
    assert db.execute('SELECT person_id FROM users').fetchone()['person_id'] is None


def test_person_kann_nur_einmal_verknuepft_werden(db):
    cur = db.execute("INSERT INTO persons (name) VALUES ('Carla')")
    pid = cur.lastrowid
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (pid,))
    db.execute("INSERT INTO users (username, password_hash, person_id) VALUES ('zweiter','x',NULL)")
    db.commit()
    import pytest
    with pytest.raises(Exception):
        db.execute("UPDATE users SET person_id=? WHERE username='zweiter'", (pid,))
        db.commit()
    db.rollback()
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_profiles_migration.py -v`
Erwartung: Alle FAIL — `test_users_hat_neue_spalten` mit „Spalte person_id fehlt", die übrigen mit `OperationalError: no such column`.

- [ ] **Step 3: Spalten, Index und `app_meta` ergänzen**

In `app/routes/main.py`, `init_db()`, die bestehende `for sql in [...]`-Liste (`main.py:382`) um die neuen Spalten erweitern:

```python
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
```

- [ ] **Step 4: Sicherungskopie vor der erstmaligen Profil-Migration**

Unmittelbar **vor** der `for sql in [...]`-Liste einfügen. Das Schema selbst dient als Marke — kein zusätzliches Flag nötig:

```python
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
```

- [ ] **Step 5: Admin-Kaskade und Auto-Verknüpfung implementieren**

Direkt **nach** dem `CREATE TABLE ... app_meta` einfügen:

```python
    _ensure_admin(conn)
    _autolink_single_person(conn)
```

Und die beiden Helfer oberhalb von `init_db()` definieren:

```python
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
```

- [ ] **Step 6: Zwei Altlasten beheben, die die Kaskade sonst unterlaufen**

Beide fielen bei der Umsetzung auf und sind Voraussetzung dafür, dass Task 1 überhaupt funktioniert:

**a) Der Bootstrap legt sonst ein Geister-Konto an.** Der Block, der `admin` erzeugt, prüft bisher nur „existiert ein Benutzer namens `admin`?". Sobald das einzige Konto umbenannt wird — genau das bietet der Assistent in Task 7/8 an — würde beim nächsten Start ein **zweiter** `admin` mit dem Passwort aus `ADMIN_PASSWORD` entstehen, und die Kaskade würde diesen statt des echten Kontos befördern. Die Bedingung muss auf „Tabelle ist leer" lauten:

```python
    user = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    # Nur bei einer wirklich leeren Tabelle einen frischen 'admin' anlegen.
    # Sonst wuerde das Umbenennen des einzigen Benutzers bei jedem Neustart
    # einen zweiten, ueberfluessigen 'admin'-Account erzeugen.
    _user_count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()['c']
    if not user and _user_count == 0:
```

**b) `init_db` gibt die gecachte Verbindung nicht frei.** Am Ende der Funktion steht ein `conn.close()`, aber `g.db` bleibt gesetzt — ein zweiter Aufruf im selben App-Kontext scheitert dann an der geschlossenen Verbindung. Am Ende von `init_db()` ergänzen:

```python
    g.pop('db', None)
```

- [ ] **Step 7: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

- [ ] **Step 8: Migration gegen eine Kopie der Produktionsdatenbank prüfen**

Die echte Datenbank ist verschlüsselt und darf nicht angefasst werden. Deshalb gegen eine **Kopie** in einem Wegwerf-Verzeichnis:

```bash
mkdir -p /tmp/kst-migcheck && cp data_/kassenstuerzle.db /tmp/kst-migcheck/
DATABASE_PATH=/tmp/kst-migcheck/kassenstuerzle.db DISABLE_SCHEDULER=1 \
DB_ENCRYPTION_KEY="$(grep '^DB_ENCRYPTION_KEY=' .env | cut -d= -f2-)" \
  .venv-dev/bin/python -c "
import sys; sys.path.insert(0, '.')
from app import create_app
from app.routes import main as m
app = create_app()
c = m._open_db(m.DB_PATH)
c.row_factory = m.sqlite3.Row
print('Admins:', [dict(r) for r in c.execute(
    \"SELECT username, role, is_active, person_id FROM users\")])
print('Personen:', [r['name'] for r in c.execute('SELECT name FROM persons')])
c.close()"
rm -rf /tmp/kst-migcheck
```

Erwartung: genau ein Benutzer mit `role='admin'` und `is_active=1`; die Personenliste unverändert. Falls `sqlcipher3` lokal nicht installiert ist, schlägt das Öffnen der verschlüsselten Kopie fehl — dann diesen Schritt überspringen und im Bericht vermerken.

---

### Task 2: Rollen und Session-Härtung

**Files:**
- Modify: `app/routes/main.py` (`login_required`, neu `admin_required`, `login`, `change_password`, Backup-Endpunkte)
- Modify: `app/translations.py`
- Test: `tests/test_auth_roles.py`

**Interfaces:**
- Consumes: Schema aus Task 1
- Produces:
  - `login_required` verweigert deaktivierte Benutzer und Sessions mit veraltetem `session_epoch`; legt den Datensatz in `g.current_user` ab
  - `admin_required` — 403 (JSON) bzw. Redirect (HTML) für Nicht-Admins
  - `_bump_session_epoch(conn, user_id)` — beendet alle Sessions dieses Benutzers
  - Alle `/api/backup/*`-Endpunkte sind admin-pflichtig
  - `login` setzt `session['session_epoch']` und schreibt `last_login`

- [ ] **Step 1: Failing Test schreiben**

`tests/test_auth_roles.py`:

```python
from app.routes import main as m


def _als_member(db):
    db.execute("UPDATE users SET role='member' WHERE username='admin'")
    db.commit()


def _als_admin(db):
    db.execute("UPDATE users SET role='admin' WHERE username='admin'")
    db.commit()


def test_deaktivierter_benutzer_wird_abgewiesen(auth_client, db):
    db.execute("UPDATE users SET is_active=0 WHERE username='admin'")
    db.commit()
    res = auth_client.get('/overview')
    assert res.status_code == 302
    assert '/login' in res.headers['Location']
    db.execute("UPDATE users SET is_active=1 WHERE username='admin'")
    db.commit()


def test_veraltete_session_wird_abgewiesen(auth_client, db):
    """session_epoch ist der Hebel, um Cookie-Sessions serverseitig zu beenden."""
    db.execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE username='admin'")
    db.commit()
    res = auth_client.get('/overview')
    assert res.status_code == 302
    assert '/login' in res.headers['Location']


def test_login_setzt_last_login(client, db):
    db.execute("UPDATE users SET last_login=NULL WHERE username='admin'")
    db.commit()
    client.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
    assert db.execute("SELECT last_login FROM users WHERE username='admin'").fetchone()['last_login']


def test_member_darf_kein_backup_anlegen(auth_client, csrf, db):
    _als_member(db)
    res = auth_client.post('/api/backup/create', json={'label': ''},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 403
    _als_admin(db)


def test_member_darf_kein_backup_wiederherstellen(auth_client, csrf, db):
    _als_member(db)
    res = auth_client.post('/api/backup/restore', headers={'X-CSRF-Token': csrf})
    assert res.status_code == 403
    _als_admin(db)


def test_member_darf_backups_nicht_auflisten(auth_client, db):
    _als_member(db)
    assert auth_client.get('/api/backup/list').status_code == 403
    _als_admin(db)


def test_member_darf_weiterhin_buchungen_speichern(auth_client, csrf, db):
    """E6: alle duerfen alle Buchungen bearbeiten."""
    _als_member(db)
    res = auth_client.post('/api/expenses/save',
                           json={'v': 2, 'year': 2026, 'month': 9, 'rows': [], 'deleted_ids': []},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    _als_admin(db)


def test_admin_darf_backup_auflisten(auth_client, db):
    _als_admin(db)
    assert auth_client.get('/api/backup/list').status_code == 200
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_auth_roles.py -v`
Erwartung: Die Deaktivierungs-, Epoch- und `last_login`-Tests FAIL (keine Prüfung vorhanden), die Member-Tests FAIL mit 200 statt 403.

- [ ] **Step 3: `login_required` härten und `admin_required` ergänzen**

`app/routes/main.py`, `login_required` (`main.py:221`) ersetzen und `admin_required` direkt darunter ergänzen:

```python
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('main.login'))
        row = get_db().execute(
            'SELECT id, username, role, is_active, session_epoch, must_change_password, person_id'
            ' FROM users WHERE id=?', (session.get('user_id'),)).fetchone()
        # Deaktiviert, geloescht oder die Session wurde serverseitig beendet
        # (Passwortwechsel, Rollenaenderung, Umbenennung erhoehen session_epoch).
        if row is None or not row['is_active'] \
                or row['session_epoch'] != session.get('session_epoch', 0):
            lang = session.get('lang')
            session.clear()
            if lang:
                session['lang'] = lang
            return redirect(url_for('main.login'))
        g.current_user = row
        if row['must_change_password'] and request.endpoint not in (
                'main.force_password_change', 'main.logout', 'static'):
            return redirect(url_for('main.force_password_change'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Ergaenzt login_required um die Rollenpruefung. Serverseitig, nicht nur im UI."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        row = getattr(g, 'current_user', None)
        if row is None or row['role'] != 'admin':
            if request.path.startswith('/api/'):
                return jsonify(success=False, error=t('srv.admin_only')), 403
            flash(t('srv.admin_only'), 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function


def _bump_session_epoch(conn, user_id: int) -> None:
    """Beendet alle bestehenden Sessions dieses Benutzers sofort."""
    conn.execute('UPDATE users SET session_epoch = session_epoch + 1 WHERE id=?', (user_id,))
```

- [ ] **Step 4: Login um `session_epoch` und `last_login` erweitern**

In `login()` im Erfolgszweig, direkt nach `session['username'] = username`:

```python
            session['session_epoch'] = user['session_epoch'] or 0
            conn.execute("UPDATE users SET last_login=? WHERE id=?",
                         (datetime.now().isoformat(timespec='seconds'), user['id']))
            conn.commit()
```

Die `SELECT`-Abfrage darüber muss die neuen Spalten mitlesen:

```python
        user = conn.execute(
            "SELECT id, password_hash, is_active, session_epoch FROM users WHERE username = ?",
            (username,)).fetchone()
```

Und der Erfolgszweig darf nur greifen, wenn das Konto aktiv ist — ein deaktivierter Benutzer bekommt **dieselbe** unspezifische Meldung wie bei falschem Passwort:

```python
        if user and user['is_active'] and check_password_hash(user['password_hash'], password):
```

- [ ] **Step 5: Passwortwechsel beendet andere Sessions**

In `change_password`, nach dem `UPDATE users SET password_hash=...`:

```python
    _bump_session_epoch(conn, session['user_id'])
    conn.execute("UPDATE users SET must_change_password=0 WHERE id=?", (session['user_id'],))
    conn.commit()
    # Die eigene Session bleibt gueltig - Epoch in der Session nachziehen.
    session['session_epoch'] = conn.execute(
        'SELECT session_epoch FROM users WHERE id=?', (session['user_id'],)).fetchone()['session_epoch']
```

- [ ] **Step 6: Backup-Endpunkte auf `admin_required` umstellen**

Bei `api_backup_create`, `api_backup_list`, `api_backup_download`, `api_backup_restore` und `api_backup_delete` jeweils `@login_required` durch `@admin_required` ersetzen. Sonst nichts ändern.

- [ ] **Step 7: Übersetzung ergänzen**

`app/translations.py`, deutscher Block bei den `srv.`-Schlüsseln:

```python
        'srv.admin_only': 'Nur Administratoren dürfen das.',
```

Englischer Block:

```python
        'srv.admin_only': 'Administrators only.',
```

- [ ] **Step 8: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

---

### Task 3: Login-Sperre pro Benutzername

**Files:**
- Modify: `app/routes/main.py` (`_login_attempts`, `login`)
- Test: `tests/test_login_lockout.py`

**Interfaces:**
- Consumes: nichts aus Task 1/2
- Produces: Fehlversuche werden pro `username.lower()` gezählt; eine großzügige IP-Obergrenze bleibt als Rückfallnetz

- [ ] **Step 1: Failing Test schreiben**

`tests/test_login_lockout.py`:

```python
from app.routes import main as m


def _reset():
    m._login_attempts.clear()
    m._ip_attempts.clear()


def test_fehlversuche_sperren_nur_den_betroffenen_benutzer(client, db):
    """Im gemeinsamen WLAN darf ein Nutzer mit Tippfehlern niemanden
    anderen aussperren - frueher war die Sperre IP-basiert."""
    _reset()
    db.execute("INSERT INTO users (username, password_hash, role) VALUES ('bob', 'x', 'member')")
    db.commit()
    for _ in range(m._MAX_LOGIN_ATTEMPTS):
        client.post('/login', data={'username': 'bob', 'password': 'falsch'})

    res = client.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
    assert res.status_code == 302, 'admin wurde durch bobs Fehlversuche mitgesperrt'
    _reset()


def test_gesperrter_benutzer_wird_abgewiesen(client):
    _reset()
    for _ in range(m._MAX_LOGIN_ATTEMPTS):
        client.post('/login', data={'username': 'admin', 'password': 'falsch'})
    res = client.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
    assert res.status_code == 200, 'gesperrtes Konto haette nicht anmelden duerfen'
    _reset()


def test_erfolgreicher_login_setzt_zaehler_zurueck(client):
    _reset()
    client.post('/login', data={'username': 'admin', 'password': 'falsch'})
    client.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
    assert m._login_attempts['admin']['count'] == 0
    _reset()


def test_meldung_verraet_nicht_ob_konto_existiert(client):
    """Account-Enumeration verhindern: gleiche Meldung fuer beide Faelle."""
    _reset()
    a = client.post('/login', data={'username': 'gibtesnicht', 'password': 'x'})
    _reset()
    b = client.post('/login', data={'username': 'admin', 'password': 'falsch'})
    _reset()
    assert a.get_data(as_text=True).count('Ungültiger') == b.get_data(as_text=True).count('Ungültiger')
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_login_lockout.py -v`
Erwartung: `AttributeError: module has no attribute '_ip_attempts'` und der Mitsperr-Test FAIL.

- [ ] **Step 3: Sperre umstellen**

`app/routes/main.py`, bei `_login_attempts` (`main.py:718`):

```python
# Sperre pro Benutzername. Frueher pro IP - im gemeinsamen WLAN sperrte damit
# ein Nutzer mit Tippfehlern alle anderen Haushaltsmitglieder aus.
_login_attempts: dict = _defaultdict(lambda: {'count': 0, 'locked_until': 0})
# Grosszuegiges Rueckfallnetz gegen breites Durchprobieren von einer Quelle.
_ip_attempts: dict = _defaultdict(lambda: {'count': 0, 'locked_until': 0})
_MAX_IP_ATTEMPTS = 30
```

In `login()` den Sperrblock ersetzen:

```python
def login():
    ip = request.remote_addr or 'unknown'
    username = (request.form.get('username', '') or '').strip()
    key = username.lower()
    state = _login_attempts[key]
    ip_state = _ip_attempts[ip]

    gesperrt_bis = max(state['locked_until'], ip_state['locked_until'])
    if gesperrt_bis > _time.time():
        remaining = int(gesperrt_bis - _time.time())
        flash(t('login.locked', seconds=remaining), 'danger')
        return render_template('login.html')

    if request.method == 'POST':
        password = request.form.get('password', '')
        ...
```

Im Erfolgszweig beide Zähler zurücksetzen:

```python
            _login_attempts[key] = {'count': 0, 'locked_until': 0}
            _ip_attempts[ip] = {'count': 0, 'locked_until': 0}
```

Im Fehlerzweig beide hochzählen:

```python
        else:
            state['count'] += 1
            ip_state['count'] += 1
            if ip_state['count'] >= _MAX_IP_ATTEMPTS:
                ip_state['locked_until'] = _time.time() + _LOGIN_LOCKOUT_SECONDS
                ip_state['count'] = 0
            if state['count'] >= _MAX_LOGIN_ATTEMPTS:
                state['locked_until'] = _time.time() + _LOGIN_LOCKOUT_SECONDS
                state['count'] = 0
                flash(t('login.locked_now', seconds=_LOGIN_LOCKOUT_SECONDS), 'danger')
            else:
                remaining_attempts = _MAX_LOGIN_ATTEMPTS - state['count']
                flash(t('login.invalid', remaining=remaining_attempts), 'danger')
```

`username` wird jetzt oberhalb des `if request.method == 'POST'` gelesen — die doppelte Zuweisung im POST-Zweig entfernen.

- [ ] **Step 4: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

---

### Task 4: Erzwungener Passwortwechsel

**Files:**
- Create: `app/templates/force_password_change.html`
- Modify: `app/routes/main.py` (neue Route `force_password_change`)
- Modify: `app/translations.py`
- Test: `tests/test_auth_roles.py` (erweitern)

**Interfaces:**
- Consumes: `must_change_password` aus Task 1, die Weiterleitung in `login_required` aus Task 2
- Produces: `GET/POST /password/aendern` (`endpoint` = `main.force_password_change`)

- [ ] **Step 1: Failing Test schreiben**

An `tests/test_auth_roles.py` anhängen:

```python
def test_erzwungener_wechsel_leitet_um(auth_client, db):
    db.execute("UPDATE users SET must_change_password=1 WHERE username='admin'")
    db.commit()
    res = auth_client.get('/overview')
    assert res.status_code == 302
    assert '/password/aendern' in res.headers['Location']
    db.execute("UPDATE users SET must_change_password=0 WHERE username='admin'")
    db.commit()


def test_wechselseite_selbst_ist_erreichbar(auth_client, db):
    db.execute("UPDATE users SET must_change_password=1 WHERE username='admin'")
    db.commit()
    assert auth_client.get('/password/aendern').status_code == 200
    db.execute("UPDATE users SET must_change_password=0 WHERE username='admin'")
    db.commit()


def test_wechsel_setzt_flag_zurueck(auth_client, csrf, db):
    db.execute("UPDATE users SET must_change_password=1 WHERE username='admin'")
    db.commit()
    res = auth_client.post('/password/aendern',
                           data={'new_password': 'neues-passwort-123',
                                 'new_password2': 'neues-passwort-123',
                                 'csrf_token': csrf})
    assert res.status_code == 302
    row = db.execute("SELECT must_change_password FROM users WHERE username='admin'").fetchone()
    assert row['must_change_password'] == 0
    # Fuer die uebrigen Tests das urspruengliche Passwort wiederherstellen
    from werkzeug.security import generate_password_hash
    db.execute("UPDATE users SET password_hash=? WHERE username='admin'",
               (generate_password_hash('test-password-123'),))
    db.commit()


def test_zu_kurzes_passwort_wird_abgelehnt(auth_client, csrf, db):
    db.execute("UPDATE users SET must_change_password=1 WHERE username='admin'")
    db.commit()
    res = auth_client.post('/password/aendern',
                           data={'new_password': 'kurz', 'new_password2': 'kurz',
                                 'csrf_token': csrf})
    assert res.status_code == 200
    assert db.execute("SELECT must_change_password FROM users WHERE username='admin'"
                      ).fetchone()['must_change_password'] == 1
    db.execute("UPDATE users SET must_change_password=0 WHERE username='admin'")
    db.commit()
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_auth_roles.py -v -k wechsel`
Erwartung: 404 statt 302/200 — die Route existiert noch nicht.

- [ ] **Step 3: Route ergänzen**

In `app/routes/main.py` nach `change_password`:

```python
@main_bp.route('/password/aendern', methods=['GET', 'POST'])
@login_required
def force_password_change():
    """Eigenstaendige Seite fuer den erzwungenen Wechsel eines Initialpassworts.
    login_required leitet hierher um, solange must_change_password gesetzt ist."""
    if request.method == 'GET':
        return render_template('force_password_change.html')

    neu  = request.form.get('new_password') or ''
    neu2 = request.form.get('new_password2') or ''
    if neu != neu2:
        flash(t('settings.pw_mismatch'), 'danger')
        return render_template('force_password_change.html')
    if len(neu) < 8:
        flash(t('srv.new_password_too_short'), 'danger')
        return render_template('force_password_change.html')

    conn = get_db()
    conn.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
                 (generate_password_hash(neu), session['user_id']))
    _bump_session_epoch(conn, session['user_id'])
    conn.commit()
    session['session_epoch'] = conn.execute(
        'SELECT session_epoch FROM users WHERE id=?',
        (session['user_id'],)).fetchone()['session_epoch']
    flash(t('settings.pw_changed'), 'success')
    return redirect(url_for('main.index'))
```

- [ ] **Step 4: Template anlegen**

`app/templates/force_password_change.html`:

```html
{% extends 'base.html' %}
{% block title %}{{ t('settings.change_password') }} – {{ t('nav.brand') }}{% endblock %}
{% block content %}
<div class="row justify-content-center">
  <div class="col-12 col-md-6 col-lg-5">
    <div class="card shadow-sm">
      <div class="card-header bg-secondary text-white fw-semibold">
        <i class="bi bi-shield-lock me-1"></i>{{ t('settings.change_password') }}
      </div>
      <div class="card-body">
        <p class="text-muted small">{{ t('settings.pw_forced_hint') }}</p>
        <form method="post" action="{{ url_for('main.force_password_change') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <div class="mb-3">
            <label class="form-label">{{ t('settings.new_password') }}
              <span class="text-muted">{{ t('settings.new_password_hint') }}</span></label>
            <input type="password" name="new_password" class="form-control" required>
          </div>
          <div class="mb-3">
            <label class="form-label">{{ t('settings.new_password_repeat') }}</label>
            <input type="password" name="new_password2" class="form-control" required>
          </div>
          <button class="btn btn-success w-100" type="submit">
            <i class="bi bi-check2 me-1"></i>{{ t('settings.save_password') }}
          </button>
        </form>
      </div>
    </div>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Übersetzung ergänzen**

Deutscher Block:

```python
        'settings.pw_forced_hint': 'Dein Konto wurde mit einem Startpasswort angelegt. Bitte vergib jetzt ein eigenes.',
```

Englischer Block:

```python
        'settings.pw_forced_hint': 'Your account was created with an initial password. Please set your own now.',
```

- [ ] **Step 6: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

---

### Task 5: Benutzerverwaltung — API und Schutzgeländer

**Files:**
- Modify: `app/routes/main.py` (fünf neue Routen, Personenlösch-Sperre in `save_persons`)
- Modify: `app/translations.py`
- Test: `tests/test_users_api.py`

**Interfaces:**
- Consumes: `admin_required`, `_bump_session_epoch` aus Task 2
- Produces:
  - `GET /api/users` → `{success, users: [{id, username, role, is_active, person_id, person_name, last_login, created_at}]}`
  - `POST /api/users` mit `{username, role, person_id}` → `{success, id, password}` (Initialpasswort **einmalig**)
  - `POST /api/users/<id>` mit `{role?, person_id?, is_active?}` → `{success}`
  - `POST /api/users/<id>/reset-password` → `{success, password}` (einmalig)
  - `DELETE /api/users/<id>` → `{success}`
  - `save_persons` verweigert das Löschen einer Person, die mit einem aktiven Benutzer verknüpft ist, und meldet sie in `blocked`

- [ ] **Step 1: Failing Test schreiben**

`tests/test_users_api.py`:

```python
def _anlegen(client, csrf, **felder):
    body = {'username': 'bob', 'role': 'member', 'person_id': None}
    body.update(felder)
    return client.post('/api/users', json=body, headers={'X-CSRF-Token': csrf})


def test_liste_nur_fuer_admin(auth_client, db):
    db.execute("UPDATE users SET role='member' WHERE username='admin'")
    db.commit()
    assert auth_client.get('/api/users').status_code == 403
    db.execute("UPDATE users SET role='admin' WHERE username='admin'")
    db.commit()
    assert auth_client.get('/api/users').status_code == 200


def test_anlegen_liefert_initialpasswort_und_erzwingt_wechsel(auth_client, csrf, db):
    res = _anlegen(auth_client, csrf)
    assert res.status_code == 200
    body = res.get_json()
    assert body['success'] is True
    assert len(body['password']) >= 10
    row = db.execute("SELECT must_change_password, role FROM users WHERE username='bob'").fetchone()
    assert row['must_change_password'] == 1
    assert row['role'] == 'member'


def test_doppelter_benutzername_wird_abgelehnt(auth_client, csrf):
    _anlegen(auth_client, csrf, username='carla')
    res = _anlegen(auth_client, csrf, username='carla')
    assert res.status_code == 409
    assert res.get_json()['success'] is False


def test_eigene_rolle_nicht_aenderbar(auth_client, csrf, db):
    eigene = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()['id']
    res = auth_client.post(f'/api/users/{eigene}', json={'role': 'member'},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 400
    assert db.execute('SELECT role FROM users WHERE id=?', (eigene,)).fetchone()['role'] == 'admin'


def test_letzter_admin_nicht_deaktivierbar(auth_client, csrf, db):
    """Ein zweiter Admin wird angelegt, degradiert - dann darf der letzte
    verbliebene Admin sich nicht selbst aus dem System nehmen."""
    res = _anlegen(auth_client, csrf, username='zweitadmin', role='admin')
    neue_id = res.get_json()['id']
    auth_client.post(f'/api/users/{neue_id}', json={'is_active': 0},
                     headers={'X-CSRF-Token': csrf})
    eigene = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()['id']
    res = auth_client.post(f'/api/users/{eigene}', json={'is_active': 0},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 400
    assert db.execute('SELECT is_active FROM users WHERE id=?', (eigene,)).fetchone()['is_active'] == 1


def test_deaktivierung_beendet_sessions(auth_client, csrf, db):
    res = _anlegen(auth_client, csrf, username='dora')
    neue_id = res.get_json()['id']
    vorher = db.execute('SELECT session_epoch FROM users WHERE id=?', (neue_id,)).fetchone()['session_epoch']
    auth_client.post(f'/api/users/{neue_id}', json={'is_active': 0}, headers={'X-CSRF-Token': csrf})
    nachher = db.execute('SELECT session_epoch FROM users WHERE id=?', (neue_id,)).fetchone()['session_epoch']
    assert nachher > vorher


def test_passwort_zuruecksetzen(auth_client, csrf, db):
    res = _anlegen(auth_client, csrf, username='erik')
    neue_id = res.get_json()['id']
    res = auth_client.post(f'/api/users/{neue_id}/reset-password', headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    assert len(res.get_json()['password']) >= 10
    assert db.execute('SELECT must_change_password FROM users WHERE id=?',
                      (neue_id,)).fetchone()['must_change_password'] == 1


def test_loeschen_und_letzter_admin_geschuetzt(auth_client, csrf, db):
    res = _anlegen(auth_client, csrf, username='frida')
    neue_id = res.get_json()['id']
    assert auth_client.delete(f'/api/users/{neue_id}', headers={'X-CSRF-Token': csrf}).status_code == 200
    assert db.execute('SELECT COUNT(*) c FROM users WHERE id=?', (neue_id,)).fetchone()['c'] == 0

    eigene = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()['id']
    assert auth_client.delete(f'/api/users/{eigene}', headers={'X-CSRF-Token': csrf}).status_code == 400


def test_verknuepfte_person_nicht_loeschbar(auth_client, csrf, db):
    cur = db.execute("INSERT INTO persons (name) VALUES ('Greta')")
    pid = cur.lastrowid
    db.commit()
    _anlegen(auth_client, csrf, username='greta', person_id=pid)

    res = auth_client.post('/api/persons/save',
                           json={'v': 2, 'rows': [], 'deleted_ids': [pid]},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    assert res.get_json()['blocked'] == ['Greta']
    assert db.execute('SELECT COUNT(*) c FROM persons WHERE id=?', (pid,)).fetchone()['c'] == 1
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_users_api.py -v`
Erwartung: alle FAIL mit 404 (Routen fehlen) bzw. `KeyError: 'blocked'`.

- [ ] **Step 3: Benutzer-API implementieren**

In `app/routes/main.py` nach `force_password_change` einfügen:

```python
# ── Benutzerverwaltung (nur Admin) ────────────────────────────────────────────
def _neues_initialpasswort() -> str:
    return _secrets.token_urlsafe(9)


def _anzahl_aktiver_admins(conn, ausser_id: int | None = None) -> int:
    if ausser_id is None:
        return conn.execute(
            "SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1").fetchone()['c']
    return conn.execute(
        "SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1 AND id<>?",
        (ausser_id,)).fetchone()['c']


@main_bp.route('/api/users')
@admin_required
def api_users_list():
    conn = get_db()
    rows = conn.execute(
        'SELECT u.id, u.username, u.role, u.is_active, u.person_id, u.last_login,'
        ' u.created_at, p.name AS person_name'
        ' FROM users u LEFT JOIN persons p ON p.id = u.person_id ORDER BY u.id').fetchall()
    conn.close()
    return jsonify(success=True, users=[dict(r) for r in rows])


@main_bp.route('/api/users', methods=['POST'])
@admin_required
def api_users_create():
    d = request.get_json(silent=True) or {}
    username = (d.get('username') or '').strip()
    role = d.get('role') if d.get('role') in ('admin', 'member') else 'member'
    person_id = int(d['person_id']) if d.get('person_id') else None
    if not username:
        return jsonify(success=False, error=t('srv.missing_data')), 400

    passwort = _neues_initialpasswort()
    conn = get_db()
    try:
        cur = conn.execute(
            'INSERT INTO users (username, password_hash, role, is_active,'
            ' must_change_password, created_at, person_id, session_epoch)'
            ' VALUES (?,?,?,1,1,?,?,0)',
            (username, generate_password_hash(passwort), role,
             datetime.now().isoformat(timespec='seconds'), person_id))
    except sqlite3.IntegrityError:
        # Benutzername oder Personen-Verknuepfung bereits vergeben
        return jsonify(success=False, error=t('srv.username_taken')), 409
    conn.commit()
    # Das Initialpasswort wird genau einmal zurueckgegeben (Entscheidung P1).
    return jsonify(success=True, id=cur.lastrowid, password=passwort)


@main_bp.route('/api/users/<int:user_id>', methods=['POST'])
@admin_required
def api_users_update(user_id):
    d = request.get_json(silent=True) or {}
    conn = get_db()
    row = conn.execute('SELECT id, role, is_active FROM users WHERE id=?', (user_id,)).fetchone()
    if row is None:
        return jsonify(success=False, error=t('srv.user_not_found')), 404

    eigene = user_id == session.get('user_id')
    neue_rolle = d.get('role') if d.get('role') in ('admin', 'member') else None
    neu_aktiv = None if d.get('is_active') is None else (1 if d.get('is_active') else 0)

    if eigene and (neue_rolle is not None or neu_aktiv == 0):
        return jsonify(success=False, error=t('srv.cannot_change_own_role')), 400

    # Der letzte aktive Admin darf weder degradiert noch deaktiviert werden.
    entzieht_admin = (neue_rolle == 'member' and row['role'] == 'admin') or \
                     (neu_aktiv == 0 and row['role'] == 'admin')
    if entzieht_admin and _anzahl_aktiver_admins(conn, ausser_id=user_id) == 0:
        return jsonify(success=False, error=t('srv.last_admin')), 400

    felder, werte = [], []
    if neue_rolle is not None:
        felder.append('role=?'); werte.append(neue_rolle)
    if neu_aktiv is not None:
        felder.append('is_active=?'); werte.append(neu_aktiv)
    if 'person_id' in d:
        felder.append('person_id=?')
        werte.append(int(d['person_id']) if d['person_id'] else None)
    if not felder:
        return jsonify(success=True)

    werte.append(user_id)
    try:
        conn.execute(f"UPDATE users SET {', '.join(felder)} WHERE id=?", werte)
    except sqlite3.IntegrityError:
        return jsonify(success=False, error=t('srv.person_already_linked')), 409
    # Rollen- und Statuswechsel beenden bestehende Sessions dieses Benutzers.
    _bump_session_epoch(conn, user_id)
    conn.commit()
    return jsonify(success=True)


@main_bp.route('/api/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def api_users_reset_password(user_id):
    conn = get_db()
    if conn.execute('SELECT id FROM users WHERE id=?', (user_id,)).fetchone() is None:
        return jsonify(success=False, error=t('srv.user_not_found')), 404
    passwort = _neues_initialpasswort()
    conn.execute('UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?',
                 (generate_password_hash(passwort), user_id))
    _bump_session_epoch(conn, user_id)
    conn.commit()
    return jsonify(success=True, password=passwort)


@main_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
@admin_required
def api_users_delete(user_id):
    conn = get_db()
    row = conn.execute('SELECT role FROM users WHERE id=?', (user_id,)).fetchone()
    if row is None:
        return jsonify(success=False, error=t('srv.user_not_found')), 404
    if user_id == session.get('user_id'):
        return jsonify(success=False, error=t('srv.cannot_delete_self')), 400
    if row['role'] == 'admin' and _anzahl_aktiver_admins(conn, ausser_id=user_id) == 0:
        return jsonify(success=False, error=t('srv.last_admin')), 400
    conn.execute('DELETE FROM users WHERE id=?', (user_id,))
    conn.commit()
    return jsonify(success=True)
```

- [ ] **Step 4: Personenlösch-Sperre in `save_persons`**

In `save_persons` die `deleted_ids`-Schleife ersetzen:

```python
    blocked = []
    for raw in (d.get('deleted_ids') or []):
        try:
            did = int(raw)
        except (TypeError, ValueError):
            continue
        # Eine Person, an der ein aktiver Login haengt, darf nicht verschwinden -
        # sonst zeigt das Konto ins Leere.
        verknuepft = conn.execute(
            'SELECT u.username, p.name FROM users u JOIN persons p ON p.id = u.person_id'
            ' WHERE u.person_id=? AND u.is_active=1', (did,)).fetchone()
        if verknuepft:
            blocked.append(verknuepft['name'])
            continue
        conn.execute('DELETE FROM persons WHERE id=?', (did,))
```

Und `blocked` in die Antwort aufnehmen:

```python
    return jsonify({'success': True, 'persons': result, 'id_map': id_map,
                    'activity_version': activity_version, 'conflicts': conflicts,
                    'blocked': blocked})
```

- [ ] **Step 5: Übersetzungen ergänzen**

Deutscher Block:

```python
        'srv.username_taken': 'Dieser Benutzername ist bereits vergeben.',
        'srv.user_not_found': 'Benutzer nicht gefunden.',
        'srv.cannot_change_own_role': 'Die eigene Rolle lässt sich nicht ändern.',
        'srv.cannot_delete_self': 'Das eigene Konto lässt sich nicht löschen.',
        'srv.last_admin': 'Das ist der letzte Administrator – er muss aktiv bleiben.',
        'srv.person_already_linked': 'Diese Person ist bereits mit einem Konto verknüpft.',
        'settings.person_blocked': 'Nicht gelöscht, es hängt ein Login daran: {names}',
```

Englischer Block:

```python
        'srv.username_taken': 'That username is already taken.',
        'srv.user_not_found': 'User not found.',
        'srv.cannot_change_own_role': 'You cannot change your own role.',
        'srv.cannot_delete_self': 'You cannot delete your own account.',
        'srv.last_admin': 'This is the last administrator – they must stay active.',
        'srv.person_already_linked': 'That person is already linked to an account.',
        'settings.person_blocked': 'Not deleted, an account is linked to it: {names}',
```

- [ ] **Step 6: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

---

### Task 6: Benutzerverwaltung — Oberfläche

**Files:**
- Modify: `app/routes/main.py` (`settings`-Route übergibt `current_role`)
- Modify: `app/templates/settings.html` (neue Karte, nur für Admins)
- Modify: `app/translations.py`

**Interfaces:**
- Consumes: die fünf Routen aus Task 5, `g.current_user` aus Task 2
- Produces: keine (Blattknoten)

- [ ] **Step 1: Rolle an das Template übergeben**

In `settings()` vor dem `render_template`:

```python
    aktuelle_rolle = getattr(g, 'current_user', None)
    aktuelle_rolle = aktuelle_rolle['role'] if aktuelle_rolle else 'member'
```

und im `render_template`-Aufruf ergänzen: `current_role=aktuelle_rolle,`

- [ ] **Step 2: Karte im Template ergänzen**

In `app/templates/settings.html` **vor** der Karte „Datenbank Backup" einfügen. Die Karte wird nur für Admins gerendert — die eigentliche Absicherung sitzt aber serverseitig in `admin_required`:

```html
  {% if current_role == 'admin' %}
  <!-- Benutzer -->
  <div class="col-12">
    <div class="card shadow-sm border-dark">
      <div class="card-header bg-dark text-white fw-semibold d-flex flex-wrap justify-content-between align-items-center gap-2">
        <span><i class="bi bi-person-badge me-1"></i>{{ t('settings.users') }}</span>
        <button class="btn btn-sm btn-light" id="add-user-btn">
          <i class="bi bi-plus-circle me-1"></i>{{ t('settings.user_add') }}
        </button>
      </div>
      <div class="card-body">
        <div class="table-responsive">
          <table class="table table-sm align-middle mb-0">
            <thead class="table-light">
              <tr>
                <th>{{ t('settings.user_name') }}</th>
                <th>{{ t('settings.user_person') }}</th>
                <th>{{ t('settings.user_role') }}</th>
                <th>{{ t('settings.user_active') }}</th>
                <th>{{ t('settings.user_last_login') }}</th>
                <th class="text-end">{{ t('settings.backup_action') }}</th>
              </tr>
            </thead>
            <tbody id="users-tbody"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
  {% endif %}
```

- [ ] **Step 3: JavaScript ergänzen**

Am Ende des `extra_script`-Blocks. **Alle Benutzer- und Personennamen werden über `textContent` gesetzt, nie über `innerHTML`** — siehe Global Constraints:

```javascript
{% if current_role == 'admin' %}
const PERSONEN_FUER_USER = {{ persons | tojson }};

function zelle(text) {
  const td = document.createElement('td');
  td.textContent = text == null ? '—' : String(text);
  return td;
}

function benutzerZeile(u) {
  const tr = document.createElement('tr');
  tr.appendChild(zelle(u.username));
  tr.appendChild(zelle(u.person_name));

  const tdRolle = document.createElement('td');
  const sel = document.createElement('select');
  sel.className = 'form-select form-select-sm';
  [['member', t('settings.role_member')], ['admin', t('settings.role_admin')]].forEach(([wert, label]) => {
    const o = document.createElement('option');
    o.value = wert; o.textContent = label;
    if (u.role === wert) o.selected = true;
    sel.appendChild(o);
  });
  sel.addEventListener('change', () => benutzerAendern(u.id, {role: sel.value}));
  tdRolle.appendChild(sel);
  tr.appendChild(tdRolle);

  const tdAktiv = document.createElement('td');
  const cb = document.createElement('input');
  cb.type = 'checkbox'; cb.className = 'form-check-input'; cb.checked = !!u.is_active;
  cb.addEventListener('change', () => benutzerAendern(u.id, {is_active: cb.checked ? 1 : 0}));
  tdAktiv.appendChild(cb);
  tr.appendChild(tdAktiv);

  tr.appendChild(zelle(u.last_login));

  const tdAktion = document.createElement('td');
  tdAktion.className = 'text-end text-nowrap';
  const reset = document.createElement('button');
  reset.className = 'btn btn-sm btn-outline-secondary me-1';
  reset.textContent = t('settings.user_reset_pw');
  reset.addEventListener('click', () => passwortZuruecksetzen(u.id, u.username));
  const del = document.createElement('button');
  del.className = 'btn btn-sm btn-outline-danger';
  del.innerHTML = '<i class="bi bi-trash"></i>';
  del.addEventListener('click', () => benutzerLoeschen(u.id, u.username));
  tdAktion.appendChild(reset); tdAktion.appendChild(del);
  tr.appendChild(tdAktion);
  return tr;
}

async function benutzerLaden() {
  const d = await (await fetch('/api/users')).json();
  const tb = document.getElementById('users-tbody');
  tb.innerHTML = '';
  (d.users || []).forEach(u => tb.appendChild(benutzerZeile(u)));
}

async function benutzerAendern(id, felder) {
  const d = await (await fetch(`/api/users/${id}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(felder)
  })).json();
  showToast(d.success ? t('settings.user_saved') : d.error, d.success ? 'success' : 'danger');
  benutzerLaden();
}

async function passwortZuruecksetzen(id, name) {
  if (!confirm(t('settings.user_reset_confirm', {name}))) return;
  const d = await (await fetch(`/api/users/${id}/reset-password`, {method: 'POST'})).json();
  if (d.success) zeigeInitialpasswort(name, d.password);
  else showToast(d.error, 'danger');
}

async function benutzerLoeschen(id, name) {
  if (!confirm(t('settings.user_delete_confirm', {name}))) return;
  const d = await (await fetch(`/api/users/${id}`, {method: 'DELETE'})).json();
  showToast(d.success ? t('settings.user_deleted') : d.error, d.success ? 'success' : 'danger');
  benutzerLaden();
}

/** Zeigt das Initialpasswort genau einmal an (Entscheidung P1). */
function zeigeInitialpasswort(name, passwort) {
  const box = document.createElement('div');
  box.className = 'alert alert-warning mt-3';
  const titel = document.createElement('div');
  titel.className = 'fw-semibold';
  titel.textContent = t('settings.user_password_once', {name});
  const code = document.createElement('code');
  code.className = 'fs-5 d-block my-2';
  code.textContent = passwort;
  box.appendChild(titel); box.appendChild(code);
  document.getElementById('users-tbody').closest('.card-body').appendChild(box);
}

document.getElementById('add-user-btn').addEventListener('click', async () => {
  const name = prompt(t('settings.user_name_prompt'));
  if (!name) return;
  const d = await (await fetch('/api/users', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username: name.trim(), role: 'member', person_id: null})
  })).json();
  if (d.success) { zeigeInitialpasswort(name.trim(), d.password); benutzerLaden(); }
  else showToast(d.error, 'danger');
});

benutzerLaden();
{% endif %}
```

- [ ] **Step 4: Personen-Handler um `blocked` erweitern**

Im `save-persons-btn`-Handler die Erfolgsmeldung ergänzen:

```javascript
    if (d.blocked && d.blocked.length) {
      showToast(t('settings.person_blocked', {names: d.blocked.join(', ')}), 'warning');
    } else if (d.conflicts && d.conflicts.length) {
```

- [ ] **Step 5: Übersetzungen ergänzen**

Deutscher Block:

```python
        'settings.users': 'Benutzer',
        'settings.user_add': 'Benutzer anlegen',
        'settings.user_name': 'Benutzername',
        'settings.user_person': 'Person',
        'settings.user_role': 'Rolle',
        'settings.user_active': 'Aktiv',
        'settings.user_last_login': 'Zuletzt angemeldet',
        'settings.user_reset_pw': 'Passwort zurücksetzen',
        'settings.role_admin': 'Administrator',
        'settings.role_member': 'Mitglied',
        'settings.user_saved': 'Benutzer gespeichert ✓',
        'settings.user_deleted': 'Benutzer gelöscht',
        'settings.user_name_prompt': 'Benutzername für das neue Konto:',
        'settings.user_delete_confirm': 'Konto „{name}" wirklich löschen?',
        'settings.user_reset_confirm': 'Neues Startpasswort für „{name}" erzeugen?',
        'settings.user_password_once': 'Startpasswort für „{name}" – wird nur jetzt angezeigt:',
```

Englischer Block:

```python
        'settings.users': 'Users',
        'settings.user_add': 'Add user',
        'settings.user_name': 'Username',
        'settings.user_person': 'Person',
        'settings.user_role': 'Role',
        'settings.user_active': 'Active',
        'settings.user_last_login': 'Last login',
        'settings.user_reset_pw': 'Reset password',
        'settings.role_admin': 'Administrator',
        'settings.role_member': 'Member',
        'settings.user_saved': 'User saved ✓',
        'settings.user_deleted': 'User deleted',
        'settings.user_name_prompt': 'Username for the new account:',
        'settings.user_delete_confirm': 'Really delete account "{name}"?',
        'settings.user_reset_confirm': 'Generate a new initial password for "{name}"?',
        'settings.user_password_once': 'Initial password for "{name}" – shown only now:',
```

- [ ] **Step 6: Suite prüfen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS (die Karte ist reines Frontend; die Absicherung ist durch Task 5 getestet).

---

### Task 7: Einrichtungsassistent — Backend

**Files:**
- Modify: `app/routes/main.py` (Namensableitung, Vorschlagsendpunkt, Abschluss-Flag)
- Test: `tests/test_setup_wizard.py`

**Interfaces:**
- Consumes: `app_meta` aus Task 1, `admin_required` aus Task 2
- Produces:
  - `_benutzername_aus_person(name, vergeben) -> str` — Kleinschreibung, Umlaut-Transliteration, Kollisionszähler
  - `GET /api/setup/profiles` → `{success, done: bool, me: {person_id, username}, persons: [{id, name}], suggestions: [{person_id, person_name, username}]}`
  - `POST /api/setup/profiles/self` mit `{person_id, new_username?}` → `{success, renamed: bool}` — Schritt 1 des Assistenten („Wer bist du?")
  - `POST /api/setup/profiles/complete` → `{success}` setzt `app_meta.profiles_setup_done = 1`

- [ ] **Step 1: Failing Test schreiben**

`tests/test_setup_wizard.py`:

```python
from app.routes import main as m


def test_namensableitung():
    assert m._benutzername_aus_person('Bob', set()) == 'bob'
    assert m._benutzername_aus_person('Jörg Müller', set()) == 'joerg.mueller'
    assert m._benutzername_aus_person('Straße', set()) == 'strasse'
    assert m._benutzername_aus_person('Anna-Lena', set()) == 'anna-lena'
    assert m._benutzername_aus_person('Bob', {'bob'}) == 'bob2'
    assert m._benutzername_aus_person('Bob', {'bob', 'bob2'}) == 'bob3'
    assert m._benutzername_aus_person('!!!', set()) == 'benutzer'


def test_vorschlaege_nur_fuer_unverknuepfte_personen(auth_client, db):
    db.execute("INSERT INTO persons (name) VALUES ('Hanna')")
    db.execute("INSERT INTO persons (name) VALUES ('Ingo')")
    db.commit()
    hanna = db.execute("SELECT id FROM persons WHERE name='Hanna'").fetchone()['id']
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (hanna,))
    db.commit()

    d = auth_client.get('/api/setup/profiles').get_json()
    namen = [v['person_name'] for v in d['suggestions']]
    assert 'Ingo' in namen
    assert 'Hanna' not in namen, 'bereits verknuepfte Person darf nicht vorgeschlagen werden'


def test_vorschlag_kollidiert_nicht_mit_bestehendem_konto(auth_client, db):
    db.execute("INSERT INTO persons (name) VALUES ('Admin')")
    db.commit()
    d = auth_client.get('/api/setup/profiles').get_json()
    vorschlag = [v for v in d['suggestions'] if v['person_name'] == 'Admin'][0]
    assert vorschlag['username'] != 'admin'


def test_eigene_person_zuordnen(auth_client, csrf, db):
    """Schritt 1 des Assistenten: der Admin sagt, welche Person er selbst ist."""
    cur = db.execute("INSERT INTO persons (name) VALUES ('Jana')")
    pid = cur.lastrowid
    db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
    db.commit()
    res = auth_client.post('/api/setup/profiles/self', json={'person_id': pid},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    assert db.execute("SELECT person_id FROM users WHERE username='admin'"
                      ).fetchone()['person_id'] == pid


def test_eigene_umbenennung_beendet_die_session(auth_client, csrf, db):
    """Nach der Umbenennung traegt die Session einen veralteten Namen -
    session_epoch erzwingt eine bewusste Neuanmeldung."""
    vorher = db.execute("SELECT session_epoch FROM users WHERE username='admin'").fetchone()['session_epoch']
    res = auth_client.post('/api/setup/profiles/self',
                           json={'person_id': None, 'new_username': 'jana'},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    assert res.get_json()['renamed'] is True
    row = db.execute("SELECT username, session_epoch FROM users WHERE id=1").fetchone()
    assert row['username'] == 'jana'
    assert row['session_epoch'] > vorher
    db.execute("UPDATE users SET username='admin' WHERE id=1")
    db.commit()


def test_umbenennung_auf_vergebenen_namen_wird_abgelehnt(auth_client, csrf, db):
    db.execute("INSERT INTO users (username, password_hash, role) VALUES ('belegt','x','member')")
    db.commit()
    res = auth_client.post('/api/setup/profiles/self',
                           json={'person_id': None, 'new_username': 'belegt'},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409
    db.execute("DELETE FROM users WHERE username='belegt'")
    db.commit()


def test_abschluss_setzt_flag(auth_client, csrf, db):
    assert auth_client.get('/api/setup/profiles').get_json()['done'] is False
    res = auth_client.post('/api/setup/profiles/complete', headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    assert auth_client.get('/api/setup/profiles').get_json()['done'] is True
    db.execute("DELETE FROM app_meta WHERE key='profiles_setup_done'")
    db.commit()


def test_assistent_nur_fuer_admin(auth_client, db):
    db.execute("UPDATE users SET role='member' WHERE username='admin'")
    db.commit()
    assert auth_client.get('/api/setup/profiles').status_code == 403
    db.execute("UPDATE users SET role='admin' WHERE username='admin'")
    db.commit()
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `.venv-dev/bin/pytest tests/test_setup_wizard.py -v`
Erwartung: `AttributeError: module has no attribute '_benutzername_aus_person'` und 404 für die Routen.

- [ ] **Step 3: Namensableitung und Endpunkte implementieren**

```python
# ── Einrichtungsassistent für Benutzerprofile ─────────────────────────────────
_UMLAUTE = {'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss'}


def _benutzername_aus_person(name: str, vergeben: set) -> str:
    """Leitet einen Benutzernamen aus einem Personennamen ab.

    Kleinschreibung, Umlaut-Transliteration, Leerzeichen zu Punkt, alles
    ausserhalb [a-z0-9._-] entfernt. Bei Kollision wird gezaehlt. Der Vorschlag
    ist im UI editierbar - eine Person namens "Admin" kollidiert absichtlich
    sichtbar mit dem Bestandskonto statt still umbenannt zu werden.
    """
    import re as _re
    s = (name or '').strip().lower()
    for umlaut, ersatz in _UMLAUTE.items():
        s = s.replace(umlaut, ersatz)
    s = _re.sub(r'\s+', '.', s)
    s = _re.sub(r'[^a-z0-9._-]', '', s).strip('.')
    if not s:
        s = 'benutzer'
    kandidat, zaehler = s, 1
    while kandidat in vergeben:
        zaehler += 1
        kandidat = f'{s}{zaehler}'
    return kandidat


@main_bp.route('/api/setup/profiles')
@admin_required
def api_setup_profiles():
    conn = get_db()
    flag = conn.execute("SELECT value FROM app_meta WHERE key='profiles_setup_done'").fetchone()
    vergeben = {r['username'].lower() for r in conn.execute('SELECT username FROM users')}
    ich = conn.execute('SELECT username, person_id FROM users WHERE id=?',
                       (session.get('user_id'),)).fetchone()
    alle_personen = [dict(r) for r in conn.execute('SELECT id, name FROM persons ORDER BY name')]
    offen = conn.execute(
        'SELECT p.id, p.name FROM persons p'
        ' LEFT JOIN users u ON u.person_id = p.id'
        ' WHERE u.id IS NULL ORDER BY p.name').fetchall()
    vorschlaege = []
    for p in offen:
        name = _benutzername_aus_person(p['name'], vergeben)
        vergeben.add(name)
        vorschlaege.append({'person_id': p['id'], 'person_name': p['name'], 'username': name})
    conn.close()
    return jsonify(success=True, done=bool(flag and flag['value'] == '1'),
                   me={'username': ich['username'] if ich else '',
                       'person_id': ich['person_id'] if ich else None},
                   persons=alle_personen, suggestions=vorschlaege)


@main_bp.route('/api/setup/profiles/self', methods=['POST'])
@admin_required
def api_setup_profiles_self():
    """Schritt 1 des Assistenten: "Wer bist du?"

    Verknuepft das eigene Konto mit einer Person und erlaubt optional die
    Umbenennung des Bestandskontos (typisch: 'admin' -> echter Vorname).
    Eine Umbenennung macht den Namen in der Session veraltet, deshalb wird
    session_epoch erhoeht - der Admin muss sich danach bewusst neu anmelden.
    """
    d = request.get_json(silent=True) or {}
    eigene_id = session.get('user_id')
    neuer_name = (d.get('new_username') or '').strip()
    conn = get_db()

    if 'person_id' in d:
        person_id = int(d['person_id']) if d.get('person_id') else None
        try:
            conn.execute('UPDATE users SET person_id=? WHERE id=?', (person_id, eigene_id))
        except sqlite3.IntegrityError:
            return jsonify(success=False, error=t('srv.person_already_linked')), 409

    umbenannt = False
    if neuer_name:
        try:
            conn.execute('UPDATE users SET username=? WHERE id=?', (neuer_name, eigene_id))
        except sqlite3.IntegrityError:
            return jsonify(success=False, error=t('srv.username_taken')), 409
        _bump_session_epoch(conn, eigene_id)
        umbenannt = True

    conn.commit()
    return jsonify(success=True, renamed=umbenannt)


@main_bp.route('/api/setup/profiles/complete', methods=['POST'])
@admin_required
def api_setup_profiles_complete():
    conn = get_db()
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('profiles_setup_done','1')"
                 " ON CONFLICT(key) DO UPDATE SET value='1'")
    conn.commit()
    return jsonify(success=True)
```

- [ ] **Step 4: Tests ausführen, Erfolg bestätigen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

---

### Task 8: Einrichtungsassistent — Oberfläche

**Files:**
- Modify: `app/templates/settings.html` (Hinweis-Banner und Assistent-Bereich)
- Modify: `app/translations.py`

**Interfaces:**
- Consumes: `/api/setup/profiles`, `/api/setup/profiles/complete` aus Task 7, `/api/users` aus Task 5
- Produces: keine (Blattknoten)

- [ ] **Step 1: Banner und Assistent-Bereich ergänzen**

In `app/templates/settings.html` innerhalb des Admin-Blocks, direkt über der Benutzer-Karte:

```html
  <div class="col-12" id="setup-wizard-wrap" hidden>
    <div class="alert alert-info d-flex flex-wrap align-items-center gap-2">
      <i class="bi bi-info-circle"></i>
      <span class="me-auto">{{ t('settings.wizard_banner') }}</span>
      <button class="btn btn-sm btn-primary" id="wizard-open-btn">{{ t('settings.wizard_start') }}</button>
      <button class="btn btn-sm btn-outline-secondary" id="wizard-later-btn">{{ t('settings.wizard_later') }}</button>
    </div>
    <div class="card shadow-sm" id="wizard-body" hidden>
      <div class="card-body">
        <h6 class="fw-semibold">{{ t('settings.wizard_step1') }}</h6>
        <p class="text-muted small mb-2">{{ t('settings.wizard_step1_hint') }}</p>
        <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
          <select class="form-select form-select-sm" id="wizard-me-person" style="max-width:14rem"></select>
          <input type="text" class="form-control form-control-sm" id="wizard-me-username"
                 style="max-width:14rem" placeholder="{{ t('settings.wizard_rename_placeholder') }}">
          <button class="btn btn-sm btn-primary" id="wizard-me-save">{{ t('settings.wizard_step1_save') }}</button>
        </div>
        <div class="alert alert-warning py-2 small" id="wizard-rename-warn" hidden>
          {{ t('settings.wizard_rename_warn') }}
        </div>
        <hr>
        <h6 class="fw-semibold">{{ t('settings.wizard_step2') }}</h6>
        <p class="text-muted small mb-2">{{ t('settings.wizard_hint') }}</p>
        <div id="wizard-list" class="d-flex flex-column gap-2"></div>
        <div class="mt-3 d-flex flex-wrap gap-2">
          <button class="btn btn-success" id="wizard-create-btn">{{ t('settings.wizard_create') }}</button>
          <button class="btn btn-outline-secondary" id="wizard-done-btn">{{ t('settings.wizard_done') }}</button>
        </div>
        <div class="alert alert-warning mt-3 mb-0" id="wizard-cleanup" hidden>
          {{ t('settings.wizard_cleanup') }}
        </div>
      </div>
    </div>
  </div>
```

- [ ] **Step 2: JavaScript ergänzen**

Am Ende des Admin-Blocks im `extra_script`. Namen werden ausschließlich über `value`/`textContent` gesetzt:

```javascript
let _wizardVorschlaege = [];
let _wizardDaten = null;

async function wizardLaden() {
  const d = await (await fetch('/api/setup/profiles')).json();
  if (!d.success || d.done) return;
  _wizardDaten = d;
  _wizardVorschlaege = d.suggestions || [];
  // Der Assistent lohnt sich, solange noch Personen ohne Login existieren
  // ODER das eigene Konto noch keiner Person zugeordnet ist.
  if (!_wizardVorschlaege.length && d.me && d.me.person_id) return;
  document.getElementById('setup-wizard-wrap').hidden = false;
}

/** Schritt 1: eigene Person zuordnen, optional das Bestandskonto umbenennen. */
function wizardSchritt1Aufbauen() {
  const sel = document.getElementById('wizard-me-person');
  sel.innerHTML = '';
  const leer = document.createElement('option');
  leer.value = ''; leer.textContent = t('settings.wizard_me_none');
  sel.appendChild(leer);
  (_wizardDaten.persons || []).forEach(p => {
    const o = document.createElement('option');
    o.value = p.id; o.textContent = p.name;
    if (_wizardDaten.me && _wizardDaten.me.person_id === p.id) o.selected = true;
    sel.appendChild(o);
  });
  document.getElementById('wizard-me-username').value = '';
}

document.getElementById('wizard-me-save').addEventListener('click', async () => {
  const personId = document.getElementById('wizard-me-person').value;
  const neuerName = document.getElementById('wizard-me-username').value.trim();
  if (neuerName && !confirm(t('settings.wizard_rename_confirm', {name: neuerName}))) return;
  const d = await (await fetch('/api/setup/profiles/self', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({person_id: personId ? parseInt(personId, 10) : null,
                          new_username: neuerName || null})
  })).json();
  if (!d.success) { showToast(d.error, 'danger'); return; }
  if (d.renamed) {
    // Die Session traegt jetzt einen veralteten Namen - bewusste Neuanmeldung.
    document.getElementById('wizard-rename-warn').hidden = false;
    setTimeout(() => { window.location.href = '/logout'; }, 2500);
    return;
  }
  showToast(t('settings.user_saved'), 'success');
  benutzerLaden();
});

function wizardZeile(v) {
  const row = document.createElement('div');
  row.className = 'd-flex flex-wrap align-items-center gap-2';
  const cb = document.createElement('input');
  cb.type = 'checkbox'; cb.className = 'form-check-input wizard-pick';
  cb.dataset.personId = v.person_id; cb.checked = true;
  const label = document.createElement('span');
  label.className = 'fw-semibold'; label.style.minWidth = '8rem';
  label.textContent = v.person_name;
  const feld = document.createElement('input');
  feld.type = 'text'; feld.className = 'form-control form-control-sm wizard-username';
  feld.style.maxWidth = '14rem'; feld.value = v.username;
  row.append(cb, label, feld);
  return row;
}

document.getElementById('wizard-open-btn').addEventListener('click', () => {
  wizardSchritt1Aufbauen();
  const liste = document.getElementById('wizard-list');
  liste.innerHTML = '';
  _wizardVorschlaege.forEach(v => liste.appendChild(wizardZeile(v)));
  document.getElementById('wizard-body').hidden = false;
});

document.getElementById('wizard-later-btn').addEventListener('click', () => {
  document.getElementById('setup-wizard-wrap').hidden = true;
});

document.getElementById('wizard-create-btn').addEventListener('click', async () => {
  const zeilen = [...document.querySelectorAll('#wizard-list > div')];
  let angelegt = 0;
  for (const z of zeilen) {
    const cb = z.querySelector('.wizard-pick');
    if (!cb.checked) continue;
    const username = z.querySelector('.wizard-username').value.trim();
    if (!username) continue;
    const d = await (await fetch('/api/users', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username, role: 'member', person_id: parseInt(cb.dataset.personId, 10)})
    })).json();
    if (d.success) { zeigeInitialpasswort(username, d.password); angelegt++; }
    else showToast(d.error, 'danger');
  }
  if (angelegt) {
    document.getElementById('wizard-cleanup').hidden = false;
    benutzerLaden();
  }
});

document.getElementById('wizard-done-btn').addEventListener('click', async () => {
  await fetch('/api/setup/profiles/complete', {method: 'POST'});
  document.getElementById('setup-wizard-wrap').hidden = true;
  showToast(t('settings.wizard_finished'), 'success');
});

wizardLaden();
```

- [ ] **Step 3: Übersetzungen ergänzen**

Deutscher Block:

```python
        'settings.wizard_banner': 'Benutzerprofile sind neu – jetzt einrichten?',
        'settings.wizard_start': 'Einrichten',
        'settings.wizard_later': 'Später',
        'settings.wizard_step1': '1. Wer bist du?',
        'settings.wizard_step1_hint': 'Ordne dein eigenes Konto einer Person zu. Optional kannst du es umbenennen – du meldest dich danach mit dem neuen Namen an.',
        'settings.wizard_step1_save': 'Übernehmen',
        'settings.wizard_step2': '2. Weitere Logins anlegen',
        'settings.wizard_me_none': '– keine –',
        'settings.wizard_rename_placeholder': 'Neuer Benutzername (optional)',
        'settings.wizard_rename_confirm': 'Dein Login heißt danach „{name}". Fortfahren?',
        'settings.wizard_rename_warn': 'Benutzername geändert – du wirst jetzt abgemeldet und meldest dich mit dem neuen Namen an.',
        'settings.wizard_hint': 'Für diese Personen gibt es noch keinen Login. Wähle aus, für wen ein Konto angelegt werden soll. Der Benutzername ist änderbar.',
        'settings.wizard_create': 'Konten anlegen',
        'settings.wizard_done': 'Fertig',
        'settings.wizard_finished': 'Einrichtung abgeschlossen',
        'settings.wizard_cleanup': 'Denk daran, das Passwort des gemeinsamen Zugangs zu ändern – die anderen kennen es noch.',
```

Englischer Block:

```python
        'settings.wizard_banner': 'User profiles are new – set them up now?',
        'settings.wizard_start': 'Set up',
        'settings.wizard_later': 'Later',
        'settings.wizard_step1': '1. Who are you?',
        'settings.wizard_step1_hint': 'Link your own account to a person. You can optionally rename it – you will sign in with the new name afterwards.',
        'settings.wizard_step1_save': 'Apply',
        'settings.wizard_step2': '2. Create more logins',
        'settings.wizard_me_none': '– none –',
        'settings.wizard_rename_placeholder': 'New username (optional)',
        'settings.wizard_rename_confirm': 'Your login will be "{name}". Continue?',
        'settings.wizard_rename_warn': 'Username changed – you will be signed out and can sign in with the new name.',
        'settings.wizard_hint': 'These people do not have a login yet. Pick who should get an account. The username can be edited.',
        'settings.wizard_create': 'Create accounts',
        'settings.wizard_done': 'Done',
        'settings.wizard_finished': 'Setup complete',
        'settings.wizard_cleanup': 'Remember to change the password of the shared account – the others still know it.',
```

- [ ] **Step 4: Suite prüfen**

Run: `.venv-dev/bin/pytest tests/ -q`
Erwartung: alle Tests PASS.

---

## Abschluss Phase B

Nach Task 8 folgt die vom Projekteigner gewünschte **komplette Sicherheitsprüfung**. Offene Punkte, die dort behandelt werden müssen und in diesem Plan bewusst nicht angefasst wurden:

- Gespeichertes XSS in der Namensdarstellung (`settings.html` `personsOpts`/`catsOpts`, `month.html` `buildPersonOpts`/`buildCatOpts` → `innerHTML`) — vorbestehend, heute ausnutzbar
- FK-`IntegrityError` verliert den gesamten Speicher-Batch (HTTP 500), wenn parallel eine Person gelöscht wird
- Ungeschützte `int()`-Umwandlungen in den Speicher-Endpunkten
- Restore entfernt `-wal`/`-shm`, während andere Threads die Datenbank offen haben können
- Zwei Tests aus Phase A, die auch ohne den zugehörigen Fix grün wären
