"""Eingabepruefung der Schreib-Endpunkte (Code-Durchsicht November 2026).

Vorher nahmen die Endpunkte Monat, Jahr, Tag und Betrag ungeprueft an:

* Eine einzige Buchung mit Monat 13 liess /overview und /profile fuer ALLE
  dauerhaft mit einem Serverfehler antworten (MONTH_NAMES[12]).
* Ein Betrag von 1e999 (in JSON gueltig, in Python `inf`) landete in der
  Datenbank; jede Antwort mit den Summen dieses Monats war danach kein
  gueltiges JSON mehr (`Infinity`), die Monatsansicht also nicht mehr
  bedienbar.
* Falsche Typen (Text statt Zahl) fuehrten zu einem Serverfehler statt zu
  einer Meldung.
"""
import io
import json

import pytest


@pytest.fixture()
def person(db):
    cur = db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (cur.lastrowid,))
    db.commit()
    return cur.lastrowid


def _post(client, csrf, url, payload):
    return client.post(url, json=payload, headers={'X-CSRF-Token': csrf})


def _roh(client, csrf, url, text):
    """JSON als Rohtext - json.dumps schreibt `inf` als Infinity, 1e999 nie."""
    return client.post(url, data=text, content_type='application/json',
                       headers={'X-CSRF-Token': csrf})


def _zeile(**extra):
    row = {'client_key': 'c1', 'day': 3, 'description': 'Test', 'amount': 10.0}
    row.update(extra)
    return row


def _anzahl(db, tabelle):
    return db.execute(f'SELECT COUNT(*) FROM {tabelle}').fetchone()[0]


# ── Monat und Jahr ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('url,tabelle', [('/api/expenses/save', 'expenses'),
                                         ('/api/income/save', 'income')])
@pytest.mark.parametrize('jahr,monat', [(2026, 13), (2026, 0), (2026, -1),
                                        (99999, 5), (0, 5), ('abc', 5),
                                        (2026, None), (2026, [1])])
def test_speichern_weist_ungueltigen_monat_ab(auth_client, csrf, db, person,
                                              url, tabelle, jahr, monat):
    res = _post(auth_client, csrf, url, {'v': 2, 'year': jahr, 'month': monat,
                                         'rows': [_zeile()], 'deleted_ids': []})
    assert res.status_code == 400
    assert res.get_json()['success'] is False
    assert _anzahl(db, tabelle) == 0


@pytest.mark.parametrize('url', ['/api/expenses/save', '/api/income/save'])
def test_speichern_ohne_jahr_und_monat_ist_kein_serverfehler(auth_client, csrf,
                                                             db, person, url):
    res = _post(auth_client, csrf, url, {'v': 2, 'rows': [_zeile()]})
    assert res.status_code == 400


@pytest.mark.parametrize('payload', [
    {'year': 2026, 'month': 13, 'settled': True},
    {'year': 'x', 'month': 5, 'settled': True},
    {'year': 2026, 'settled': True},
    {'month': 5},
    [],
    'text',
])
def test_abrechnen_weist_ungueltige_eingabe_ab(auth_client, csrf, db, payload):
    res = _post(auth_client, csrf, '/api/month/settle', payload)
    assert res.status_code == 400
    assert _anzahl(db, 'month_status') == 0


