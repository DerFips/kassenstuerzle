"""Weitere Befunde der Code-Durchsicht November 2026 (Server)."""
import pytest

from app.routes import main as main_module


@pytest.fixture()
def zwei_personen(db):
    """Zwei Personen und eine geteilte Kategorie, in der nur eine zahlt -
    ergibt eine offene Ausgleichszahlung."""
    a = db.execute("INSERT INTO persons (name) VALUES (?)", ('Anna\r\nATTENDEE:mailto:x@example.org',)).lastrowid
    b = db.execute("INSERT INTO persons (name) VALUES ('Ben; der, Zweite')").lastrowid
    c = db.execute("INSERT INTO categories (name, color, is_split) VALUES ('Miete','#aa0000',1)").lastrowid
    for p in (a, b):
        db.execute('INSERT INTO category_splits (category_id, person_id, share) VALUES (?,?,1)', (c, p))
    db.execute('INSERT INTO expenses (year,month,day,description,person_id,category_id,amount)'
               ' VALUES (2026,9,1,?,?,?,100)', ('Miete', a, c))
    db.commit()
    return a, b


# ── PDF-Import: Fehlerpfade ──────────────────────────────────────────────────

def test_unlesbares_pdf_liefert_meldung_statt_absturz(app):
    """`t` war in extract_bank_statement eine lokale Variable (Seitentext) und
    verdeckte die Uebersetzungsfunktion: beide Fehlerpfade endeten in einem
    UnboundLocalError statt in der Meldung."""
    with app.test_request_context('/'):
        ergebnis = main_module.extract_bank_statement(bytearray(b'kein pdf'))
    assert ergebnis.get('error')


def test_pdf_endpunkt_meldet_unlesbares_pdf(auth_client, csrf):
    import io
    res = auth_client.post('/api/import/pdf/parse',
                           data={'file': (io.BytesIO(b'%PDF-1.4 kaputt'), 'x.pdf')},
                           headers={'X-CSRF-Token': csrf},
                           content_type='multipart/form-data')
    assert res.status_code < 500
    assert res.get_json().get('error')


# ── Sprache wechseln: kein offener Redirect ──────────────────────────────────

@pytest.mark.parametrize('referer', ['https://boese.example/seite',
                                     '//boese.example/seite',
                                     'http://localhost.boese.example/'])
def test_sprachwechsel_leitet_nicht_auf_fremde_seiten(auth_client, referer):
    res = auth_client.get('/set_language/en', headers={'Referer': referer})
    assert res.status_code == 302
    ziel = res.headers['Location']
    assert 'boese.example' not in ziel
    assert ziel.startswith('/') and not ziel.startswith('//')


def test_sprachwechsel_kehrt_zur_eigenen_seite_zurueck(auth_client):
    res = auth_client.get('/set_language/en',
                          headers={'Referer': 'http://localhost/settings?x=1'})
    assert res.headers['Location'] == '/settings?x=1'
    assert auth_client.get('/set_language/de').headers['Location'].endswith('/')


# ── Kalender-Export ──────────────────────────────────────────────────────────

def test_kalender_export_escaped_namen(auth_client, db, zwei_personen):
    """Ein Zeilenumbruch im Namen fuegte sonst eigene Felder in die Datei ein."""
    res = auth_client.get('/api/calendar/export')
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    zeilen = text.split('\r\n')
    assert not any(z.startswith('ATTENDEE') for z in zeilen)
    summary = [z for z in zeilen if z.startswith('SUMMARY:')]
    assert len(summary) == 1
    assert 'Anna\\nATTENDEE:mailto:x@example.org' in summary[0]
    assert 'Ben\\; der\\, Zweite' in summary[0]
    assert '\n' not in text.replace('\r\n', '')


# ── Push: Endpunkt pruefen ───────────────────────────────────────────────────

_KEYS = {'p256dh': 'x' * 20, 'auth': 'y' * 10}


@pytest.mark.parametrize('endpoint', [
    'http://fcm.googleapis.com/fcm/send/abc',       # kein https
    'https://127.0.0.1/x', 'https://127.0.0.1:8443/x',
    'https://[::1]/x', 'https://10.0.0.5/x', 'https://192.168.1.40:5000/x',
    'https://169.254.169.254/latest/meta-data/',
    'https://localhost/x', 'https://web/x', 'https://intern.local/x',
    'https://benutzer:pw@push.example.org/x',
    'file:///etc/passwd', 'gopher://x.example.org/', 'https:///x', '',
    'https://push.example.org/' + 'a' * 3000,
    12345, ['https://push.example.org/x'],
])
def test_push_registrierung_weist_unpassende_endpunkte_ab(auth_client, csrf, db, endpoint):
    """Der Server ruft diese Adresse spaeter selbst auf (Erinnerungen). Ohne
    Pruefung konnte ein angemeldetes Mitglied ihn auf interne Adressen zeigen
    lassen."""
    res = auth_client.post('/api/notifications/register',
                           json={'endpoint': endpoint, 'keys': _KEYS},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 400
    assert db.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0] == 0


