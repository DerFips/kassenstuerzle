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
