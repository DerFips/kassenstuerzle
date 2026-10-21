import pytest

from app.translations import TRANSLATIONS

SCHLUESSEL = [
    'month.move_to_income', 'month.move_to_expense', 'month.move_amount_missing',
    'month.move_busy', 'month.move_failed',
    'month.toast_moved_to_income', 'month.toast_moved_to_expense',
]


@pytest.fixture()
def person(db):
    """Person 'Alice', mit dem Test-Admin verknuepft (Aufraeumen ueber die db-Fixture)."""
    cur = db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (cur.lastrowid,))
    db.commit()
    return cur.lastrowid


def _post(client, csrf, url, **payload):
    body = {'v': 2, 'year': 2026, 'month': 9, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post(url, json=body, headers={'X-CSRF-Token': csrf})


@pytest.mark.parametrize('schluessel', SCHLUESSEL)
def test_umbuch_texte_in_beiden_sprachen(schluessel):
    for sprache in ('de', 'en'):
        assert TRANSLATIONS[sprache].get(schluessel), f'{schluessel} fehlt in {sprache}'


def test_monatsseite_hat_umbuch_buttons(auth_client, person):
    html = auth_client.get('/month/2026/9').get_data(as_text=True)
    # je ein Button in der Ausgaben- und der Einnahmenzeile ...
    assert html.count('js-row-move') >= 4, 'Button fehlt in einer der beiden Zeilenvorlagen oder Handlern'
    # ... und die Logik, die ihn bedient
    assert 'async function moveRow(' in html
    assert 'function darfUmbuchen(' in html


def test_umbuchung_vertrag_negative_ausgabe_wird_positive_einnahme(auth_client, csrf, db, person):
    """Der Server-Vertrag, auf dem moveRow aufbaut: eine negative Ausgabe ist
    speicherbar, die Einnahme mit dem positiven Betrag ebenfalls, und das
    Loeschen des Originals hinterlaesst genau eine Buchung."""
    exp_id = _post(auth_client, csrf, '/api/expenses/save', rows=[
        {'client_key': 'a', 'day': 3, 'description': 'Erstattung', 'category_id': None,
         'amount': -50.0}]).get_json()['id_map']['a']

    assert _post(auth_client, csrf, '/api/income/save', rows=[
        {'client_key': 'b', 'day': 3, 'description': 'Erstattung', 'category_id': None,
         'amount': 50.0}]).get_json()['success']
    assert _post(auth_client, csrf, '/api/expenses/save', deleted_ids=[exp_id]).get_json()['success']

    inc = db.execute('SELECT amount, person_id FROM income').fetchall()
    assert [(r['amount'], r['person_id']) for r in inc] == [(50.0, person)]
    assert db.execute('SELECT COUNT(*) c FROM expenses').fetchone()['c'] == 0
