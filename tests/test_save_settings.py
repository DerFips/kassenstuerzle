import pytest


@pytest.fixture()
def person(db):
    """Person 'Alice', mit dem Test-Admin verknuepft: wiederkehrende Vorlagen
    sind seit dem Umzug ins Profil strikt an die eigene Person gebunden, ein
    Konto ohne Person darf gar keine speichern. Aufgeraeumt ueber die
    db-Fixture - das Loeschen der Person setzt users.person_id per
    ON DELETE SET NULL zurueck."""
    cur = db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (cur.lastrowid,))
    db.commit()
    return cur.lastrowid


def _save_persons(client, csrf, **payload):
    body = {'v': 2, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/persons/save', json=body, headers={'X-CSRF-Token': csrf})


def _save_cats(client, csrf, **payload):
    body = {'v': 2, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/categories/save', json=body, headers={'X-CSRF-Token': csrf})


def test_persons_legacy_array_rejected(auth_client, csrf):
    res = auth_client.post('/api/persons/save', json=[{'name': 'Alice'}],
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409


def test_person_added_by_other_user_survives(auth_client, csrf, db):
    """Das 'Charlie wird geloescht'-Szenario aus der Spec."""
    alice = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Alice'}]).get_json()['id_map']['a']
    bob = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'b', 'name': 'Bob'}]).get_json()['id_map']['b']
    charlie = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'c', 'name': 'Charlie'}]).get_json()['id_map']['c']

    # User 2 speichert seinen veralteten Stand (Alice + Bob, ohne Charlie)
    _save_persons(auth_client, csrf, rows=[
        {'id': alice, 'name': 'Alice neu'},
        {'id': bob, 'name': 'Bob'}])

    names = {r['name'] for r in db.execute('SELECT name FROM persons').fetchall()}
    assert 'Charlie' in names, 'Charlie wurde geloescht'
    assert 'Alice neu' in names
    assert charlie is not None


def test_person_explicit_delete(auth_client, csrf, db):
    pid = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Weg'}]).get_json()['id_map']['a']
    _save_persons(auth_client, csrf, deleted_ids=[pid])
    assert db.execute('SELECT COUNT(*) c FROM persons').fetchone()['c'] == 0


def test_person_wallet_fields_roundtrip(auth_client, csrf, db):
    pid = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Alice',
         'wallet_start_date': '2026-01', 'wallet_start_amount': 200.5}]).get_json()['id_map']['a']
    row = db.execute('SELECT * FROM persons WHERE id=?', (pid,)).fetchone()
    assert row['wallet_start_date'] == '2026-01'
    assert row['wallet_start_amount'] == 200.5


def test_person_invalid_wallet_date_becomes_null(auth_client, csrf, db):
    pid = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Alice', 'wallet_start_date': 'quatsch'}]).get_json()['id_map']['a']
    row = db.execute('SELECT * FROM persons WHERE id=?', (pid,)).fetchone()
    assert row['wallet_start_date'] is None


def test_category_added_by_other_user_survives(auth_client, csrf, db):
    miete = _save_cats(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Miete', 'color': '#111111', 'is_split': True}]).get_json()['id_map']['a']
    _save_cats(auth_client, csrf, rows=[
        {'client_key': 'b', 'name': 'Freizeit', 'color': '#222222'}])

    _save_cats(auth_client, csrf, rows=[{'id': miete, 'name': 'Miete', 'color': '#111111'}])

    names = {r['name'] for r in db.execute('SELECT name FROM categories').fetchall()}
    assert names == {'Miete', 'Freizeit'}


def test_category_splits_are_replaced_per_category(auth_client, csrf, db):
    pid = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'p', 'name': 'Alice'}]).get_json()['id_map']['p']
    cid = _save_cats(auth_client, csrf, rows=[
        {'client_key': 'c', 'name': 'Lebensmittel', 'is_split': True,
         'splits': {str(pid): 3}}]).get_json()['id_map']['c']

    row = db.execute('SELECT share FROM category_splits WHERE category_id=? AND person_id=?',
                     (cid, pid)).fetchone()
    assert row['share'] == 3

    _save_cats(auth_client, csrf, rows=[
        {'id': cid, 'name': 'Lebensmittel', 'is_split': True, 'splits': {str(pid): 1}}])
    row = db.execute('SELECT share FROM category_splits WHERE category_id=?', (cid,)).fetchone()
    assert row['share'] == 1


