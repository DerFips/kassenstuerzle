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


def test_check_password_hash_wird_auch_fuer_deaktivierten_account_aufgerufen(client, db, monkeypatch):
    """Regression: Sicherheit gegen Timing-Kanal beim Login.

    Die reine HTTP-Antwort (200, Formular erneut angezeigt) ist bei
    korrekter UND bei vertauschter Reihenfolge der Pruefungen identisch -
    der Unterschied ist nur messbar in der Antwortzeit. Der eigentliche
    Beweis gegen den Timing-Kanal ist deshalb, dass check_password_hash()
    fuer JEDEN existierenden Benutzernamen aufgerufen wird, unabhaengig
    von is_active. Nur dann kostet ein deaktivierter Account denselben
    teuren Hash-Vergleich wie ein aktiver Account mit falschem Passwort.

    Fix: Hash wird VOR is_active geprueft (main.py login()), sodass
    check_password_hash in beiden Faellen ausgefuehrt wird.
    """
    aufrufe = []
    echter_check_password_hash = m.check_password_hash

    def spionierender_check_password_hash(pwhash, password):
        aufrufe.append((pwhash, password))
        return echter_check_password_hash(pwhash, password)

    monkeypatch.setattr(m, 'check_password_hash', spionierender_check_password_hash)

    # Deaktivieren des Accounts
    db.execute("UPDATE users SET is_active=0 WHERE username='admin'")
    db.commit()

    # Login mit korrektem Passwort auf deaktiviertem Account
    res_deactivated = client.post('/login', data={
        'username': 'admin',
        'password': 'test-password-123'
    })
    assert res_deactivated.status_code == 200  # Formular wird erneut angezeigt
    assert len(aufrufe) == 1, (
        'check_password_hash muss auch fuer deaktivierte Accounts aufgerufen '
        'werden, sonst antwortet der Login messbar schneller'
    )

    # Reaktivieren für zweiten Versuch
    db.execute("UPDATE users SET is_active=1 WHERE username='admin'")
    db.commit()

    # Login mit falschem Passwort auf aktivem Account
    res_wrong_pwd = client.post('/login', data={
        'username': 'admin',
        'password': 'falsches-passwort'
    })
    assert res_wrong_pwd.status_code == 200    # Formular wird erneut angezeigt
    assert len(aufrufe) == 2, (
        'check_password_hash muss auch beim aktiven Account mit falschem '
        'Passwort aufgerufen werden'
    )


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
