import pytest

from app.routes.main import WEALTH_TYPES


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
