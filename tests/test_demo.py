"""Demo-Modus: Sperr-Waechter, Banner und Anmelde-Anleitung.

Der Demo-Modus liegt in app.config['DEMO_MODE'] und wird bei jeder Anfrage neu
gelesen, deshalb schaltet die Fixture ihn nur um und stellt ihn danach zurueck.
"""
import pytest

from app.translations import TRANSLATIONS

MELDUNG = 'Im Demo-Modus nicht verfügbar.'


@pytest.fixture()
def demo_modus(app):
    alt = (app.config.get('DEMO_MODE'), app.config.get('DEMO_PASSWORD'))
    app.config['DEMO_MODE'] = True
    app.config['DEMO_PASSWORD'] = 'demo1234'
    yield
    app.config['DEMO_MODE'], app.config['DEMO_PASSWORD'] = alt


@pytest.fixture()
def person(db):
    """Person 'Alice', mit dem Test-Admin verknuepft (Muster aus
    tests/test_save_expenses.py)."""
    cur = db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (cur.lastrowid,))
    db.commit()
    return cur.lastrowid


def _body(**kw):
    body = {'v': 2, 'rows': [], 'deleted_ids': []}
    body.update(kw)
    return body


GESPERRT = [
    ('post', '/change_password'),
    ('post', '/password/aendern'),
    ('post', '/api/users'),
    ('post', '/api/users/1'),
    ('delete', '/api/users/1'),
    ('post', '/api/users/1/reset-password'),
    ('post', '/api/setup/profiles/self'),
    ('post', '/api/setup/profiles/complete'),
    ('post', '/api/backup/create'),
    ('get', '/api/backup/list'),
    ('get', '/api/backup/download/x.db'),
    ('post', '/api/backup/restore'),
    ('delete', '/api/backup/delete/x.db'),
    ('post', '/api/import/csv'),
    ('post', '/api/import/pdf/parse'),
    ('post', '/api/import/pdf/commit'),
    ('post', '/api/import/receipt-parse'),
    ('post', '/api/receipt/upload'),
    ('delete', '/api/receipt/x.jpg'),
    ('post', '/api/notifications/register'),
    ('post', '/api/notifications/unregister'),
]


@pytest.mark.parametrize('methode,url', GESPERRT)
def test_gesperrte_endpunkte_antworten_mit_demo_meldung(auth_client, csrf, demo_modus,
                                                        methode, url):
    res = getattr(auth_client, methode)(url, json={}, headers={'X-CSRF-Token': csrf})
    assert res.status_code == 403
    if url.startswith('/api/'):
        data = res.get_json()
        assert data['success'] is False
        assert data['error'] == MELDUNG
    else:
        assert MELDUNG in res.get_data(as_text=True)


def test_jeder_gesperrte_endpunkt_ist_abgedeckt(app):
    """Die Liste der Beispiel-URLs deckt genau die Endpunktnamen der Sperrliste ab."""
    from app.routes.main import DEMO_GESPERRT
    adapter = app.url_map.bind('localhost')
    gefunden = set()
    for methode, url in GESPERRT:
        endpoint, _ = adapter.match(url, method=methode.upper())
        gefunden.add(endpoint)
    assert gefunden == set(DEMO_GESPERRT)
    assert len(DEMO_GESPERRT) == 21


def test_formular_und_seite_zeigen_die_sperrseite(auth_client, csrf, demo_modus):
    res = auth_client.post('/change_password',
                           data={'old_password': 'x', 'new_password': 'y', 'csrf_token': csrf})
    assert res.status_code == 403
    html = res.get_data(as_text=True)
    assert MELDUNG in html
    assert 'href="/settings"' in html

    res = auth_client.get('/password/aendern')
    assert res.status_code == 403
    html = res.get_data(as_text=True)
    assert MELDUNG in html
    assert 'href="/settings"' in html


