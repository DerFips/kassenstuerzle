"""Demo-Modus: Sperr-Waechter, Banner und Anmelde-Anleitung.

Der Demo-Modus liegt in app.config['DEMO_MODE'] und wird bei jeder Anfrage neu
gelesen, deshalb schaltet die Fixture ihn nur um und stellt ihn danach zurueck.
"""
import json
import logging
import os
import pathlib
import re
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
    # Ein dauerhaft sichtbarer Hinweis ist eine Statusanzeige, kein Alarm:
    # role="alert" liest Screenreader bei jedem Seitenaufruf als Warnung vor.
    banner = re.search(r'<div id="demo-banner"[^>]*>', html).group(0)
    assert 'role="status"' in banner
    assert 'role="alert"' not in banner


def _anmelde_kasten(html):
    """Der gerenderte Kasten `demo-login-box` samt seinem Inhalt. Die Rohtexte
    stehen auch in window.I18N auf jeder Seite ("ben" steckt in "haben") und
    taugen deshalb nicht als Nachweis."""
    start = html.index('<div id="demo-login-box"')
    ende = html.index('</ul>', start)
    return html[start:ende]


def test_anmelde_kasten_nur_im_demo_modus(client, app, demo_modus):
    html = client.get('/login').get_data(as_text=True)
    assert 'id="demo-login-box"' in html
    kasten = _anmelde_kasten(html)
    assert '<li>anna – Administrator' in kasten
    assert '<li>ben – Mitglied' in kasten
    assert 'als Passwort demo1234, und' in html

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
                'demo.login_tip', 'login.invalid_simple'):
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


def _fehlermeldung(html):
    """Text des gerenderten Hinweiskastens (nicht der Rohtexte in window.I18N)."""
    treffer = re.search(r'<div class="alert alert-danger[^"]*"[^>]*>(.*?)<button',
                        html, re.S)
    assert treffer, 'kein Hinweiskasten gerendert'
    return treffer.group(1).strip()


def test_demo_falsche_anmeldung_nennt_keine_restversuche(client, demo_modus):
    """Es gibt in der Demo keine Sperre, also auch keine "verbleibenden Versuche"."""
    _zaehler_leeren()
    for name in ('admin', ''):
        html = client.post('/login', data={'username': name,
                                           'password': 'falsch'}).get_data(as_text=True)
        assert _fehlermeldung(html) == 'Ungültiger Benutzername oder Passwort.'


def test_ohne_demo_modus_nennt_falsche_anmeldung_die_restversuche(app, client):
    assert not app.config.get('DEMO_MODE')
    _zaehler_leeren()
    try:
        html = client.post('/login', data={'username': 'admin',
                                           'password': 'falsch'}).get_data(as_text=True)
        assert 'Versuche verbleibend' in _fehlermeldung(html)
        assert '4 Versuche verbleibend' in _fehlermeldung(html)
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


