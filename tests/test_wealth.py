import pytest

from app.routes.main import WEALTH_TYPES, _wealth_data


@pytest.fixture()
def person(db):
    """Person 'Alice', mit dem Test-Admin verknuepft (wie in test_save_expenses).
    Aufgeraeumt ueber die db-Fixture."""
    cur = db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (cur.lastrowid,))
    db.commit()
    return cur.lastrowid


def _konto(db, person_id, name, typ):
    cur = db.execute(
        'INSERT INTO wealth_accounts (person_id, name, type) VALUES (?, ?, ?)',
        (person_id, name, typ))
    db.commit()
    return cur.lastrowid


def _saldo(db, konto_id, datum, betrag):
    db.execute(
        'INSERT INTO wealth_balances (account_id, balance_date, balance) VALUES (?, ?, ?)',
        (konto_id, datum, betrag))
    db.commit()


def test_tabellen_existieren(db):
    namen = {r['name'] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'wealth_accounts' in namen
    assert 'wealth_balances' in namen


def test_wealth_types_zuordnung():
    assert len(WEALTH_TYPES) == 15
    assert sorted(k for k, v in WEALTH_TYPES.items() if v == 'debt') == [
        'credit_card', 'loan', 'mortgage', 'other_debt', 'overdraft']


def test_person_loeschen_loescht_konten_und_salden(db, person):
    konto = _konto(db, person, 'Giro', 'checking')
    _saldo(db, konto, '2026-01-31', 100.0)
    db.execute('DELETE FROM persons WHERE id=?', (person,))
    db.commit()
    assert db.execute('SELECT COUNT(*) FROM wealth_accounts').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM wealth_balances').fetchone()[0] == 0


def test_konto_loeschen_loescht_seine_salden(db, person):
    konto = _konto(db, person, 'Giro', 'checking')
    _saldo(db, konto, '2026-01-31', 100.0)
    _saldo(db, konto, '2026-02-28', 150.0)
    db.execute('DELETE FROM wealth_accounts WHERE id=?', (konto,))
    db.commit()
    assert db.execute('SELECT COUNT(*) FROM wealth_balances').fetchone()[0] == 0


def test_aktueller_saldo_juengster_eintrag(db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    _saldo(db, giro, '2026-01-31', 1000)
    _saldo(db, giro, '2026-03-31', 1500)
    daten = _wealth_data(db, person)
    assert daten['accounts'][0]['balance'] == 1500
    assert daten['accounts'][0]['balance_date'] == '2026-03-31'


def test_netto_mit_schulden(db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    hyp = _konto(db, person, 'Hypothek', 'mortgage')
    _saldo(db, giro, '2026-01-31', 1000)
    _saldo(db, hyp, '2026-01-31', 800)
    daten = _wealth_data(db, person)
    assert daten['total_assets'] == 1000
    assert daten['total_debts'] == 800
    assert daten['net_worth'] == 200
    assert [a['kind'] for a in daten['accounts']] == ['asset', 'debt']


def test_verlauf(db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    kredit = _konto(db, person, 'Kredit', 'loan')
    _saldo(db, giro, '2026-01-01', 1000)
    _saldo(db, giro, '2026-03-01', 1500)
    _saldo(db, kredit, '2026-02-01', 400)
    serie = _wealth_data(db, person)['series']
    assert [p['date'] for p in serie] == ['2026-01-01', '2026-02-01', '2026-03-01']
    assert [p['net_worth'] for p in serie] == [1000, 600, 1100]
    assert [p['assets'] for p in serie] == [1000, 1000, 1500]
    assert [p['debts'] for p in serie] == [0, 400, 400]


def test_konto_ohne_eintrag_zaehlt_null(db, person):
    _konto(db, person, 'Giro', 'checking')
    daten = _wealth_data(db, person)
    assert daten['accounts'][0]['balance'] == 0.0
    assert daten['accounts'][0]['balance_date'] is None
    assert daten['series'] == []


def test_leer(db, person):
    daten = _wealth_data(db, person)
    assert daten['total_assets'] == 0
    assert daten['total_debts'] == 0
    assert daten['net_worth'] == 0
    assert daten['accounts'] == []
    assert daten['series'] == []
    assert daten['entries'] == []


def test_nur_eigene_person(db, person):
    andere = db.execute("INSERT INTO persons (name) VALUES ('Bob')").lastrowid
    db.commit()
    fremd = _konto(db, andere, 'Fremdkonto', 'checking')
    _saldo(db, fremd, '2026-01-31', 999)
    eigen = _konto(db, person, 'Giro', 'checking')
    _saldo(db, eigen, '2026-02-28', 50)
    daten = _wealth_data(db, person)
    assert [a['name'] for a in daten['accounts']] == ['Giro']
    assert daten['total_assets'] == 50
    assert [p['date'] for p in daten['series']] == ['2026-02-28']
    assert [e['account_id'] for e in daten['entries']] == [eigen]


def test_rundung(db, person):
    a = _konto(db, person, 'A', 'checking')
    b = _konto(db, person, 'B', 'savings')
    _saldo(db, a, '2026-01-31', 0.1)
    _saldo(db, b, '2026-01-31', 0.2)
    daten = _wealth_data(db, person)
    assert daten['total_assets'] == 0.3
    assert daten['series'][0]['assets'] == 0.3


def test_eintraege_absteigend_nach_datum(db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    _saldo(db, giro, '2026-01-31', 1)
    _saldo(db, giro, '2026-03-31', 3)
    _saldo(db, giro, '2026-02-28', 2)
    eintraege = _wealth_data(db, person)['entries']
    assert [e['balance_date'] for e in eintraege] == [
        '2026-03-31', '2026-02-28', '2026-01-31']
    assert set(eintraege[0]) == {'account_id', 'balance_date', 'balance'}


# ── POST /api/wealth/accounts/save ────────────────────────────────────────────
def _post_konten(client, csrf, **payload):
    body = {'v': 2, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/wealth/accounts/save', json=body,
                       headers={'X-CSRF-Token': csrf})


def _fremde_person(db, name='Bob'):
    pid = db.execute('INSERT INTO persons (name) VALUES (?)', (name,)).lastrowid
    db.commit()
    return pid


def test_konten_anlegen_person_aus_session(auth_client, csrf, db, person):
    fremd = _fremde_person(db)
    res = _post_konten(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Giro', 'type': 'checking', 'person_id': fremd}])
    assert res.status_code == 200
    daten = res.get_json()
    assert daten['success'] is True
    kid = daten['id_map']['a']
    zeile = db.execute('SELECT person_id, name, type FROM wealth_accounts WHERE id=?',
                       (kid,)).fetchone()
    assert zeile['person_id'] == person
    assert (zeile['name'], zeile['type']) == ('Giro', 'checking')
    assert [a['id'] for a in daten['accounts']] == [kid]
    assert db.execute('SELECT COUNT(*) FROM wealth_accounts WHERE person_id=?',
                      (fremd,)).fetchone()[0] == 0


def test_konten_aendern_behaelt_person(auth_client, csrf, db, person):
    fremd = _fremde_person(db)
    kid = _konto(db, person, 'Giro', 'checking')
    res = _post_konten(auth_client, csrf, rows=[
        {'id': kid, 'name': 'Tagesgeld', 'type': 'savings', 'person_id': fremd}])
    assert res.status_code == 200
    zeile = db.execute('SELECT person_id, name, type FROM wealth_accounts WHERE id=?',
                       (kid,)).fetchone()
    assert (zeile['person_id'], zeile['name'], zeile['type']) == (
        person, 'Tagesgeld', 'savings')


def test_fremdes_konto_unantastbar(auth_client, csrf, db, person):
    fremd = _fremde_person(db)
    fremdes = _konto(db, fremd, 'Fremdkonto', 'checking')
    _saldo(db, fremdes, '2026-01-31', 500.0)

    res = _post_konten(auth_client, csrf, rows=[
        {'id': fremdes, 'name': 'Gekapert', 'type': 'loan'}])
    assert res.status_code == 200
    zeile = db.execute('SELECT person_id, name, type FROM wealth_accounts WHERE id=?',
                       (fremdes,)).fetchone()
    assert (zeile['person_id'], zeile['name'], zeile['type']) == (
        fremd, 'Fremdkonto', 'checking')

    res = _post_konten(auth_client, csrf, deleted_ids=[fremdes])
    assert res.status_code == 200
    zeile = db.execute('SELECT person_id, name FROM wealth_accounts WHERE id=?',
                       (fremdes,)).fetchone()
    assert zeile is not None
    assert (zeile['person_id'], zeile['name']) == (fremd, 'Fremdkonto')
    assert db.execute('SELECT COUNT(*) FROM wealth_balances WHERE account_id=?',
                      (fremdes,)).fetchone()[0] == 1
    # Die Antwort verraet nichts ueber das fremde Konto.
    assert res.get_json()['accounts'] == []


def test_ungueltige_zeilen_uebersprungen(auth_client, csrf, db, person):
    res = _post_konten(auth_client, csrf, rows=[
        {'client_key': 'ok', 'name': '  Giro  ', 'type': 'checking'},
        {'client_key': 'typ', 'name': 'X', 'type': 'xyz'},
        {'client_key': 'leer', 'name': '   ', 'type': 'checking'},
        {'client_key': 'lang', 'name': 'a' * 61, 'type': 'checking'},
        {'id': 'abc', 'name': 'Boese', 'type': 'checking'},
    ])
    assert res.status_code == 200
    assert list(res.get_json()['id_map']) == ['ok']
    namen = [r['name'] for r in db.execute('SELECT name FROM wealth_accounts')]
    assert namen == ['Giro']


def test_konto_ohne_person_400(auth_client, csrf, db):
    res = _post_konten(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': 'Giro', 'type': 'checking'}])
    assert res.status_code == 400
    assert db.execute('SELECT COUNT(*) FROM wealth_accounts').fetchone()[0] == 0


def test_alte_payload_409(auth_client, csrf, db, person):
    res = auth_client.post('/api/wealth/accounts/save',
                           json=[{'name': 'Giro', 'type': 'checking'}],
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409
    assert db.execute('SELECT COUNT(*) FROM wealth_accounts').fetchone()[0] == 0


def test_name_wird_woertlich_gespeichert(auth_client, csrf, db, person):
    name = '<img src=x onerror=alert(1)>${fetch(1)}'
    res = _post_konten(auth_client, csrf, rows=[
        {'client_key': 'a', 'name': name, 'type': 'checking'}])
    assert res.status_code == 200
    kid = res.get_json()['id_map']['a']
    assert db.execute('SELECT name FROM wealth_accounts WHERE id=?',
                      (kid,)).fetchone()['name'] == name


def _konten_namen(db):
    return [r['name'] for r in db.execute('SELECT name FROM wealth_accounts ORDER BY id')]


def test_ungueltige_ids_in_deleted_ids_werden_uebersprungen(auth_client, csrf, db, person):
    # Eigenes Konto mit id 1: True und 1.9 wuerden per int() darauf zeigen.
    db.execute("INSERT INTO wealth_accounts (id, person_id, name, type) "
               "VALUES (1, ?, 'Eigen', 'checking')", (person,))
    db.commit()
    res = _post_konten(auth_client, csrf,
                       deleted_ids=[10**30, True, 1.9, 'x', None],
                       rows=[{'client_key': 'ok', 'name': 'Neu', 'type': 'cash'}])
    assert res.status_code == 200
    assert 'ok' in res.get_json()['id_map']
    assert _konten_namen(db) == ['Eigen', 'Neu']


def test_zeile_mit_riesiger_id_wird_uebersprungen(auth_client, csrf, db, person):
    res = _post_konten(auth_client, csrf, rows=[
        {'id': 10**30, 'name': 'Riesig', 'type': 'checking'}])
    assert res.status_code == 200
    assert res.get_json()['id_map'] == {}
    assert _konten_namen(db) == []


def test_infinity_als_id_wird_uebersprungen(auth_client, csrf, db, person):
    eigen = _konto(db, person, 'Eigen', 'checking')
    body = ('{"v": 2, "rows": [{"id": Infinity, "name": "X", "type": "checking"}],'
            ' "deleted_ids": [Infinity, -Infinity]}')
    res = auth_client.post('/api/wealth/accounts/save', data=body,
                           content_type='application/json',
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    assert _konten_namen(db) == ['Eigen']
    assert db.execute('SELECT id FROM wealth_accounts').fetchone()['id'] == eigen
