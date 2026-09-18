import os

from app.routes import main as m


def test_connection_uses_wal_and_busy_timeout(db):
    mode = db.execute('PRAGMA journal_mode').fetchone()[0]
    assert mode.lower() == 'wal'
    timeout = db.execute('PRAGMA busy_timeout').fetchone()[0]
    assert timeout == 5000


def test_restore_removes_stale_wal_sidecars(app, auth_client, csrf, tmp_path):
    """Ein uebrig gebliebenes -wal darf die wiederhergestellte DB nicht zurueckrollen."""
    wal = m.DB_PATH + '-wal'
    shm = m.DB_PATH + '-shm'
    with open(wal, 'wb') as fh:
        fh.write(b'stale')
    with open(shm, 'wb') as fh:
        fh.write(b'stale')

    # Gueltige SQLite-Datei als Upload erzeugen
    src = tmp_path / 'backup.db'
    con = m.sqlite3.connect(str(src))
    con.execute('CREATE TABLE persons (id INTEGER PRIMARY KEY, name TEXT)')
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
        assert not os.path.exists(wal), '-wal wurde nicht entfernt'
        assert not os.path.exists(shm), '-shm wurde nicht entfernt'
    finally:
        # Der Restore hat die Test-Datenbank durch die minimale Upload-Datei
        # ersetzt. Ohne Wiederherstellung des Schemas scheitern alle folgenden
        # Tests an fehlenden Tabellen.
        with app.app_context():
            m.init_db()