def test_abrechnen_ohne_json_ist_kein_serverfehler(auth_client, csrf, db):
    res = auth_client.post('/api/month/settle', data='kein json',
                           content_type='text/plain',
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 400


def test_abrechnen_gueltig_funktioniert_weiter(auth_client, csrf, db):
    res = _post(auth_client, csrf, '/api/month/settle',
                {'year': 2026, 'month': 9, 'settled': True})
    assert res.status_code == 200
    assert res.get_json()['settled'] is True
    # Auch als Text (wie aus einem Formular) - int() nahm das schon immer an.
    res = _post(auth_client, csrf, '/api/month/settle',
                {'year': '2026', 'month': '9', 'settled': False})
    assert res.status_code == 200
    row = db.execute('SELECT year, month, settled FROM month_status').fetchall()
    assert [tuple(r) for r in row] == [(2026, 9, 0)]


def test_monatsansicht_mit_monat_13_ist_404(auth_client):
    assert auth_client.get('/month/2026/13').status_code == 404
    assert auth_client.get('/month/2026/0').status_code == 404
    assert auth_client.get('/month/99999/5').status_code == 404
    assert auth_client.get('/month/2026/9').status_code == 200


def test_wiederkehrende_fuer_monat_13_ist_404(auth_client):
    assert auth_client.get('/api/recurring/for-month/2026/13').status_code == 404
    assert auth_client.get('/api/recurring/for-month/2026/9').status_code == 200


def test_uebersicht_und_profil_ueberleben_altdaten_mit_monat_13(auth_client, db, person):
    """Bestehende Datenbanken koennen solche Zeilen schon enthalten (frueherer
    CSV-Import). Sie duerfen die Auswertung nicht fuer alle lahmlegen."""
    db.execute("INSERT INTO categories (name, color, is_split) VALUES ('Miete', '#aa0000', 1)")
    cid = db.execute('SELECT id FROM categories').fetchone()[0]
    for monat in (9, 13, 0):
        db.execute('INSERT INTO expenses (year,month,day,description,person_id,'
                   'category_id,amount) VALUES (2026,?,1,?,?,?,100)',
                   (monat, 'Zeile %d' % monat, person, cid))
        db.execute('INSERT INTO income (year,month,day,description,person_id,amount)'
                   ' VALUES (2026,?,1,?,?,50)', (monat, 'Lohn %d' % monat, person))
    db.execute("INSERT INTO month_status (year,month,settled,settled_at)"
               " VALUES (2026,13,1,'01.01.2027 10:00')")
    db.commit()
    for pfad in ('/overview', '/profile', '/api/chart-data', '/api/calendar/export',
                 '/search?q=Zeile'):
        assert auth_client.get(pfad).status_code == 200, pfad


# ── Betrag ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('url,tabelle', [('/api/expenses/save', 'expenses'),
                                         ('/api/income/save', 'income')])
@pytest.mark.parametrize('betrag', ['1e999', '-1e999', '1e12', '"inf"', '"nan"',
                                    '"Infinity"'])
def test_speichern_weist_unendlichen_betrag_ab(auth_client, csrf, db, person,
                                               url, tabelle, betrag):
    text = ('{"v":2,"year":2026,"month":9,"deleted_ids":[],"rows":[{"client_key":"c1",'
            '"day":3,"description":"Test","amount":%s}]}' % betrag)
    res = _roh(auth_client, csrf, url, text)
    assert res.status_code == 400
    assert res.get_json()['success'] is False
    assert _anzahl(db, tabelle) == 0


@pytest.mark.parametrize('url,tabelle', [('/api/expenses/save', 'expenses'),
                                         ('/api/income/save', 'income')])
def test_ungueltige_zeile_weist_den_ganzen_request_ab(auth_client, csrf, db, person,
                                                      url, tabelle):
    """Nichts halb speichern: die gueltige Zeile davor darf nicht landen."""
    text = ('{"v":2,"year":2026,"month":9,"deleted_ids":[],"rows":['
            '{"client_key":"a","day":1,"description":"gut","amount":5},'
            '{"client_key":"b","day":2,"description":"kaputt","amount":1e999}]}')
    assert _roh(auth_client, csrf, url, text).status_code == 400
    assert _anzahl(db, tabelle) == 0


def test_antwort_bleibt_gueltiges_json(auth_client, csrf, db, person):
    """Der eigentliche Schaden: `Infinity` in der Antwort ist kein JSON."""
    res = _post(auth_client, csrf, '/api/expenses/save',
                {'v': 2, 'year': 2026, 'month': 9, 'deleted_ids': [],
                 'rows': [_zeile(amount=12.5)]})
    assert res.status_code == 200
    json.loads(res.get_data(as_text=True), parse_constant=lambda c: pytest.fail(c))


def test_negativer_und_grosser_betrag_bleiben_erlaubt(auth_client, csrf, db, person):
    res = _post(auth_client, csrf, '/api/expenses/save',
                {'v': 2, 'year': 2026, 'month': 9, 'deleted_ids': [],
                 'rows': [_zeile(client_key='a', amount=-24.95),
                          _zeile(client_key='b', amount=999999999.99),
                          _zeile(client_key='c', amount='41,20' if False else '41.20')]})
    assert res.status_code == 200
    betraege = sorted(r[0] for r in db.execute('SELECT amount FROM expenses'))
    assert betraege == [-24.95, 41.2, 999999999.99]


# ── Tag und uebrige Typen ────────────────────────────────────────────────────

@pytest.mark.parametrize('url,tabelle', [('/api/expenses/save', 'expenses'),
                                         ('/api/income/save', 'income')])
@pytest.mark.parametrize('feld,wert', [('day', 'abc'), ('day', 0), ('day', 32),
                                       ('day', -3), ('day', [1]),
                                       ('category_id', 'abc'), ('category_id', [1])])