@pytest.mark.parametrize('endpoint', [
    'https://fcm.googleapis.com/fcm/send/abc:def',
    'https://updates.push.services.mozilla.com/wpush/v2/gAAAA',
    'https://web.push.apple.com/QGxyz',
    'https://wns2-par02p.notify.windows.com/w/?token=abc',
])
def test_push_registrierung_nimmt_echte_dienste_an(auth_client, csrf, db, endpoint):
    try:
        res = auth_client.post('/api/notifications/register',
                               json={'endpoint': endpoint, 'keys': _KEYS},
                               headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        assert db.execute('SELECT endpoint FROM push_subscriptions').fetchone()[0] == endpoint
    finally:
        db.execute('DELETE FROM push_subscriptions')
        db.commit()


@pytest.mark.parametrize('payload', [[], 'text', {'endpoint': 'https://a.example.org/x', 'keys': 'k'}])
def test_push_registrierung_mit_kaputtem_json_ist_kein_serverfehler(auth_client, csrf, payload):
    res = auth_client.post('/api/notifications/register', json=payload,
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 400


def _falsches_pywebpush(monkeypatch, webpush):
    """Ersetzt das Modul pywebpush (in der Entwicklungsumgebung nicht
    installiert) durch eine Attrappe."""
    import sys
    import types
    modul = types.ModuleType('pywebpush')
    modul.webpush = webpush
    modul.WebPushException = type('WebPushException', (Exception,), {})
    monkeypatch.setitem(sys.modules, 'pywebpush', modul)


def test_push_versand_hat_zeitlimit(app, monkeypatch):
    """Ohne timeout haengt der Erinnerungs-Job an einem Endpunkt, der die
    Verbindung annimmt und nie antwortet."""
    gesehen = {}

    def fake(**kwargs):
        gesehen.update(kwargs)

    _falsches_pywebpush(monkeypatch, fake)
    with app.app_context():
        ok = main_module._send_one_push(
            {'endpoint': 'https://push.example.org/x', 'p256dh': 'a', 'auth': 'b'}, 'T', 'B')
    assert ok is True
    assert gesehen.get('timeout') and gesehen['timeout'] <= 30


def test_push_versand_ueberspringt_altlast_mit_internem_endpunkt(app, monkeypatch):
    """Vor der Pruefung gespeicherte Eintraege werden nicht mehr aufgerufen,
    sondern als ungueltig gemeldet (und damit entfernt)."""
    _falsches_pywebpush(monkeypatch,
                        lambda **kw: pytest.fail('interner Endpunkt wurde aufgerufen'))
    with app.app_context():
        ok = main_module._send_one_push(
            {'endpoint': 'http://127.0.0.1:9/x', 'p256dh': 'a', 'auth': 'b'}, 'T', 'B')
    assert ok is False


# ── Service Worker ───────────────────────────────────────────────────────────

def test_service_worker_wird_fuer_die_ganze_anwendung_registriert(auth_client, client):
    """Unter /static/sw.js gilt der Worker nur fuer /static/ und steuert keine
    Seite: keine Offline-Seite, und "Push aktivieren" wartet endlos auf
    navigator.serviceWorker.ready."""
    html = auth_client.get('/settings').get_data(as_text=True)
    assert "serviceWorker.register('/sw.js')" in html
    assert "register('/static/sw.js')" not in html
    res = client.get('/sw.js')      # ohne Anmeldung erreichbar
    assert res.status_code == 200
    assert res.mimetype in ('application/javascript', 'text/javascript')
    assert 'no-cache' in res.headers.get('Cache-Control', '')


def test_service_worker_cacht_keine_seiten_und_keine_fremden_hosts(client):
    """Seit der Worker alle Seiten steuert: Seiten mit Finanzdaten duerfen
    nicht im Browser-Cache landen, fremde Hosts (CDN) fasst er nicht an."""
    js = client.get('/sw.js').get_data(as_text=True)
    assert 'url.origin !== self.location.origin' in js
    precache = js.split('const PRECACHE = [')[1].split('];')[0]
    assert "'/'," not in precache, "die Startseite wird nicht mehr vorab gecacht"
    assert "caches.open('kassenstuerzle-v2')" not in js and 'CACHE_NAME' not in js, \
        'der Seiten-Cache frueherer Versionen wird nicht mehr benutzt'
    # Nur in zwei Faellen wird in den Cache geschrieben: beim Installieren
    # (PRECACHE) und fuer /static/.
    assert js.count('.put(') == 1 and js.count('addAll(') == 1
