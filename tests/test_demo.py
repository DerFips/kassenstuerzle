"""Demo-Modus: Sperr-Waechter, Banner und Anmelde-Anleitung.

Der Demo-Modus liegt in app.config['DEMO_MODE'] und wird bei jeder Anfrage neu
gelesen, deshalb schaltet die Fixture ihn nur um und stellt ihn danach zurueck.
"""
import logging
import os
import pathlib
import shutil
import signal
import subprocess

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


def _zaehler_leeren():
    from app.routes import main as m
    m._login_attempts.clear()
    m._ip_attempts.clear()


def test_demo_anmeldung_wird_nicht_gesperrt(client, demo_modus):
    """Die Zugangsdaten sind oeffentlich: weder die Sperre je Benutzername noch
    die je IP darf in der Demo greifen (sonst kann jeder alle aussperren)."""
    from app.routes import main as m
    _zaehler_leeren()
    try:
        for _ in range(m._MAX_LOGIN_ATTEMPTS + 1):
            client.post('/login', data={'username': 'admin', 'password': 'falsch'})
        res = client.post('/login', data={'username': 'admin',
                                          'password': 'test-password-123'})
        assert res.status_code == 302
        assert 'Demo' not in res.get_data(as_text=True)

        # Wechselnde Benutzernamen: so greift nur die IP-Sperre, nicht die je Konto
        for i in range(m._MAX_IP_ATTEMPTS + 1):
            client.post('/login', data={'username': f'x{i}', 'password': 'falsch'})
        res = client.post('/login', data={'username': 'admin',
                                          'password': 'test-password-123'})
        assert res.status_code == 302
        # Nichts davon wurde gezaehlt
        assert not m._login_attempts or m._login_attempts['admin']['count'] == 0
        assert all(v['count'] == 0 and v['locked_until'] == 0
                   for v in m._ip_attempts.values())
    finally:
        _zaehler_leeren()


def test_ohne_demo_modus_bleibt_die_sperre_aktiv(app, client):
    from app.routes import main as m
    assert not app.config.get('DEMO_MODE')
    _zaehler_leeren()
    try:
        for _ in range(m._MAX_LOGIN_ATTEMPTS):
            client.post('/login', data={'username': 'admin', 'password': 'falsch'})
        res = client.post('/login', data={'username': 'admin',
                                          'password': 'test-password-123'})
        assert res.status_code == 200, 'Sperre muss ausserhalb der Demo greifen'
    finally:
        _zaehler_leeren()


# ── Taeglicher Reset (Task 2) ────────────────────────────────────────────────

def _feld(job, name):
    return next(str(f) for f in job.trigger.fields if f.name == name)


@pytest.mark.parametrize('stunde,minute', [(4, 0), (6, 30)])
def test_reset_job_steht_standardmaessig_auf_vier_uhr(stunde, minute):
    from apscheduler.schedulers.background import BackgroundScheduler
    from app.demo import schedule_demo_reset
    s = BackgroundScheduler()  # bewusst nicht gestartet
    schedule_demo_reset(s, stunde, minute)
    job = s.get_job('demo_reset')
    assert job is not None
    assert _feld(job, 'hour') == str(stunde)
    assert _feld(job, 'minute') == str(minute)


def test_reset_beendet_im_container_den_gunicorn_master(monkeypatch):
    from app.demo import demo_reset
    aufrufe = []
    monkeypatch.setenv('KASSENSTUERZLE_IM_CONTAINER', '1')
    monkeypatch.setattr(os, 'getppid', lambda: 4242)
    monkeypatch.setattr(os, 'kill', lambda pid, sig: aufrufe.append((pid, sig)))
    demo_reset()
    assert aufrufe == [(4242, signal.SIGTERM)]


def test_reset_tut_ausserhalb_des_containers_nichts(monkeypatch, caplog):
    from app.demo import demo_reset
    aufrufe = []
    monkeypatch.delenv('KASSENSTUERZLE_IM_CONTAINER', raising=False)
    monkeypatch.setattr(os, 'getppid', lambda: 4242)
    monkeypatch.setattr(os, 'kill', lambda pid, sig: aufrufe.append((pid, sig)))
    with caplog.at_level(logging.WARNING):
        demo_reset()
    assert aufrufe == []
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize('demo,vorhanden,fehlt', [
    ('1', 'demo_reset', 'push_reminders'),
    (None, 'push_reminders', 'demo_reset'),
])
def test_create_app_im_demo_modus_traegt_reset_statt_erinnerung_ein(
        monkeypatch, tmp_path, demo, vorhanden, fehlt):
    from app import create_app
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'wegwerf.db'))
    monkeypatch.delenv('DISABLE_SCHEDULER', raising=False)
    if demo is None:
        monkeypatch.delenv('DEMO_MODE', raising=False)
    else:
        monkeypatch.setenv('DEMO_MODE', demo)
    neue_app = create_app()
    try:
        scheduler = neue_app.extensions['scheduler']
        assert scheduler.get_job(vorhanden) is not None
        assert scheduler.get_job(fehlt) is None
    finally:
        neue_app.extensions['scheduler'].shutdown(wait=False)


# --- Betrieb: Compose-Datei und Update-Skript ---------------------------------

REPO = pathlib.Path(__file__).resolve().parents[1]
COMPOSE_DEMO = REPO / 'docker-compose.demo.yml'
UPDATE_SKRIPT = REPO / 'scripts' / 'demo-update.sh'


def test_compose_demo_ohne_env_file_und_bind_mount():
    text = COMPOSE_DEMO.read_text(encoding='utf-8')
    for erwartet in ('name: kassenstuerzle-demo', 'DEMO_MODE', 'tmpfs',
                     '/app/data', 'cap_drop', 'no-new-privileges',
                     'exec gunicorn', 'python -m app.demo',
                     'restart: unless-stopped'):
        assert erwartet in text, erwartet
    # Die echte Installation und ihre Geheimnisse muessen unerreichbar bleiben.
    for verboten in ('env_file', './data', '.env'):
        assert verboten not in text, verboten


def test_compose_demo_ist_gueltig():
    if shutil.which('docker') is None:
        pytest.skip('docker nicht installiert')
    probe = subprocess.run(['docker', 'compose', 'version'],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip('docker compose-Plugin fehlt')
    ergebnis = subprocess.run(
        ['docker', 'compose', '-f', 'docker-compose.demo.yml', 'config'],
        cwd=REPO, capture_output=True, text=True)
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_update_skript_ist_ausfuehrbar_und_strikt():
    assert UPDATE_SKRIPT.is_file()
    assert os.access(UPDATE_SKRIPT, os.X_OK)
    text = UPDATE_SKRIPT.read_text(encoding='utf-8')
    assert text.startswith('#!/usr/bin/env bash')
    assert 'set -euo pipefail' in text
    assert 'git pull --ff-only' in text
    assert 'docker compose -f docker-compose.demo.yml up -d --build' in text
