def _anlegen(client, csrf, **felder):
    body = {'username': 'bob', 'role': 'member', 'person_id': None}
    body.update(felder)
    return client.post('/api/users', json=body, headers={'X-CSRF-Token': csrf})


def test_liste_nur_fuer_admin(auth_client, db):
    db.execute("UPDATE users SET role='member' WHERE username='admin'")
    db.commit()
    try:
        assert auth_client.get('/api/users').status_code == 403
    finally:
        # Muss auch bei fehlgeschlagener Assertion laufen, sonst bleibt das
        # geteilte admin-Konto fuer den Rest des Testlaufs degradiert.
        db.execute("UPDATE users SET role='admin' WHERE username='admin'")
        db.commit()
    assert auth_client.get('/api/users').status_code == 200


def test_anlegen_liefert_initialpasswort_und_erzwingt_wechsel(auth_client, csrf, db):
    try:
        res = _anlegen(auth_client, csrf)
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] is True
        assert len(body['password']) >= 10
        row = db.execute("SELECT must_change_password, role FROM users WHERE username='bob'").fetchone()
        assert row['must_change_password'] == 1
        assert row['role'] == 'member'
    finally:
        db.execute("DELETE FROM users WHERE username='bob'")
        db.commit()


def test_doppelter_benutzername_wird_abgelehnt(auth_client, csrf, db):
    try:
        _anlegen(auth_client, csrf, username='carla')
        res = _anlegen(auth_client, csrf, username='carla')
        assert res.status_code == 409
        assert res.get_json()['success'] is False
    finally:
        db.execute("DELETE FROM users WHERE username='carla'")
        db.commit()


