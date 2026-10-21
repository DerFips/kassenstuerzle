from datetime import datetime, timedelta

import re

import pytest

from app.routes.main import WEALTH_TYPES, _wealth_data
from app.translations import TRANSLATIONS
from test_xss_namen import _template_literale


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


# ── POST /api/wealth/balances/save ────────────────────────────────────────────
def _post_salden(client, csrf, **payload):
    body = {'v': 2, 'entries': [], 'deleted': []}
    body.update(payload)
    return client.post('/api/wealth/balances/save', json=body,
                       headers={'X-CSRF-Token': csrf})


def _post_salden_roh(client, csrf, body):
    """Rohtext-Body, damit auch NaN/Infinity (kein gueltiges JSON, aber von
    Pythons Parser akzeptiert) ankommen."""
    return client.post('/api/wealth/balances/save', data=body,
                       content_type='application/json',
                       headers={'X-CSRF-Token': csrf})


def _salden(db):
    return [(r['account_id'], r['balance_date'], r['balance']) for r in db.execute(
        'SELECT account_id, balance_date, balance FROM wealth_balances '
        'ORDER BY account_id, balance_date')]


def test_saldo_upsert_gleicher_tag_ueberschreibt(auth_client, csrf, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    for betrag in (100, 150):
        res = _post_salden(auth_client, csrf, date='2026-10-01',
                           entries=[{'account_id': giro, 'balance': betrag}])
        assert res.status_code == 200
    assert _salden(db) == [(giro, '2026-10-01', 150)]


def test_saldo_negativ_bei_vermoegen_erlaubt(auth_client, csrf, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    res = _post_salden(auth_client, csrf, date='2026-10-01',
                       entries=[{'account_id': giro, 'balance': -20.5}])
    assert res.status_code == 200
    assert _salden(db) == [(giro, '2026-10-01', -20.5)]


def test_saldo_fremdes_konto_still_uebersprungen(auth_client, csrf, db, person):
    fremd = _fremde_person(db)
    fremdes = _konto(db, fremd, 'Fremd', 'checking')
    eigenes = _konto(db, person, 'Eigen', 'checking')
    _saldo(db, fremdes, '2026-01-31', 500)
    res = _post_salden(auth_client, csrf, date='2026-10-01', entries=[
        {'account_id': fremdes, 'balance': 1},
        {'account_id': eigenes, 'balance': 2}])
    assert res.status_code == 200
    # Nur der eigene Eintrag kommt dazu; am fremden Konto aendert sich nichts.
    assert _salden(db) == [(fremdes, '2026-01-31', 500),
                           (eigenes, '2026-10-01', 2)]


def test_saldo_loeschen_nur_eigene(auth_client, csrf, db, person):
    fremd = _fremde_person(db)
    fremdes = _konto(db, fremd, 'Fremd', 'checking')
    eigenes = _konto(db, person, 'Eigen', 'checking')
    _saldo(db, fremdes, '2026-01-31', 500)
    _saldo(db, eigenes, '2026-01-31', 100)
    res = _post_salden(auth_client, csrf, deleted=[
        {'account_id': fremdes, 'balance_date': '2026-01-31'},
        {'account_id': eigenes, 'balance_date': '2026-01-31'}])
    assert res.status_code == 200
    assert _salden(db) == [(fremdes, '2026-01-31', 500)]


@pytest.mark.parametrize('datum', ['2026-13-40', '21.10.2026', '', '2026-1-5', None, 20261021])
def test_ungueltiges_datum_400_nichts_geschrieben(auth_client, csrf, db, person, datum):
    giro = _konto(db, person, 'Giro', 'checking')
    res = _post_salden(auth_client, csrf, date=datum,
                       entries=[{'account_id': giro, 'balance': 10}])
    assert res.status_code == 400
    assert res.get_json()['success'] is False
    assert _salden(db) == []


def test_heute_erlaubt_morgen_abgewiesen(auth_client, csrf, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    heute = datetime.now().date()
    entry = [{'account_id': giro, 'balance': 10}]
    res = _post_salden(auth_client, csrf, date=heute.isoformat(), entries=entry)
    assert res.status_code == 200
    res = _post_salden(auth_client, csrf, date=(heute + timedelta(days=1)).isoformat(),
                       entries=entry)
    assert res.status_code == 400
    assert _salden(db) == [(giro, heute.isoformat(), 10)]


def test_datum_nur_bei_eintraegen_noetig(auth_client, csrf, db, person):
    res = _post_salden(auth_client, csrf, date='kaputt')
    assert res.status_code == 200
    res = _post_salden(auth_client, csrf)
    assert res.status_code == 200


@pytest.mark.parametrize('wert', ['NaN', 'Infinity', '-Infinity', '1e12', '-1e12',
                                  '1e300', '10000000000000000000000'])
def test_nan_infinity_zu_gross_400(auth_client, csrf, db, person, wert):
    giro = _konto(db, person, 'Giro', 'checking')
    body = ('{"v": 2, "date": "2026-10-01", "entries": ['
            '{"account_id": %d, "balance": 5}, {"account_id": %d, "balance": %s}]}'
            % (giro, giro, wert))
    res = _post_salden_roh(auth_client, csrf, body)
    assert res.status_code == 400
    assert _salden(db) == []


@pytest.mark.parametrize('wert', ['abc', None, True, False, [], {}, '12'])
def test_saldo_nicht_numerisch_400(auth_client, csrf, db, person, wert):
    giro = _konto(db, person, 'Giro', 'checking')
    res = _post_salden(auth_client, csrf, date='2026-10-01', entries=[
        {'account_id': giro, 'balance': 5}, {'account_id': giro, 'balance': wert}])
    assert res.status_code == 400
    assert _salden(db) == []


def test_saldo_ungueltig_bei_fremdem_konto_ebenfalls_400(auth_client, csrf, db, person):
    fremdes = _konto(db, _fremde_person(db), 'Fremd', 'checking')
    res = _post_salden(auth_client, csrf, date='2026-10-01',
                       entries=[{'account_id': fremdes, 'balance': 'abc'}])
    assert res.status_code == 400


def test_grenzwert_knapp_unter_1e12_erlaubt(auth_client, csrf, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    res = _post_salden(auth_client, csrf, date='2026-10-01',
                       entries=[{'account_id': giro, 'balance': 999999999999.99}])
    assert res.status_code == 200
    assert _salden(db) == [(giro, '2026-10-01', 999999999999.99)]


def test_account_id_ungueltig_uebersprungen(auth_client, csrf, db, person):
    _konto(db, person, 'Giro', 'checking')
    res = _post_salden(auth_client, csrf, date='2026-10-01', entries=[
        {'account_id': 'x', 'balance': 1},
        {'account_id': None, 'balance': 1},
        {'balance': 1},
        {'account_id': True, 'balance': 1},
        {'account_id': 1.0, 'balance': 1},
        {'account_id': 10**30, 'balance': 1},
        {'account_id': 0, 'balance': 1},
        'kein dict', None, 7])
    assert res.status_code == 200
    assert _salden(db) == []


def test_ungueltige_ids_und_paare_in_deleted_uebersprungen(auth_client, csrf, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    _saldo(db, giro, '2026-01-31', 100)
    body = ('{"v": 2, "deleted": [{"account_id": Infinity, "balance_date": "2026-01-31"},'
            ' {"account_id": %d, "balance_date": ["2026-01-31"]},'
            ' {"account_id": %d, "balance_date": null}, {"account_id": %d},'
            ' {"account_id": true, "balance_date": "2026-01-31"},'
            ' {"account_id": 1e30, "balance_date": "2026-01-31"}, 5, null]}'
            % (giro, giro, giro))
    res = _post_salden_roh(auth_client, csrf, body)
    assert res.status_code == 200
    assert _salden(db) == [(giro, '2026-01-31', 100)]


def test_antwort_enthaelt_vermoegensdaten(auth_client, csrf, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    hyp = _konto(db, person, 'Hypothek', 'mortgage')
    res = _post_salden(auth_client, csrf, date='2026-10-01', entries=[
        {'account_id': giro, 'balance': 1000.5}, {'account_id': hyp, 'balance': 400}])
    assert res.status_code == 200
    daten = res.get_json()
    assert daten['success'] is True
    assert daten['wealth']['net_worth'] == 600.5
    assert daten['wealth']['series'][-1]['date'] == '2026-10-01'


def test_salden_ohne_person_400(auth_client, csrf, db):
    res = _post_salden(auth_client, csrf, date='2026-10-01',
                       entries=[{'account_id': 1, 'balance': 1}])
    assert res.status_code == 400
    assert _salden(db) == []


def test_salden_alte_payload_409(auth_client, csrf, db, person):
    res = auth_client.post('/api/wealth/balances/save',
                           json=[{'account_id': 1, 'balance': 1}],
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409
    res = auth_client.post('/api/wealth/balances/save',
                           json={'date': '2026-10-01', 'entries': []},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 409


def test_salden_falsche_listentypen_409(auth_client, csrf, db, person):
    res = _post_salden(auth_client, csrf, entries={'a': 1})
    assert res.status_code == 409
    res = _post_salden(auth_client, csrf, deleted='x')
    assert res.status_code == 409


def test_client_person_id_wird_ignoriert(auth_client, csrf, db, person):
    fremdes = _konto(db, _fremde_person(db), 'Fremd', 'checking')
    res = _post_salden(auth_client, csrf, date='2026-10-01', person_id=person,
                       entries=[{'account_id': fremdes, 'balance': 1, 'person_id': person}])
    assert res.status_code == 200
    assert _salden(db) == []


# ── Profil: Kennzahl und Karte (serverseitig) ─────────────────────────────────
WEALTH_SCHLUESSEL = [
    'profile.stat_net_worth', 'profile.stat_assets', 'profile.stat_debts',
    'wealth.title', 'wealth.group_assets', 'wealth.group_debts',
    'wealth.add_account', 'wealth.save_accounts', 'wealth.account_name_placeholder',
    'wealth.date_label', 'wealth.save_balances', 'wealth.history_title',
    'wealth.history_empty', 'wealth.chart_title', 'wealth.no_accounts',
    'wealth.toast_accounts_saved', 'wealth.toast_balances_saved',
    'srv.wealth_invalid_date', 'srv.wealth_invalid_balance',
]


def _profil(client):
    res = client.get('/profile')
    assert res.status_code == 200
    return res.get_data(as_text=True)


def _kennzahl(html):
    """(Klassen, Text) des Elements #statNetWorth."""
    m = re.search(r'<div([^>]*\bid="statNetWorth"[^>]*)>([^<]*)</div>', html)
    assert m, 'Element #statNetWorth fehlt'
    return m.group(1), m.group(2).strip()


def test_uebersetzungsschluessel_in_beiden_sprachen():
    schluessel = WEALTH_SCHLUESSEL + [f'wealth.type.{k}' for k in WEALTH_TYPES]
    for sprache in ('de', 'en'):
        fehlend = [k for k in schluessel if not TRANSLATIONS[sprache].get(k)]
        assert fehlend == [], f'{sprache}: {fehlend}'


def test_profil_zeigt_nettovermoegen(auth_client, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    hyp = _konto(db, person, 'Hypothek', 'mortgage')
    _saldo(db, giro, '2026-01-31', 1000)
    _saldo(db, hyp, '2026-01-31', 800)
    klassen, text = _kennzahl(_profil(auth_client))
    assert text == '200.00 €'
    assert 'text-success' in klassen and 'text-danger' not in klassen

    _saldo(db, hyp, '2026-02-28', 1500)
    klassen, text = _kennzahl(_profil(auth_client))
    assert text == '-500.00 €'
    assert 'text-danger' in klassen and 'text-success' not in klassen


def test_profil_zeigt_vermoegen_und_schulden_klein(auth_client, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    hyp = _konto(db, person, 'Hypothek', 'mortgage')
    _saldo(db, giro, '2026-01-31', 1000)
    _saldo(db, hyp, '2026-01-31', 800)
    html = _profil(auth_client)
    assert re.search(r'id="statAssets"[^>]*>\s*1000\.00 €', html)
    assert re.search(r'id="statDebts"[^>]*>\s*800\.00 €', html)


def test_profil_ohne_konten_rendert(auth_client, db, person):
    html = _profil(auth_client)
    assert _kennzahl(html)[1] == '0.00 €'
    assert 'id="wealthCard"' in html


def test_profil_mit_einem_stichtag_rendert(auth_client, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    _saldo(db, giro, '2026-01-31', 50)
    html = _profil(auth_client)
    assert _kennzahl(html)[1] == '50.00 €'
    assert 'id="wealthChart"' in html and 'id="wealthHistory"' in html
    assert 'id="wealthDate"' in html


def test_negative_null_wird_nie_als_minus_null_angezeigt(auth_client, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    _saldo(db, giro, '2026-01-31', -0.0)
    html = _profil(auth_client)
    assert '-0.00' not in html
    assert _kennzahl(html)[1] == '0.00 €'


def test_fremde_konten_nicht_im_profil(auth_client, db, person):
    bob = _fremde_person(db)
    fremd = _konto(db, bob, 'GeheimesKontoBob', 'savings')
    _saldo(db, fremd, '2026-01-31', 123456.78)
    eigen = _konto(db, person, 'Giro', 'checking')
    _saldo(db, eigen, '2026-01-31', 10)
    html = _profil(auth_client)
    assert 'GeheimesKontoBob' not in html
    assert '123456.78' not in html
    assert _kennzahl(html)[1] == '10.00 €'


def test_namen_maskiert_und_nicht_im_template_literal(auth_client, db, person):
    name = '<img src=x onerror=1>${fetch(2)}'
    giro = _konto(db, person, name, 'checking')
    _saldo(db, giro, '2026-01-31', 5)
    html = _profil(auth_client)
    assert '<img src=x onerror=1>' not in html
    assert '&lt;img' in html
    assert not [l for l in _template_literale(html) if '${fetch(2)}' in l]


def test_konten_zeilen_mit_ganzzahligen_ids(auth_client, db, person):
    giro = _konto(db, person, 'Giro', 'checking')
    hyp = _konto(db, person, 'Hypothek', 'mortgage')
    html = _profil(auth_client)
    zeilen = re.findall(r'class="[^"]*\bwealth-row\b[^"]*"[^>]*\bdata-id="([^"]*)"', html)
    assert sorted(zeilen) == sorted([str(giro), str(hyp)])
    for knopf in ('wealthAddAccountBtn', 'wealthSaveAccountsBtn',
                  'wealthSaveBalancesBtn', 'wealthAccountsList', 'statNetWorth'):
        assert f'id="{knopf}"' in html


def test_unverknuepft_keine_karte(auth_client, db):
    db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
    db.commit()
    html = _profil(auth_client)
    assert 'id="wealthCard"' not in html
    assert 'id="statNetWorth"' not in html
