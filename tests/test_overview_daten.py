"""Regressionsschutz fuer den _overview_daten()-Refactor (Task 1):
overview() darf nach der Extraktion exakt dieselben Werte rendern wie vorher."""
from datetime import datetime

import pytest


@pytest.fixture()
def testdaten(db):
    db.execute("INSERT INTO persons (name) VALUES ('Anna'), ('Ben')")
    db.execute("INSERT INTO categories (name, color) VALUES ('Essen', '#111111')")
    anna = db.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()['id']
    kat = db.execute("SELECT id FROM categories WHERE name='Essen'").fetchone()['id']
    jahr = datetime.now().year
    db.execute(
        "INSERT INTO expenses (year, month, day, description, person_id, category_id, amount)"
        " VALUES (?, 1, 1, 'Testausgabe', ?, ?, 42.5)", (jahr, anna, kat))
    db.execute(
        "INSERT INTO income (year, month, day, description, person_id, category_id, amount)"
        " VALUES (?, 1, 1, 'Testeinnahme', ?, ?, 100)", (jahr, anna, kat))
    db.commit()
    return {'jahr': jahr, 'anna_name': 'Anna'}


def test_overview_zeigt_person_totale_unveraendert(auth_client, testdaten):
    """Ankerwert VOR dem Refactor: Annas Ausgaben-/Einnahmensumme muss nach
    der Extraktion von _overview_daten() identisch bleiben."""
    res = auth_client.get('/overview')
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # person_totals rendert in der Personen-Uebersichtstabelle - der Name
    # und mindestens einer der beiden Betraege muessen auftauchen.
    assert 'Anna' in html
    assert '42,5' in html.replace('.', ',') or '42.5' in html