def test_settings_bumps_activity(auth_client, csrf, db):
    v1 = _save_persons(auth_client, csrf, rows=[{'client_key': 'a', 'name': 'A'}]).get_json()['activity_version']
    v2 = _save_cats(auth_client, csrf, rows=[{'client_key': 'b', 'name': 'B'}]).get_json()['activity_version']
    assert v2 == v1 + 1


def test_no_conflicts_reported_when_all_saved(auth_client, csrf, db):
    body = _save_persons(auth_client, csrf, rows=[{'client_key': 'a', 'name': 'Alice'}]).get_json()
    assert body['conflicts'] == []


def test_duplicate_person_name_is_reported(auth_client, csrf, db):
    _save_persons(auth_client, csrf, rows=[{'client_key': 'a', 'name': 'Alice'}])
    body = _save_persons(auth_client, csrf, rows=[{'client_key': 'b', 'name': 'Alice'}]).get_json()
    assert body['success'] is True
    assert body['conflicts'] == ['Alice']
    assert db.execute('SELECT COUNT(*) c FROM persons').fetchone()['c'] == 1


def test_rename_to_taken_name_is_reported_not_500(auth_client, csrf, db):
    alice = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Alice'}]).get_json()['id_map']['a']
    _save_persons(auth_client, csrf, rows=[{'client_key': 'b', 'name': 'Bob'}])

    res = _save_persons(auth_client, csrf, rows=[{'id': alice, 'name': 'Bob'}])
    assert res.status_code == 200, 'Umbenennen auf vergebenen Namen darf nicht crashen'
    assert res.get_json()['conflicts'] == ['Bob']
    names = {r['name'] for r in db.execute('SELECT name FROM persons').fetchall()}
    assert names == {'Alice', 'Bob'}


def test_other_rows_still_saved_despite_conflict(auth_client, csrf, db):
    _save_persons(auth_client, csrf, rows=[{'client_key': 'a', 'name': 'Alice'}])
    body = _save_persons(auth_client, csrf, rows=[
        {'client_key': 'b', 'name': 'Alice'},
        {'client_key': 'c', 'name': 'Charlie'}]).get_json()
    assert body['conflicts'] == ['Alice']
    names = {r['name'] for r in db.execute('SELECT name FROM persons').fetchall()}
    assert names == {'Alice', 'Charlie'}


def test_duplicate_category_name_is_reported(auth_client, csrf, db):
    _save_cats(auth_client, csrf, rows=[{'client_key': 'a', 'name': 'Miete'}])
    body = _save_cats(auth_client, csrf, rows=[{'client_key': 'b', 'name': 'Miete'}]).get_json()
    assert body['conflicts'] == ['Miete']
    assert db.execute('SELECT COUNT(*) c FROM categories').fetchone()['c'] == 1