def test_eigene_rolle_nicht_aenderbar(auth_client, csrf, db):
    eigene = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()['id']
    res = auth_client.post(f'/api/users/{eigene}', json={'role': 'member'},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 400
    assert db.execute('SELECT role FROM users WHERE id=?', (eigene,)).fetchone()['role'] == 'admin'


def test_letzter_admin_nicht_deaktivierbar(auth_client, csrf, db):
    """Ein zweiter Admin wird angelegt, degradiert - dann darf der letzte
    verbliebene Admin sich nicht selbst aus dem System nehmen."""
    try:
        res = _anlegen(auth_client, csrf, username='zweitadmin', role='admin')
        neue_id = res.get_json()['id']
        auth_client.post(f'/api/users/{neue_id}', json={'is_active': 0},
                         headers={'X-CSRF-Token': csrf})
        eigene = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()['id']
        res = auth_client.post(f'/api/users/{eigene}', json={'is_active': 0},
                               headers={'X-CSRF-Token': csrf})
        assert res.status_code == 400
        assert db.execute('SELECT is_active FROM users WHERE id=?', (eigene,)).fetchone()['is_active'] == 1
    finally:
        db.execute("DELETE FROM users WHERE username='zweitadmin'")
        db.commit()


def test_deaktivierung_beendet_sessions(auth_client, csrf, db):
    try:
        res = _anlegen(auth_client, csrf, username='dora')
        neue_id = res.get_json()['id']
        vorher = db.execute('SELECT session_epoch FROM users WHERE id=?', (neue_id,)).fetchone()['session_epoch']
        auth_client.post(f'/api/users/{neue_id}', json={'is_active': 0}, headers={'X-CSRF-Token': csrf})
        nachher = db.execute('SELECT session_epoch FROM users WHERE id=?', (neue_id,)).fetchone()['session_epoch']
        assert nachher > vorher
    finally:
        db.execute("DELETE FROM users WHERE username='dora'")
        db.commit()


def test_passwort_zuruecksetzen(auth_client, csrf, db):
    try:
        res = _anlegen(auth_client, csrf, username='erik')
        neue_id = res.get_json()['id']
        res = auth_client.post(f'/api/users/{neue_id}/reset-password', headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        assert len(res.get_json()['password']) >= 10
        assert db.execute('SELECT must_change_password FROM users WHERE id=?',
                          (neue_id,)).fetchone()['must_change_password'] == 1
    finally:
        db.execute("DELETE FROM users WHERE username='erik'")
        db.commit()


def test_loeschen_und_letzter_admin_geschuetzt(auth_client, csrf, db):
    try:
        res = _anlegen(auth_client, csrf, username='frida')
        neue_id = res.get_json()['id']
        assert auth_client.delete(f'/api/users/{neue_id}', headers={'X-CSRF-Token': csrf}).status_code == 200
        assert db.execute('SELECT COUNT(*) c FROM users WHERE id=?', (neue_id,)).fetchone()['c'] == 0

        eigene = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()['id']
        assert auth_client.delete(f'/api/users/{eigene}', headers={'X-CSRF-Token': csrf}).status_code == 400
    finally:
        db.execute("DELETE FROM users WHERE username='frida'")
        db.commit()


def test_anlegen_mit_verknuepfter_person_liefert_richtige_meldung(auth_client, csrf, db):
    """Ein person_id, das schon an ein anderes Konto gebunden ist, muss die
    Personen-Meldung liefern - nicht die (falsche) Benutzername-Meldung."""
    cur = db.execute("INSERT INTO persons (name) VALUES ('Hanna')")
    pid = cur.lastrowid
    db.commit()
    try:
        res = _anlegen(auth_client, csrf, username='hanna', person_id=pid)
        assert res.status_code == 200

        res = _anlegen(auth_client, csrf, username='hanna2', person_id=pid)
        assert res.status_code == 409
        body = res.get_json()
        assert body['success'] is False
        assert body['error'] == 'Diese Person ist bereits mit einem Konto verknüpft.'

        # Das zweite Konto darf nicht angelegt worden sein.
        assert db.execute("SELECT COUNT(*) c FROM users WHERE username='hanna2'").fetchone()['c'] == 0
    finally:
        db.execute("DELETE FROM users WHERE username IN ('hanna', 'hanna2')")
        db.execute('DELETE FROM persons WHERE id=?', (pid,))
        db.commit()


def test_is_active_als_string_null_deaktiviert(auth_client, csrf, db):
    """{"is_active": "0"} muss deaktivieren - ein truthy-Test auf den
    String wuerde das Konto stattdessen versehentlich aktivieren."""
    try:
        res = _anlegen(auth_client, csrf, username='ida')
        neue_id = res.get_json()['id']

        res = auth_client.post(f'/api/users/{neue_id}', json={'is_active': '0'},
                               headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        assert db.execute('SELECT is_active FROM users WHERE id=?',
                          (neue_id,)).fetchone()['is_active'] == 0
    finally:
        db.execute("DELETE FROM users WHERE username='ida'")
        db.commit()


def test_eigenes_passwort_nicht_zuruecksetzbar(auth_client, csrf, db):
    """Regression Important 4: der Reset-Button steht auch auf der eigenen
    Zeile. Ein Klick darauf wuerde must_change_password setzen, die eigene
    Session per session_epoch sofort beenden und das neue Passwort nur
    einmalig in einem <div> zeigen, das bei der naechsten Navigation weg
    ist - ohne Selbstschutz gaebe es dann kein Break-Glass-Konto mehr."""
    eigene = db.execute("SELECT id, password_hash, session_epoch FROM users"
                        " WHERE username='admin'").fetchone()
    res = auth_client.post(f'/api/users/{eigene["id"]}/reset-password',
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 400
    body = res.get_json()
    assert body['success'] is False
    assert body['error'] == 'Das eigene Passwort änderst du unter Passwort ändern.'
    nachher = db.execute("SELECT password_hash, session_epoch FROM users"
                         " WHERE username='admin'").fetchone()
    assert nachher['password_hash'] == eigene['password_hash']
    assert nachher['session_epoch'] == eigene['session_epoch']


def test_reine_personen_verknuepfung_bumped_session_nicht(auth_client, csrf, db):
    """Regression Important 5: die Nutzerverwaltung zeigt einen
    Personen-Selektor auch auf der eigenen Zeile. Wuerde eine reine
    Personen-Aenderung die Session beenden, wuerde ein Admin sich beim
    Verknuepfen der eigenen Person selbst aussperren - benutzerLaden()
    bekaeme danach ein 302 statt JSON und die Seite wirkt eingefroren."""
    cur = db.execute("INSERT INTO persons (name) VALUES ('Ivo')")
    pid = cur.lastrowid
    db.commit()
    try:
        eigene = db.execute("SELECT id, session_epoch FROM users"
                            " WHERE username='admin'").fetchone()
        res = auth_client.post(f'/api/users/{eigene["id"]}', json={'person_id': pid},
                               headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        nachher = db.execute("SELECT session_epoch, person_id FROM users"
                             " WHERE id=?", (eigene['id'],)).fetchone()
        assert nachher['session_epoch'] == eigene['session_epoch']
        assert nachher['person_id'] == pid
    finally:
        db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
        db.execute('DELETE FROM persons WHERE id=?', (pid,))
        db.commit()


def test_rollenwechsel_bumped_session_weiterhin(auth_client, csrf, db):
    """Gegenstueck zum vorigen Test: eine echte Rollen-/Statusaenderung muss
    weiterhin bestehende Sitzungen beenden - sonst waere die Bedingung aus
    Important 5 zu weit gefasst."""
    try:
        res = _anlegen(auth_client, csrf, username='juna')
        neue_id = res.get_json()['id']
        vorher = db.execute('SELECT session_epoch FROM users WHERE id=?',
                            (neue_id,)).fetchone()['session_epoch']
        auth_client.post(f'/api/users/{neue_id}', json={'role': 'admin'},
                         headers={'X-CSRF-Token': csrf})
        nachher = db.execute('SELECT session_epoch FROM users WHERE id=?',
                             (neue_id,)).fetchone()['session_epoch']
        assert nachher > vorher
    finally:
        db.execute("DELETE FROM users WHERE username='juna'")
        db.commit()


def test_verknuepfte_person_nicht_loeschbar(auth_client, csrf, db):
    cur = db.execute("INSERT INTO persons (name) VALUES ('Greta')")
    pid = cur.lastrowid
    db.commit()
    try:
        _anlegen(auth_client, csrf, username='greta', person_id=pid)

        res = auth_client.post('/api/persons/save',
                               json={'v': 2, 'rows': [], 'deleted_ids': [pid]},
                               headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        assert res.get_json()['blocked'] == ['Greta']
        assert db.execute('SELECT COUNT(*) c FROM persons WHERE id=?', (pid,)).fetchone()['c'] == 1
    finally:
        db.execute("DELETE FROM users WHERE username='greta'")
        db.execute('DELETE FROM persons WHERE id=?', (pid,))
        db.commit()
