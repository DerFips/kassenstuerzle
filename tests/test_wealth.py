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