def _save_recur(client, csrf, **payload):
    body = {'v': 2, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/recurring/save', json=body, headers={'X-CSRF-Token': csrf})


def test_recurring_legacy_array_rejected(auth_client, csrf):
    res = auth_client.post('/api/recurring/save', json=[{'description': 'Netflix'}],
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409


def test_recurring_added_by_other_user_survives(auth_client, csrf, db, person):
    netflix = _save_recur(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Netflix', 'amount': 17.99}]).get_json()['id_map']['a']
    _save_recur(auth_client, csrf, rows=[
        {'client_key': 'b', 'day': 5, 'description': 'Spotify', 'amount': 9.99}])

    # Veralteter Stand mit nur Netflix darf Spotify nicht loeschen
    _save_recur(auth_client, csrf, rows=[
        {'id': netflix, 'day': 1, 'description': 'Netflix', 'amount': 17.99}])

    descs = {r['description'] for r in db.execute('SELECT description FROM recurring_expenses').fetchall()}
    assert descs == {'Netflix', 'Spotify'}


def test_recurring_update_and_explicit_delete(auth_client, csrf, db, person):
    rid = _save_recur(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Netflix', 'amount': 17.99}]).get_json()['id_map']['a']

    _save_recur(auth_client, csrf, rows=[
        {'id': rid, 'day': 3, 'description': 'Netflix Premium', 'amount': 19.99,
         'frequency': 'yearly', 'start_month': 4}])
    row = db.execute('SELECT * FROM recurring_expenses WHERE id=?', (rid,)).fetchone()
    assert row['description'] == 'Netflix Premium'
    assert row['day'] == 3
    assert row['frequency'] == 'yearly'
    assert row['start_month'] == 4

    _save_recur(auth_client, csrf, deleted_ids=[rid])
    assert db.execute('SELECT COUNT(*) c FROM recurring_expenses').fetchone()['c'] == 0


def test_recurring_blanked_row_is_deleted(auth_client, csrf, db, person):
    """Eine geleerte Zeile (leere Beschreibung, Betrag 0) mit vorhandener id
    muss verschwinden statt beim naechsten Laden wieder aufzutauchen."""
    rid = _save_recur(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Netflix', 'amount': 17.99}]).get_json()['id_map']['a']

    _save_recur(auth_client, csrf, rows=[
        {'id': rid, 'day': 1, 'description': '', 'amount': 0}])

    assert db.execute('SELECT COUNT(*) c FROM recurring_expenses WHERE id=?',
                      (rid,)).fetchone()['c'] == 0


def test_recurring_monthly_forces_start_month_one(auth_client, csrf, db, person):
    rid = _save_recur(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Miete', 'amount': 750.0,
         'frequency': 'monthly', 'start_month': 7}]).get_json()['id_map']['a']
    row = db.execute('SELECT start_month FROM recurring_expenses WHERE id=?', (rid,)).fetchone()
    assert row['start_month'] == 1


def test_fremde_wiederkehrende_vorlage_bleibt_unangetastet(auth_client, csrf, db, person):
    """Serverseitig erzwungen: wiederkehrende Vorlagen sind streng persoenlich
    (Nutzerentscheidung, kein globales Widget, keine Ausnahme fuer Admins). Ein
    direkter API-Aufruf mit der id einer fremden Vorlage darf sie weder
    umschreiben noch loeschen - das Frontend zeigt ohnehin nur die eigenen an,
    aber das ist keine Sicherheitsgrenze."""
    ben = db.execute("INSERT INTO persons (name) VALUES ('Ben')").lastrowid
    db.commit()
    fremde = db.execute(
        "INSERT INTO recurring_expenses (day, description, person_id, amount, frequency, start_month, active)"
        " VALUES (1, 'Bens Miete', ?, 500.0, 'monthly', 1, 1)", (ben,)).lastrowid
    db.commit()

    _save_recur(auth_client, csrf, rows=[
        {'id': fremde, 'day': 9, 'description': 'gekapert', 'amount': 999.0}])
    row = db.execute('SELECT description, amount, person_id FROM recurring_expenses WHERE id=?',
                     (fremde,)).fetchone()
    assert row['description'] == 'Bens Miete', 'fremde Vorlage wurde umgeschrieben'
    assert row['amount'] == 500.0
    assert row['person_id'] == ben

    _save_recur(auth_client, csrf, deleted_ids=[fremde])
    assert db.execute('SELECT COUNT(*) c FROM recurring_expenses WHERE id=?',
                      (fremde,)).fetchone()['c'] == 1, 'fremde Vorlage wurde geloescht'
