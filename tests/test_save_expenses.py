import pytest


@pytest.fixture()
def person(db):
    cur = db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    db.commit()
    return cur.lastrowid


def _save(client, csrf, **payload):
    body = {'v': 2, 'year': 2026, 'month': 9, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/expenses/save', json=body, headers={'X-CSRF-Token': csrf})


def test_legacy_payload_rejected(auth_client, csrf, db):
    res = auth_client.post('/api/expenses/save',
                           json={'year': 2026, 'month': 9, 'expenses': []},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409


def test_insert_returns_id_map(auth_client, csrf, db, person):
    res = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 3, 'description': 'Einkauf',
         'person_id': person, 'category_id': None, 'amount': 41.2, 'is_cash': True}])
    assert res.status_code == 200
    body = res.get_json()
    assert body['success'] is True
    new_id = body['id_map']['c1']
    row = db.execute('SELECT * FROM expenses WHERE id=?', (new_id,)).fetchone()
    assert row['description'] == 'Einkauf'
    assert row['amount'] == pytest.approx(41.2)


def test_update_keeps_id_and_does_not_duplicate(auth_client, csrf, db, person):
    created = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'Miete',
         'person_id': person, 'amount': 750.0}]).get_json()['id_map']['c1']

    _save(auth_client, csrf, rows=[
        {'id': created, 'day': 2, 'description': 'Miete korrigiert',
         'person_id': person, 'amount': 760.0}])

    rows = db.execute('SELECT * FROM expenses WHERE year=2026 AND month=9').fetchall()
    assert len(rows) == 1, 'Upsert hat ein Duplikat erzeugt'
    assert rows[0]['id'] == created
    assert rows[0]['description'] == 'Miete korrigiert'
    assert rows[0]['day'] == 2


def test_missing_row_is_not_deleted(auth_client, csrf, db, person):
    """Kernszenario: ein veralteter Payload darf fremde Zeilen nicht loeschen."""
    keep = _save(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'von User 1',
         'person_id': person, 'amount': 10.0}]).get_json()['id_map']['a']

    # User 2 speichert seinen alten Stand, in dem diese Zeile fehlt
    _save(auth_client, csrf, rows=[
        {'client_key': 'b', 'day': 5, 'description': 'von User 2',
         'person_id': person, 'amount': 20.0}])

    ids = {r['id'] for r in db.execute('SELECT id FROM expenses').fetchall()}
    assert keep in ids, 'Zeile von User 1 wurde geloescht'
    assert len(ids) == 2


def test_explicit_delete_removes_row(auth_client, csrf, db, person):
    rid = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'weg',
         'person_id': person, 'amount': 5.0}]).get_json()['id_map']['c1']

    _save(auth_client, csrf, deleted_ids=[rid])
    assert db.execute('SELECT COUNT(*) c FROM expenses').fetchone()['c'] == 0


def test_zero_amount_deletes_existing_row(auth_client, csrf, db, person):
    rid = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'x',
         'person_id': person, 'amount': 5.0}]).get_json()['id_map']['c1']

    _save(auth_client, csrf, rows=[
        {'id': rid, 'day': 1, 'description': 'x', 'person_id': person, 'amount': 0}])
    assert db.execute('SELECT COUNT(*) c FROM expenses').fetchone()['c'] == 0


def test_zero_amount_new_row_ignored(auth_client, csrf, db, person):
    res = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'leer',
         'person_id': person, 'amount': 0}])
    assert res.get_json()['id_map'] == {}
    assert db.execute('SELECT COUNT(*) c FROM expenses').fetchone()['c'] == 0


def test_update_cannot_cross_month_boundary(auth_client, csrf, db, person):
    rid = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'September',
         'person_id': person, 'amount': 5.0}]).get_json()['id_map']['c1']

    # Versuch, dieselbe id ueber den Oktober-Endpunkt zu aendern
    body = {'v': 2, 'year': 2026, 'month': 10, 'rows': [
        {'id': rid, 'day': 9, 'description': 'gekapert', 'person_id': person, 'amount': 99.0}],
        'deleted_ids': []}
    auth_client.post('/api/expenses/save', json=body, headers={'X-CSRF-Token': csrf})

    row = db.execute('SELECT * FROM expenses WHERE id=?', (rid,)).fetchone()
    assert row['description'] == 'September', 'Monatsgrenze wurde nicht durchgesetzt'


def test_activity_version_increases(auth_client, csrf, db, person):
    first = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'a',
         'person_id': person, 'amount': 1.0}]).get_json()['activity_version']
    second = _save(auth_client, csrf, rows=[]).get_json()['activity_version']
    assert second == first + 1


def test_neue_zeile_bekommt_immer_die_eigene_person(auth_client, csrf, db):
    """Serverseitig erzwungen: ein Client kann person_id einer NEUEN Zeile
    nicht selbst bestimmen, egal was er mitschickt."""
    db.execute("INSERT INTO persons (name) VALUES ('Anna'), ('Ben')")
    anna = db.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()['id']
    ben = db.execute("SELECT id FROM persons WHERE name='Ben'").fetchone()['id']
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (anna,))
    db.commit()
    try:
        res = auth_client.post('/api/expenses/save', json={
            'v': 2, 'year': 2026, 'month': 1,
            'rows': [{'day': 1, 'description': 'Test', 'person_id': ben,
                     'category_id': None, 'amount': 10}],
            'deleted_ids': []
        }, headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        row = db.execute("SELECT person_id FROM expenses WHERE description='Test'").fetchone()
        assert row['person_id'] == anna, 'Client-gesendetes person_id wurde uebernommen statt ignoriert'
    finally:
        db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
        db.commit()


def test_bestehende_zeile_behaelt_ihre_person_beim_update(auth_client, csrf, db):
    """Serverseitig erzwungen: person_id einer BESTEHENDEN Zeile aendert
    sich nie, auch wenn der Client versucht, sie umzuschreiben."""
    db.execute("INSERT INTO persons (name) VALUES ('Anna'), ('Ben')")
    anna = db.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()['id']
    ben = db.execute("SELECT id FROM persons WHERE name='Ben'").fetchone()['id']
    cur = db.execute("INSERT INTO expenses (year,month,day,description,person_id,amount)"
                     " VALUES (2026,1,1,'Alt',?,5)", (anna,))
    db.commit()
    rid = cur.lastrowid
    res = auth_client.post('/api/expenses/save', json={
        'v': 2, 'year': 2026, 'month': 1,
        'rows': [{'id': rid, 'day': 1, 'description': 'Alt-geaendert',
                 'person_id': ben, 'category_id': None, 'amount': 5}],
        'deleted_ids': []
    }, headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    row = db.execute("SELECT person_id, description FROM expenses WHERE id=?", (rid,)).fetchone()
    assert row['person_id'] == anna, 'person_id einer Bestandszeile wurde veraendert'
    assert row['description'] == 'Alt-geaendert', 'andere Felder muessen weiterhin aenderbar bleiben'
