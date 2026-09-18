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