def test_speichern_weist_falsche_typen_ab(auth_client, csrf, db, person,
                                          url, tabelle, feld, wert):
    res = _post(auth_client, csrf, url, {'v': 2, 'year': 2026, 'month': 9,
                                         'deleted_ids': [], 'rows': [_zeile(**{feld: wert})]})
    assert res.status_code == 400
    assert _anzahl(db, tabelle) == 0


@pytest.mark.parametrize('url,tabelle', [('/api/expenses/save', 'expenses'),
                                         ('/api/income/save', 'income')])
@pytest.mark.parametrize('kategorie', [987654, 1e30, '987654'])
def test_geloeschte_kategorie_wird_leer_statt_serverfehler(auth_client, csrf, db, person,
                                                           url, tabelle, kategorie):
    """Ein offener Tab kennt noch eine Kategorie, die inzwischen geloescht ist.
    Frueher scheiterte das Speichern am Fremdschluessel (Serverfehler); die
    Datenbank setzt solche Zeilen beim Loeschen selbst auf "keine Kategorie"."""
    res = _post(auth_client, csrf, url, {'v': 2, 'year': 2026, 'month': 9,
                                         'deleted_ids': [], 'rows': [_zeile(category_id=kategorie)]})
    assert res.status_code == 200
    assert db.execute(f'SELECT category_id FROM {tabelle}').fetchone()[0] is None


def test_ausgabe_weist_falsches_bezahlt_fuer_ab(auth_client, csrf, db, person):
    res = _post(auth_client, csrf, '/api/expenses/save',
                {'v': 2, 'year': 2026, 'month': 9, 'deleted_ids': [],
                 'rows': [_zeile(paid_for_person_id='abc')]})
    assert res.status_code == 400
    assert _anzahl(db, 'expenses') == 0


@pytest.mark.parametrize('url', ['/api/expenses/save', '/api/income/save'])
@pytest.mark.parametrize('rows', ['text', {'a': 1}, [1, 2], ['x'], [None]])
def test_speichern_weist_kaputte_zeilenliste_ab(auth_client, csrf, db, person, url, rows):
    res = _post(auth_client, csrf, url, {'v': 2, 'year': 2026, 'month': 9,
                                         'rows': rows, 'deleted_ids': []})
    assert res.status_code == 400


def test_leerer_tag_wird_weiter_zu_1(auth_client, csrf, db, person):
    """Bisheriges Verhalten: ein leeres Tagesfeld speichert Tag 1."""
    res = _post(auth_client, csrf, '/api/expenses/save',
                {'v': 2, 'year': 2026, 'month': 9, 'deleted_ids': [],
                 'rows': [_zeile(day='')]})
    assert res.status_code == 200
    assert db.execute('SELECT day FROM expenses').fetchone()[0] == 1


def test_beschreibung_wird_als_text_gespeichert(auth_client, csrf, db, person):
    """Eine Liste als Beschreibung war ein Serverfehler (sqlite kann sie nicht binden)."""
    res = _post(auth_client, csrf, '/api/expenses/save',
                {'v': 2, 'year': 2026, 'month': 9, 'deleted_ids': [],
                 'rows': [_zeile(description=['a', 'b'])]})
    assert res.status_code == 400


# ── Wiederkehrende Ausgaben ──────────────────────────────────────────────────

def _vorlage(**extra):
    row = {'client_key': 'r1', 'day': 1, 'description': 'Miete', 'amount': 800,
           'frequency': 'monthly', 'start_month': 1}
    row.update(extra)
    return row


@pytest.mark.parametrize('feld,wert', [('day', 'abc'), ('day', 40), ('category_id', 'x'),
                                       ('frequency', 'taeglich'), ('frequency', 5),
                                       ('amount', 'inf')])
def test_vorlage_weist_ungueltige_werte_ab(auth_client, csrf, db, person, feld, wert):
    res = _post(auth_client, csrf, '/api/recurring/save',
                {'v': 2, 'rows': [_vorlage(**{feld: wert})], 'deleted_ids': []})
    assert res.status_code == 400
    assert _anzahl(db, 'recurring_expenses') == 0


@pytest.mark.parametrize('startmonat', ['abc', 0, 13, [1]])
def test_vorlage_weist_ungueltigen_startmonat_ab(auth_client, csrf, db, person, startmonat):
    res = _post(auth_client, csrf, '/api/recurring/save',
                {'v': 2, 'deleted_ids': [],
                 'rows': [_vorlage(frequency='yearly', start_month=startmonat)]})
    assert res.status_code == 400
    assert _anzahl(db, 'recurring_expenses') == 0


