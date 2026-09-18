import pytest


@pytest.fixture()
def person(db):
    cur = db.execute("INSERT INTO persons (name) VALUES ('Bob')")
    db.commit()
    return cur.lastrowid


def _save(client, csrf, **payload):
    body = {'v': 2, 'year': 2026, 'month': 9, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/income/save', json=body, headers={'X-CSRF-Token': csrf})


def test_legacy_payload_rejected(auth_client, csrf):
    res = auth_client.post('/api/income/save',
                           json={'year': 2026, 'month': 9, 'income': []},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409


def test_insert_and_update(auth_client, csrf, db, person):
    rid = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'Gehalt',
         'person_id': person, 'amount': 2500.0}]).get_json()['id_map']['c1']

    _save(auth_client, csrf, rows=[
        {'id': rid, 'day': 1, 'description': 'Gehalt Maerz',
         'person_id': person, 'amount': 2600.0}])

    rows = db.execute('SELECT * FROM income WHERE year=2026 AND month=9').fetchall()
    assert len(rows) == 1
    assert rows[0]['description'] == 'Gehalt Maerz'
    assert rows[0]['amount'] == pytest.approx(2600.0)


def test_missing_row_is_not_deleted(auth_client, csrf, db, person):
    keep = _save(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'von User 1',
         'person_id': person, 'amount': 100.0}]).get_json()['id_map']['a']

    _save(auth_client, csrf, rows=[
        {'client_key': 'b', 'day': 2, 'description': 'von User 2',
         'person_id': person, 'amount': 200.0}])

    ids = {r['id'] for r in db.execute('SELECT id FROM income').fetchall()}
    assert keep in ids
    assert len(ids) == 2


def test_explicit_delete_and_zero_amount(auth_client, csrf, db, person):
    a = _save(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'a',
         'person_id': person, 'amount': 10.0}]).get_json()['id_map']['a']
    b = _save(auth_client, csrf, rows=[
        {'client_key': 'b', 'day': 2, 'description': 'b',
         'person_id': person, 'amount': 20.0}]).get_json()['id_map']['b']

    _save(auth_client, csrf, deleted_ids=[a],
          rows=[{'id': b, 'day': 2, 'description': 'b', 'person_id': person, 'amount': 0}])
    assert db.execute('SELECT COUNT(*) c FROM income').fetchone()['c'] == 0


def test_update_cannot_cross_month_boundary(auth_client, csrf, db, person):
    rid = _save(auth_client, csrf, rows=[
        {'client_key': 'c1', 'day': 1, 'description': 'September',
         'person_id': person, 'amount': 5.0}]).get_json()['id_map']['c1']

    body = {'v': 2, 'year': 2026, 'month': 10, 'rows': [
        {'id': rid, 'day': 9, 'description': 'gekapert', 'person_id': person, 'amount': 99.0}],
        'deleted_ids': []}
    auth_client.post('/api/income/save', json=body, headers={'X-CSRF-Token': csrf})
    row = db.execute('SELECT * FROM income WHERE id=?', (rid,)).fetchone()
    assert row['description'] == 'September'
