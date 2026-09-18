import os
from pathlib import Path

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


def _schreibe_backup_datei(backup_dir, name, mtime):
    """Legt eine Sicherungsdatei mit definierter Aenderungszeit an.

    create_backup() taugt hier nicht zum Befuellen: der Zeitstempel im
    Dateinamen ist minutengenau, zwei Sicherungen mit demselben Label
    innerhalb einer Minute wuerden sich gegenseitig ueberschreiben.
    """
    pfad = backup_dir / name
    pfad.write_bytes(b'nicht wirklich sqlite')
    os.utime(pfad, (mtime, mtime))
    return pfad


def test_schutzkopien_rotieren_getrennt_von_gewoehnlichen(app):
    """Regression Important 6: eine Absturzschleife waehrend der Migration
    schreibt bei jedem Neustart eine neue 'pre-profiles'-Sicherung (init_db()
    prueft nur, ob die Spalte 'role' noch fehlt). In einem gemeinsamen Topf
    verdraengen 30 solcher Neustarts jede vorher vorhandene Sicherung - also
    genau die, die im Ernstfall die letzte Rettung waere.

    Die Schutzkopie ist hier bewusst die AELTESTE Datei und ihr Label sortiert
    vor den gewoehnlichen ('pre-' < 'sicherung'): unter der alten, gemeinsamen
    Rotation waere sie damit als erste verdraengt worden, egal ob nach Name
    oder nach Alter sortiert wird. Der Test faellt also ohne den Fix wirklich.
    """
    backup_dir = Path(os.path.dirname(m.DB_PATH)) / 'backups'
    backup_dir.mkdir(exist_ok=True)
    for f in backup_dir.glob('kassenstuerzle_*.db'):
        f.unlink()
    try:
        schutz = _schreibe_backup_datei(
            backup_dir, 'kassenstuerzle_2026-01-01_00-00_pre-profiles.db', 1_000_000)
        for i in range(31):
            _schreibe_backup_datei(
                backup_dir, f'kassenstuerzle_2026-01-01_00-{i:02d}_sicherung{i}.db',
                2_000_000 + i)

        with app.app_context():
            m.create_backup('ausloeser')

        assert schutz.exists(), 'Schutzkopie wurde trotz getrennter Rotation verdraengt'
        gewoehnlich = [f for f in backup_dir.glob('kassenstuerzle_*.db')
                       if not m._ist_schutzkopie(f.name)]
        assert len(gewoehnlich) == m._MAX_BACKUPS, (
            'Gewoehnliche Sicherungen werden nicht mehr auf '
            f'{m._MAX_BACKUPS} begrenzt: {len(gewoehnlich)}')
    finally:
        for f in backup_dir.glob('kassenstuerzle_*.db'):
            f.unlink()


def test_schutzkopien_wachsen_nicht_unbegrenzt(app):
    """Schutzkopien duerfen nicht einfach von der Rotation ausgenommen sein.

    Jeder Restore legt eine 'pre-restore'-Sicherung an, und das Label stammt
    bei /api/backup/create aus einer Benutzereingabe, die Bindestriche stehen
    laesst - ein reiner Ausschluss waere also beliebig oft ausloesbar und
    wuerde die Platte volllaufen lassen. Erwartet wird ein eigener, aber
    begrenzter Topf.
    """
    backup_dir = Path(os.path.dirname(m.DB_PATH)) / 'backups'
    backup_dir.mkdir(exist_ok=True)
    for f in backup_dir.glob('kassenstuerzle_*.db'):
        f.unlink()
    try:
        for i in range(m._MAX_SCHUTZ_BACKUPS + 4):
            _schreibe_backup_datei(
                backup_dir, f'kassenstuerzle_2026-01-01_00-{i:02d}_pre-restore.db',
                1_000_000 + i)

        with app.app_context():
            m.create_backup('ausloeser')

        schutzkopien = [f for f in backup_dir.glob('kassenstuerzle_*.db')
                        if m._ist_schutzkopie(f.name)]
        assert len(schutzkopien) == m._MAX_SCHUTZ_BACKUPS, (
            f'Schutzkopien nicht begrenzt: {len(schutzkopien)}')
        # Begrenzt wird von hinten: die juengsten bleiben erhalten.
        assert all('00-0' not in f.name or int(f.name.split('00-')[1][:2]) >= 4
                   for f in schutzkopien)
    finally:
        for f in backup_dir.glob('kassenstuerzle_*.db'):
            f.unlink()


def test_restore_schliesst_verbindung_vor_dateitausch(app, auth_client, csrf, tmp_path,
                                                      monkeypatch):
    """Regression Blocker 2: beim Dateitausch darf keine Verbindung zur ALTEN
    Datei mehr offen sein.

    Seit Phase B oeffnet login_required (ueber admin_required) auf JEDER
    Anfrage eine Verbindung und legt sie in `g` ab - sie wuerde erst in der
    Teardown-Phase geschlossen, also nach dem shutil.copy. Ihr Checkpoint
    beim Schliessen kann dann alte WAL-Frames in die frisch eingespielte
    Datei zurueckschreiben.

    Die resultierende Korruption ist nicht deterministisch reproduzierbar,
    deshalb prueft der Test die Bedingung selbst: im Moment des Kopierens
    darf in `g` keine offene DB-Verbindung mehr haengen.
    """
    from flask import g as _g

    beobachtet = {}
    echtes_copy = m.shutil.copy

    def pruefendes_copy(src, dst, *args, **kwargs):
        beobachtet['g_db'] = _g.get('db', None)
        return echtes_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(m.shutil, 'copy', pruefendes_copy)

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
        assert 'g_db' in beobachtet, 'shutil.copy wurde gar nicht aufgerufen'
        assert beobachtet['g_db'] is None, (
            'Beim Dateitausch haengt noch eine offene Verbindung zur alten '
            'Datenbank in g - der Checkpoint beim Schliessen kann die '
            'wiederhergestellte Datei beschaedigen')
    finally:
        with app.app_context():
            m.init_db()
