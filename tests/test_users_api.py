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


def test_person_id_im_update_wird_ignoriert_und_bumped_session_nicht(auth_client, csrf, db):
    """Frueher Regression Important 5 (reine Personen-Verknuepfung ueber diesen
    Endpunkt durfte die eigene Sitzung nicht beenden). Seit der strikten
    1:1-Kopplung nimmt POST /api/users/<id> `person_id` gar nicht mehr an
    (Befund I3) - ein mitgesendeter Wert aendert weder die Verknuepfung noch
    beendet er die Sitzung."""
    cur = db.execute("INSERT INTO persons (name) VALUES ('Ivo')")
    pid = cur.lastrowid
    db.commit()
    try:
        eigene = db.execute("SELECT id, session_epoch, person_id FROM users"
                            " WHERE username='admin'").fetchone()
        res = auth_client.post(f'/api/users/{eigene["id"]}', json={'person_id': pid},
                               headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        nachher = db.execute("SELECT session_epoch, person_id FROM users"
                             " WHERE id=?", (eigene['id'],)).fetchone()
        assert nachher['session_epoch'] == eigene['session_epoch']
        assert nachher['person_id'] == eigene['person_id'], \
            'person_id wurde ueber das Update-Payload uebernommen'
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


def test_anlegen_ohne_person_id_erzeugt_person_automatisch(auth_client, csrf, db):
    """Kernaenderung von Task 4: fehlt person_id, entsteht die Person
    automatisch mit demselben Namen wie der Benutzername - atomar mit
    dem Benutzerkonto."""
    try:
        res = _anlegen(auth_client, csrf, username='dora', person_id=None)
        assert res.status_code == 200
        body = res.get_json()
        person = db.execute("SELECT id, name FROM persons WHERE name='dora'").fetchone()
        assert person is not None, 'Es wurde keine Person zum neuen Konto angelegt'
        benutzer = db.execute("SELECT person_id FROM users WHERE username='dora'").fetchone()
        assert benutzer['person_id'] == person['id']
    finally:
        db.execute("DELETE FROM users WHERE username='dora'")
        db.execute("DELETE FROM persons WHERE name='dora'")
        db.commit()


def test_anlegen_mit_vorhandener_person_id_bleibt_moeglich(auth_client, csrf, db):
    """Rueckwaertskompatibilitaet: ein Aufruf MIT person_id legt keine
    zusaetzliche Person an, nutzt die uebergebene."""
    db.execute("INSERT INTO persons (name) VALUES ('Elias')")
    elias = db.execute("SELECT id FROM persons WHERE name='Elias'").fetchone()['id']
    db.commit()
    try:
        res = _anlegen(auth_client, csrf, username='eliaskonto', person_id=elias)
        assert res.status_code == 200
        anzahl_personen_namens_elias = db.execute(
            "SELECT COUNT(*) c FROM persons WHERE name='Elias'").fetchone()['c']
        assert anzahl_personen_namens_elias == 1
        benutzer = db.execute("SELECT person_id FROM users WHERE username='eliaskonto'").fetchone()
        assert benutzer['person_id'] == elias
    finally:
        db.execute("DELETE FROM users WHERE username='eliaskonto'")
        db.execute("DELETE FROM persons WHERE name='Elias'")
        db.commit()


def test_loeschen_entfernt_verknuepfte_person_atomar(auth_client, csrf, db):
    """Kernaenderung von Task 4: Konto loeschen entfernt die verknuepfte
    Person in derselben Aktion mit."""
    try:
        res = _anlegen(auth_client, csrf, username='finn', person_id=None)
        neue_id = res.get_json()['id']
        assert db.execute("SELECT id FROM persons WHERE name='finn'").fetchone() is not None

        res = auth_client.delete(f'/api/users/{neue_id}', headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        assert db.execute("SELECT id FROM users WHERE id=?", (neue_id,)).fetchone() is None
        assert db.execute("SELECT id FROM persons WHERE name='finn'").fetchone() is None, \
            'Person wurde beim Loeschen des Kontos nicht mitentfernt'
    finally:
        db.execute("DELETE FROM users WHERE username='finn'")
        db.execute("DELETE FROM persons WHERE name='finn'")
        db.commit()


def test_loeschen_ohne_verknuepfte_person_funktioniert_weiterhin(auth_client, csrf, db):
    """Randfall: ein (nach altem Bestand) unverknuepftes Konto laesst sich
    weiterhin loeschen, ohne dass ein Fehler auftritt, weil keine Person
    zum Mitloeschen existiert."""
    try:
        res = _anlegen(auth_client, csrf, username='greta', person_id=None)
        neue_id = res.get_json()['id']
        db.execute("UPDATE users SET person_id=NULL WHERE id=?", (neue_id,))
        db.execute("DELETE FROM persons WHERE name='greta'")
        db.commit()

        res = auth_client.delete(f'/api/users/{neue_id}', headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
    finally:
        db.execute("DELETE FROM users WHERE username='greta'")
        db.execute("DELETE FROM persons WHERE name='greta'")
        db.commit()


def test_fehlgeschlagener_user_insert_hinterlaesst_keine_verwaiste_person(auth_client, csrf, db):
    """Schlaegt der User-Insert fehl (Benutzername bereits vergeben), nachdem
    im selben Request bereits eine Person automatisch angelegt wurde, darf
    davon keine Spur bleiben - Person-Insert und User-Insert muessen in
    derselben Transaktion stehen und gemeinsam zurueckgerollt werden."""
    db.execute("INSERT INTO users (username, password_hash) VALUES ('milo', 'x')")
    db.commit()
    try:
        res = _anlegen(auth_client, csrf, username='milo', person_id=None)
        assert res.status_code == 409
        assert res.get_json()['success'] is False
        assert db.execute("SELECT id FROM persons WHERE name='milo'").fetchone() is None, \
            'Verwaiste Person nach fehlgeschlagenem User-Insert (kein Rollback)'
    finally:
        db.execute("DELETE FROM users WHERE username='milo'")
        db.execute("DELETE FROM persons WHERE name='milo'")
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
