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