def test_vorlage_gueltig_funktioniert_weiter(auth_client, csrf, db, person):
    res = _post(auth_client, csrf, '/api/recurring/save',
                {'v': 2, 'deleted_ids': [],
                 'rows': [_vorlage(), _vorlage(client_key='r2', frequency='quarterly',
                                               start_month=2, description='GEZ')]})
    assert res.status_code == 200
    assert _anzahl(db, 'recurring_expenses') == 2


# ── Personen: Startbetrag der Geldboerse ─────────────────────────────────────

def test_person_weist_unendlichen_startbetrag_ab(auth_client, csrf, db, person):
    text = ('{"v":2,"deleted_ids":[],"rows":[{"id":%d,"name":"Alice",'
            '"wallet_start_date":"2026-01","wallet_start_amount":1e999}]}' % person)
    res = _roh(auth_client, csrf, '/api/persons/save', text)
    assert res.status_code == 400
    assert db.execute('SELECT wallet_start_amount FROM persons').fetchone()[0] == 0


# ── CSV-Import ───────────────────────────────────────────────────────────────

def _csv(client, csrf, zeilen):
    kopf = 'Typ;Jahr;Monat;Tag;Person;Kategorie;Beschreibung;Betrag;Bar\n'
    return client.post('/api/import/csv',
                       data={'file': (io.BytesIO((kopf + '\n'.join(zeilen)).encode()), 'x.csv')},
                       headers={'X-CSRF-Token': csrf}, content_type='multipart/form-data')


def test_csv_ueberspringt_ungueltige_zeilen(auth_client, csrf, db, person):
    res = _csv(auth_client, csrf, [
        'Ausgabe;2026;9;3;Alice;;gut;12,50;Nein',
        'Ausgabe;2026;13;3;Alice;;Monat 13;10;Nein',
        'Ausgabe;2026;0;3;Alice;;Monat 0;10;Nein',
        'Ausgabe;2026;9;45;Alice;;Tag 45;10;Nein',
        'Ausgabe;2026;9;3;Alice;;unendlich;inf;Nein',
        'Einnahme;2026;9;3;Alice;;nan;nan;Nein',
        'Einnahme;99999;9;3;Alice;;Jahr;10;Nein',
    ])
    body = res.get_json()
    assert body['success'] is True
    assert body['imported'] == 1
    assert body['skipped'] == 6
    assert body['months'] == 1, 'ungueltige Monate zaehlen nicht als betroffen'
    assert [tuple(r) for r in db.execute('SELECT month, description FROM expenses')] == [(9, 'gut')]
    assert _anzahl(db, 'income') == 0


def test_csv_ueberschreiben_loescht_keinen_monat_fuer_ungueltige_zeilen(auth_client, csrf, db, person):
    """Mit "ueberschreiben" wird jeder betroffene Monat vorher geleert. Eine
    Zeile, die danach gar nicht importiert wird, darf dafuer nicht genuegen."""
    db.execute('INSERT INTO expenses (year,month,day,description,person_id,amount)'
               ' VALUES (2026,9,1,?,?,10)', ('bleibt', person))
    db.commit()
    kopf = 'Typ;Jahr;Monat;Tag;Person;Kategorie;Beschreibung;Betrag;Bar\n'
    res = auth_client.post(
        '/api/import/csv',
        data={'file': (io.BytesIO((kopf + 'Ausgabe;2026;9;3;Alice;;kaputt;inf;Nein').encode()), 'x.csv'),
              'overwrite': 'true'},
        headers={'X-CSRF-Token': csrf}, content_type='multipart/form-data')
    assert res.get_json()['imported'] == 0
    assert [r[0] for r in db.execute('SELECT description FROM expenses')] == ['bleibt']


# ── PDF-Import ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('aenderung', [{'month': 13}, {'month': 0}, {'day': 0},
                                       {'day': 40}, {'year': 99999},
                                       {'amount': 'inf'}, {'amount': 'nan'},
                                       {'amount': 1e13}])
def test_pdf_uebernahme_weist_ungueltige_buchung_ab(auth_client, csrf, db, person, aenderung):
    gut = {'include': True, 'year': 2026, 'month': 9, 'day': 3,
           'description': 'gut', 'amount': 5.0}
    kaputt = dict(gut, description='kaputt', **aenderung)
    res = _post(auth_client, csrf, '/api/import/pdf/commit',
                {'transactions': [gut, kaputt]})
    assert res.status_code == 400
    assert _anzahl(db, 'expenses') == 0, 'auch die gueltige Zeile davor darf nicht landen'