def test_reset_job_holt_einen_verpassten_lauf_nach():
    """APScheduler-Standard ist eine Gnadenfrist von 1 s: wacht der Thread
    spaeter auf, entfiele der Reset an diesem Tag."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from app.demo import schedule_demo_reset
    s = BackgroundScheduler()
    schedule_demo_reset(s, 4, 0)
    job = s.get_job('demo_reset')
    assert job.misfire_grace_time == 3600
    assert job.coalesce is True


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


def _code_zeilen(pfad):
    """Zeilen der Compose-Datei ohne Kommentare (ganze und angehaengte)."""
    zeilen = []
    for zeile in pathlib.Path(pfad).read_text(encoding='utf-8').splitlines():
        zeile = re.sub(r'(^|\s)#.*$', '', zeile).rstrip()
        if zeile.strip():
            zeilen.append(zeile)
    return zeilen


def _port_zeilen(zeilen):
    """Alle Eintraege unter `ports:` (Block- oder Inline-Schreibweise)."""
    eintraege = []
    for n, zeile in enumerate(zeilen):
        if zeile.strip().startswith('ports:'):
            rest = zeile.split(':', 1)[1].strip()
            if rest:
                eintraege.append(rest)
            einzug = len(zeile) - len(zeile.lstrip())
            for folge in zeilen[n + 1:]:
                if len(folge) - len(folge.lstrip()) <= einzug:
                    break
                eintraege.append(folge.strip())
    return eintraege


def _pruefe_compose_text(pfad):
    """Textpruefung nur der Code-Zeilen: Pflichtteile da, Isolation gewahrt."""
    code = '\n'.join(_code_zeilen(pfad))
    for erwartet in ('name: kassenstuerzle-demo', 'DEMO_MODE', 'tmpfs',
                     '/app/data', 'cap_drop', 'no-new-privileges',
                     'exec gunicorn', 'python -m app.demo',
                     'restart: unless-stopped'):
        assert erwartet in code, erwartet
    # Die echte Installation und ihre Geheimnisse muessen unerreichbar bleiben.
    for verboten in ('env_file', 'volumes', 'extra_hosts', 'network_mode',
                     'external', './data', '.env'):
        assert verboten not in code, verboten
    ports = _port_zeilen(_code_zeilen(pfad))
    assert ports, 'keine Port-Zuordnung gefunden'
    for eintrag in ports:
        assert '127.0.0.1' in eintrag, eintrag


def _compose_json(pfad, extra_env=None):
    """`docker compose config --format json` oder Skip, wenn Docker fehlt."""
    if shutil.which('docker') is None:
        pytest.skip('docker nicht installiert')
    probe = subprocess.run(['docker', 'compose', 'version'],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip('docker compose-Plugin fehlt')
    env = dict(os.environ)
    for name in ('DEMO_PORT', 'DEMO_PASSWORD', 'DEMO_HTTPS',
                 'DEMO_RESET_HOUR', 'DEMO_RESET_MINUTE'):
        env.pop(name, None)
    env.update(extra_env or {})
    ergebnis = subprocess.run(
        ['docker', 'compose', '-f', str(pfad), 'config', '--format', 'json'],
        cwd=pathlib.Path(pfad).parent, capture_output=True, text=True, env=env)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return json.loads(ergebnis.stdout)


def _pruefe_compose_struktur(config, port='5001'):
    assert config['name'] == 'kassenstuerzle-demo'
    web = config['services']['web']
    for verboten in ('volumes', 'env_file', 'extra_hosts', 'network_mode'):
        assert verboten not in web, verboten
    assert len(web['ports']) == 1
    eintrag = web['ports'][0]
    assert eintrag.get('host_ip') == '127.0.0.1'
    assert eintrag['target'] == 5000
    assert str(eintrag['published']) == port
    assert web['restart'] == 'unless-stopped'
    assert web['cap_drop'] == ['ALL']
    assert web['user'] == '1000:1000'
    assert web['environment']['DEMO_MODE'] == '1'
    # Compose loest eine Umgebungsdatei in `environment` auf (der Schluessel
    # env_file bleibt dann nicht erhalten). Darum muss genau die bekannte
    # Variablenmenge uebrig bleiben: jede fremde Variable faellt auf.
    assert set(web['environment']) == {
        'DEMO_MODE', 'ALLOW_UNENCRYPTED', 'DEMO_PASSWORD', 'ADMIN_PASSWORD',
        'HTTPS', 'DEMO_RESET_HOUR', 'DEMO_RESET_MINUTE', 'TZ',
        'GUNICORN_WORKERS'}
    # tmpfs wird als Liste von Zeichenketten "ziel:optionen" ausgegeben.
    ziele = [t.split(':', 1)[0] for t in web['tmpfs']]
    assert ziele == ['/app/data']


def test_compose_demo_ohne_env_file_und_bind_mount():
    _pruefe_compose_text(COMPOSE_DEMO)


def test_compose_demo_ist_gueltig():
    _compose_json(COMPOSE_DEMO)


def test_compose_demo_struktur_und_isolation():
    _pruefe_compose_struktur(_compose_json(COMPOSE_DEMO))


def test_compose_demo_tmpfs_128m_und_log_rotation():
    web = _compose_json(COMPOSE_DEMO)['services']['web']
    # Eine einzelne grosse Anfrage ist auf 256 KB begrenzt; 128m tmpfs bleiben
    # weit unter mem_limit (512m).
    assert web['tmpfs'] == ['/app/data:uid=1000,gid=1000,mode=0700,size=128m']
    # Ohne Rotation waechst das json-file-Log des Containers unbegrenzt.
    assert web['logging']['driver'] == 'json-file'
    assert web['logging']['options'] == {'max-size': '10m', 'max-file': '3'}


def test_compose_demo_port_folgt_demo_port():
    config = _compose_json(COMPOSE_DEMO, {'DEMO_PORT': '5099'})
    _pruefe_compose_struktur(config, port='5099')


# Jede Aenderung bricht die Isolation: (Name, Zeile nach `restart:` einfuegen
# oder Zeile ersetzen)
_ISOLATIONSBRUECHE = {
    'bind_mount_absolut': ('einfuegen', '    volumes: ["/srv/kasse/data:/app/data"]'),
    'bind_mount_fremd': ('einfuegen', '    volumes:\n      - /var/lib/foo:/x'),
    'env_file': ('einfuegen', '    env_file: .env'),
    'host_netz': ('einfuegen', '    network_mode: host'),
    'alle_interfaces': ('ersetzen', '      - "5001:5000"'),
}


def _bruch_kopie(tmp_path, name):
    art, zeile = _ISOLATIONSBRUECHE[name]
    text = COMPOSE_DEMO.read_text(encoding='utf-8')
    if art == 'einfuegen':
        text = text.replace('    restart: unless-stopped\n',
                            '    restart: unless-stopped\n' + zeile + '\n', 1)
    else:
        text = text.replace('      - "127.0.0.1:${DEMO_PORT:-5001}:5000"', zeile, 1)
    assert zeile in text
    kopie = tmp_path / 'docker-compose.demo.yml'
    kopie.write_text(text, encoding='utf-8')
    (tmp_path / '.env').write_text('X=1\n', encoding='utf-8')
    return kopie


@pytest.mark.parametrize('name', sorted(_ISOLATIONSBRUECHE))
def test_textpruefung_erkennt_isolationsbrueche(tmp_path, name):
    kopie = _bruch_kopie(tmp_path, name)
    with pytest.raises(AssertionError):
        _pruefe_compose_text(kopie)


@pytest.mark.parametrize('name', sorted(_ISOLATIONSBRUECHE))
def test_strukturpruefung_erkennt_isolationsbrueche(tmp_path, name):
    kopie = _bruch_kopie(tmp_path, name)
    # Docker lehnt manche Brueche schon beim `config` ab (z. B. ein Mount auf
    # /app/data neben dem tmpfs). Auch das ist ein Fehlschlag, kein Durchlass.
    with pytest.raises(AssertionError):
        _pruefe_compose_struktur(_compose_json(kopie))


def test_textpruefung_ignoriert_kommentare(tmp_path):
    """Pflichtteile nur in Kommentaren zaehlen nicht, verbotene Woerter
    in Kommentaren stoeren nicht."""
    kopie = tmp_path / 'c.yml'
    kopie.write_text('# name: kassenstuerzle-demo env_file volumes\n'
                     'name: x # tmpfs\n', encoding='utf-8')
    with pytest.raises(AssertionError) as fehler:
        _pruefe_compose_text(kopie)
    assert 'name: kassenstuerzle-demo' in str(fehler.value)


def test_update_skript_ist_ausfuehrbar_und_strikt():
    assert UPDATE_SKRIPT.is_file()
    assert os.access(UPDATE_SKRIPT, os.X_OK)
    text = UPDATE_SKRIPT.read_text(encoding='utf-8')
    assert text.startswith('#!/usr/bin/env bash')
    assert 'set -euo pipefail' in text
    assert 'git pull --ff-only' in text
    assert 'docker compose' in text
    assert '-f docker-compose.demo.yml up -d --build' in text
    # Das Skript arbeitet immer im Projektverzeichnis, egal wo es aufgerufen wird.
    assert 'cd "$(dirname "$0")/.."' in text
    # Optionale, nicht eingecheckte Einstellungsdatei: sonst gingen DEMO_*-Werte
    # aus der Shell beim Neuerstellen des Containers verloren.
    assert 'demo.env' in text
    assert '--env-file' in text


# --- Grosse Anfragen (Demo-Modus) ------------------------------------------------

def _app_mit_demo_modus(monkeypatch, tmp_path, demo):
    from app import create_app
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'wegwerf.db'))
    monkeypatch.setenv('DISABLE_SCHEDULER', '1')
    if demo:
        monkeypatch.setenv('DEMO_MODE', '1')
    else:
        monkeypatch.delenv('DEMO_MODE', raising=False)
    neue_app = create_app()
    neue_app.config.update(TESTING=True)
    return neue_app


@pytest.mark.parametrize('demo,grenze', [(True, 256 * 1024), (False, 32 * 1024 * 1024)])
def test_anfragegroesse_nur_im_demo_modus_begrenzt(monkeypatch, tmp_path, demo, grenze):
    neue_app = _app_mit_demo_modus(monkeypatch, tmp_path, demo)
    assert neue_app.config['MAX_CONTENT_LENGTH'] == grenze


def _grosse_anfrage(neue_app):
    """Meldet 'admin' an und sendet eine Beschreibung von rund 300 KB."""
    from app.routes import main as m
    conn = m._open_db(m.DB_PATH)
    try:
        conn.execute("UPDATE users SET must_change_password=0 WHERE username='admin'")
        conn.commit()
    finally:
        conn.close()
    c = neue_app.test_client()
    res = c.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
    assert res.status_code == 302, 'Login fehlgeschlagen'
    token = c.get('/api/csrf-token').get_json()['csrf_token']
    return c.post('/api/expenses/save', headers={'X-CSRF-Token': token},
                  json=_body(year=2026, month=9, rows=[
                      {'client_key': 'c1', 'day': 3, 'description': 'x' * (300 * 1024),
                       'amount': 1}]))


def test_demo_modus_lehnt_anfrage_ueber_256_kb_mit_413_ab(monkeypatch, tmp_path):
    res = _grosse_anfrage(_app_mit_demo_modus(monkeypatch, tmp_path, True))
    assert res.status_code == 413


def test_ohne_demo_modus_wird_grosse_anfrage_nicht_mit_413_abgelehnt(monkeypatch, tmp_path):
    res = _grosse_anfrage(_app_mit_demo_modus(monkeypatch, tmp_path, False))
    assert res.status_code != 413


def test_demo_env_ist_nicht_eingecheckt():
    zeilen = [z.strip() for z in
              (REPO / '.gitignore').read_text(encoding='utf-8').splitlines()]
    assert 'demo.env' in zeilen


def _fuehre_update_skript_aus(tmp_path, mit_demo_env):
    """Fuehrt eine Kopie des Skripts mit Attrappen fuer git und docker aus und
    liefert die Argumente des `docker compose`-Aufrufs."""
    projekt = tmp_path / 'projekt'
    (projekt / 'scripts').mkdir(parents=True)
    (tmp_path / 'bin').mkdir()
    kopie = projekt / 'scripts' / 'demo-update.sh'
    shutil.copy(UPDATE_SKRIPT, kopie)
    if mit_demo_env:
        (projekt / 'demo.env').write_text('DEMO_PORT=5099\n', encoding='utf-8')
    attrappen = tmp_path / 'bin'
    protokoll = tmp_path / 'aufrufe.txt'
    for name in ('git', 'docker'):
        skript = attrappen / name
        skript.write_text('#!/bin/sh\necho "%s $*" >> "%s"\n' % (name, protokoll),
                          encoding='utf-8')
        skript.chmod(0o755)
    env = dict(os.environ, PATH='%s:%s' % (attrappen, os.environ['PATH']))
    # Aus einem fremden Verzeichnis starten: das Skript muss selbst wechseln.
    ergebnis = subprocess.run(['bash', str(kopie)], cwd=tmp_path, env=env,
                              capture_output=True, text=True)
    assert ergebnis.returncode == 0, ergebnis.stderr
    return protokoll.read_text(encoding='utf-8').splitlines()


def test_update_skript_nutzt_demo_env_nur_wenn_vorhanden(tmp_path):
    ohne = _fuehre_update_skript_aus(tmp_path / 'a', mit_demo_env=False)
    assert ohne[-1] == 'docker compose -f docker-compose.demo.yml up -d --build'
    mit = _fuehre_update_skript_aus(tmp_path / 'b', mit_demo_env=True)
    assert mit[-1] == ('docker compose --env-file demo.env '
                       '-f docker-compose.demo.yml up -d --build')
