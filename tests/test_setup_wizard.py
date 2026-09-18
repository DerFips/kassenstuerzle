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