def test_persons_anlegen_und_loeschen_gesperrt_aendern_erlaubt(auth_client, csrf, db,
                                                                person, demo_modus):
    h = {'X-CSRF-Token': csrf}
    res = auth_client.post('/api/persons/save', headers=h,
                           json=_body(rows=[{'client_key': 'n', 'name': 'Neu'}]))
    assert res.status_code == 403
    assert res.get_json()['error'] == MELDUNG
    assert db.execute("SELECT COUNT(*) c FROM persons WHERE name='Neu'").fetchone()['c'] == 0

    res = auth_client.post('/api/persons/save', headers=h, json=_body(deleted_ids=[1]))
    assert res.status_code == 403
    assert res.get_json()['error'] == MELDUNG

    res = auth_client.post('/api/persons/save', headers=h, json=_body(
        rows=[{'id': person, 'name': 'Alice', 'wallet_start_amount': 50}]))
    assert res.status_code == 200
    assert db.execute('SELECT wallet_start_amount a FROM persons WHERE id=?',
                      (person,)).fetchone()['a'] == 50


def test_kernfunktionen_bleiben_nutzbar(auth_client, csrf, person, demo_modus):
    h = {'X-CSRF-Token': csrf}
    res = auth_client.post('/api/expenses/save', headers=h, json=_body(
        year=2026, month=9,
        rows=[{'client_key': 'c1', 'day': 3, 'description': 'Einkauf',
               'person_id': person, 'amount': 12.5}]))
    assert res.status_code == 200
    res = auth_client.post('/api/wealth/accounts/save', headers=h, json=_body())
    assert res.status_code == 200
    assert auth_client.get('/api/users').status_code == 200


def test_ohne_demo_modus_ist_nichts_gesperrt(app, auth_client, csrf):
    assert not app.config.get('DEMO_MODE')
    assert auth_client.get('/api/backup/list').status_code == 200
    res = auth_client.post('/change_password',
                           data={'old_password': 'falsch', 'new_password': 'NeuesPasswort123!',
                                 'csrf_token': csrf})
    assert res.status_code != 403
    assert 'Demo-Modus' not in res.get_data(as_text=True)


def test_banner_nur_im_demo_modus(app, auth_client):
    html = auth_client.get('/overview').get_data(as_text=True)
    assert 'id="demo-banner"' not in html
    assert '04:00' not in html

    app.config['DEMO_MODE'] = True
    try:
        html = auth_client.get('/overview').get_data(as_text=True)
    finally:
        app.config['DEMO_MODE'] = False
    assert 'id="demo-banner"' in html
    assert '04:00' in html


def test_anmelde_kasten_nur_im_demo_modus(client, app, demo_modus):
    html = client.get('/login').get_data(as_text=True)
    assert 'id="demo-login-box"' in html
    assert 'anna' in html and 'ben' in html
    assert 'als Passwort demo1234, und' in html
    assert 'Administrator' in html and 'Mitglied' in html

    app.config['DEMO_MODE'] = False
    html = client.get('/login').get_data(as_text=True)
    assert 'id="demo-login-box"' not in html
    assert 'demo1234' not in html
    # Der Rohtext steht (mit Platzhalter) im i18n-Woerterbuch jeder Seite; der
    # gerenderte Satz mit dem Passwort darf ohne Demo-Modus nirgends stehen.
    assert 'Passwort demo1234' not in html


def test_demo_passwort_wird_maskiert(app, client, demo_modus):
    app.config['DEMO_PASSWORD'] = '<b>x</b>'
    html = client.get('/login').get_data(as_text=True)
    assert '&lt;b&gt;x&lt;/b&gt;' in html
    assert '<b>x</b>' not in html


def test_demo_texte_in_beiden_sprachen():
    for key in ('srv.demo_disabled', 'demo.banner', 'demo.login_title',
                'demo.login_steps', 'demo.login_anna', 'demo.login_ben',
                'demo.login_tip'):
        for lang in ('de', 'en'):
            assert TRANSLATIONS[lang].get(key), (lang, key)
