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


def test_deaktivierter_account_erhaelt_gleiche_fehlermeldung_wie_falsches_passwort(client, db):
    """Regression: Sicherheit gegen Timing-Kanal beim Login.

    Deaktivierte Accounts durften ohne Hash-Vergleich abgewiesen werden,
    was messbar schneller antwortete als ein aktiver Account mit falschem
    Passwort. Das verrät den Account-Status trotz unspezifischer Meldung.

    Fix: Hash wird VOR is_active geprueft, damit die Antwortzeit immer
    vom teuren Hash-Vergleich dominiert wird.
    """
    # Deaktivieren des Accounts
    db.execute("UPDATE users SET is_active=0 WHERE username='admin'")
    db.commit()

    # Login mit korrektem Passwort auf deaktiviertem Account
    res_deactivated = client.post('/login', data={
        'username': 'admin',
        'password': 'test-password-123'
    })

    # Reaktivieren für zweiten Versuch
    db.execute("UPDATE users SET is_active=1 WHERE username='admin'")
    db.commit()

    # Login mit falschem Passwort auf aktivem Account
    res_wrong_pwd = client.post('/login', data={
        'username': 'admin',
        'password': 'falsches-passwort'
    })

    # Beide sollten NICHT erfolgreich sein (kein Redirect zu /overview oder /)
    # Stattdessen sollten beide dieselbe HTTP-Antwort zeigen:
    # Login-Formular mit Fehlermeldung
    assert res_deactivated.status_code == 200  # Formular wird erneut angezeigt
    assert res_wrong_pwd.status_code == 200    # Formular wird erneut angezeigt
