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

    # Gueltige SQLite-Datei als Upload erzeugen. Schema entspricht bewusst dem
    # echten `persons`-Schema (inkl. UNIQUE): der Restore ersetzt die komplette
    # DB-Datei (main.py:2085, shutil.copy), init_db() in `finally` unten repariert
    # fehlende Tabellen/Spalten nur additiv (CREATE TABLE IF NOT EXISTS / ALTER
    # TABLE ADD COLUMN) und kann eine fehlende UNIQUE-Constraint nicht nachtragen.
    # Ein unrealistisch minimales Test-Backup wuerde also `persons.name` fuer den
    # Rest der Testsession dauerhaft nicht mehr UNIQUE machen.
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


def test_restore_einer_vor_phase_b_datenbank_bleibt_benutzbar(app, auth_client, csrf, tmp_path):
    """Regression Blocker 1+2: Wiederherstellung einer Sicherung von VOR der
    Profil-Migration (nur id/username/password_hash) darf die App nicht
    unbrauchbar machen.

    Ohne Blocker-1-Fix fehlen nach dem simplen Datei-Austausch die Spalten
    role/is_active/session_epoch/must_change_password/person_id, auf die seit
    dieser Phase JEDE Anfrage angewiesen ist (login_required, login()) - jede
    Route inkl. /login wuerfe einen unbehandelten sqlite3.OperationalError,
    und nur ein Container-Neustart wuerde init_db() erneut laufen lassen.

    Ohne Blocker-2-Fix bleibt die alte Verbindung aus admin_required/
    login_required waehrend des shutil.copy offen; ihr Checkpoint beim
    Schliessen (nach Requestende) kann dann alte WAL-Frames in die frisch
    eingespielte Datei zurueckschreiben - also genau die Korruption, gegen
    die die WAL-Sidecar-Bereinigung eigentlich schuetzen soll.
    """
    from werkzeug.security import generate_password_hash
    import sqlite3 as _sqlite3

    alte_db = tmp_path / 'vor_phase_b.db'
    con = _sqlite3.connect(str(alte_db))
    con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,'
                ' username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL)')
    con.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                ('oldadmin', generate_password_hash('altes-passwort-123')))
    con.commit()
    con.close()

    try:
        with open(alte_db, 'rb') as fh:
            res = auth_client.post('/api/backup/restore',
                                   data={'file': (fh, 'vor_phase_b.db')},
                                   headers={'X-CSRF-Token': csrf},
                                   content_type='multipart/form-data')
        assert res.status_code == 200
        assert res.get_json()['success'] is True

        # Die eigentliche Behauptung: ein ganz normaler Folge-Request
        # funktioniert noch. Ohne Blocker-1-Fix wirft schon dieses Login
        # einen 500er, weil login() `is_active, session_epoch` selektiert -
        # Spalten, die die wiederhergestellte Datei nicht mehr hat.
        client = app.test_client()
        login_res = client.post('/login', data={'username': 'oldadmin',
                                                 'password': 'altes-passwort-123'})
        assert login_res.status_code == 302
        assert '/login' not in login_res.headers['Location']

        # Und eine laengst bestehende Seite laedt ebenfalls - das uebt
        # login_required's SELECT auf role/is_active/session_epoch/
        # must_change_password/person_id aus.
        overview_res = client.get('/overview')
        assert overview_res.status_code == 200
    finally:
        # Die wiederhergestellte Datei hat die komplette users-Tabelle
        # ersetzt (nur 'oldadmin', kein 'admin' mehr). Fuer alle folgenden
        # Tests muss der urspruengliche Zustand (ein einziger 'admin' mit dem
        # Test-Passwort, id=1 - andere Tests verlassen sich hart darauf)
        # wiederhergestellt werden - init_db()'s Bootstrap (leere Tabelle ->
        # frischer 'admin' aus ADMIN_PASSWORD) reproduziert exakt den Zustand,
        # den conftest beim Start dieser Session angelegt hat. Der
        # AUTOINCREMENT-Zaehler muss dafuer mit zurueckgesetzt werden, sonst
        # bekaeme der neue 'admin' wegen 'oldadmin' (id=1) die id=2.
        conn = m._open_db(m.DB_PATH)
        conn.execute('DELETE FROM users')
        conn.execute("DELETE FROM sqlite_sequence WHERE name='users'")
        conn.commit()
        conn.close()
        with app.app_context():
            m.init_db()