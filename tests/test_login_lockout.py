import time

import pytest

from app.routes import main as m
from app.translations import translate


def _reset():
    m._login_attempts.clear()
    m._ip_attempts.clear()


def test_fehlversuche_sperren_nur_den_betroffenen_benutzer(client, db):
    """Im gemeinsamen WLAN darf ein Nutzer mit Tippfehlern niemanden
    anderen aussperren - frueher war die Sperre IP-basiert."""
    _reset()
    db.execute("INSERT INTO users (username, password_hash, role) VALUES ('bob', 'x', 'member')")
    db.commit()
    try:
        for _ in range(m._MAX_LOGIN_ATTEMPTS):
            client.post('/login', data={'username': 'bob', 'password': 'falsch'})

        res = client.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
        assert res.status_code == 302, 'admin wurde durch bobs Fehlversuche mitgesperrt'
    finally:
        # 'users' ist nicht Teil der DB-Fixture-Aufraeumtabellen - ohne diese
        # Zeile wuerde 'bob' andere Tests verunreinigen (z.B. den Autolink-Test,
        # der genau einen Benutzer erwartet).
        db.execute("DELETE FROM users WHERE username='bob'")
        db.commit()
        _reset()


def test_gesperrter_benutzer_wird_abgewiesen(client):
    _reset()
    for _ in range(m._MAX_LOGIN_ATTEMPTS):
        client.post('/login', data={'username': 'admin', 'password': 'falsch'})
    res = client.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
    assert res.status_code == 200, 'gesperrtes Konto haette nicht anmelden duerfen'
    _reset()


def test_erfolgreicher_login_setzt_zaehler_zurueck(client):
    _reset()
    client.post('/login', data={'username': 'admin', 'password': 'falsch'})
    client.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
    assert m._login_attempts['admin']['count'] == 0
    _reset()


def test_leere_benutzername_versuche_sperren_niemanden(client):
    """Ein leer abgeschicktes Formular (oder ein blosses GET) hat keinen
    Benutzernamen - der Schluessel waere ''. Wuerde man den mitzaehlen,
    koennte ein einziger Besucher mit fuenf leeren Absendungen allen
    anderen die Sperrmeldung auf der Loginseite einblenden."""
    _reset()
    for _ in range(m._MAX_LOGIN_ATTEMPTS):
        client.post('/login', data={'username': '', 'password': 'falsch'})

    res = client.get('/login')
    body = res.get_data(as_text=True)
    # 'Zu viele Fehlversuche' steht immer im eingebetteten window.I18N-Block
    # (JS-Uebersetzungstabelle) - das ist keine Sperrmeldung, sondern nur die
    # Rohtabelle mit unersetztem Platzhalter. Eine echte Flash-Meldung landet
    # als 'alert-danger'-Banner (der statische PWA-Install-Hinweis ist
    # 'alert-success' und waere sonst ein falscher Treffer).
    assert 'alert-danger' not in body, \
        'ein leerer Benutzername darf die Loginseite fuer alle anderen nicht sperren'
    assert '' not in m._login_attempts
    _reset()


def test_meldung_verraet_nicht_ob_konto_existiert(client):
    """Account-Enumeration verhindern: gleiche Meldung fuer beide Faelle."""
    _reset()
    a = client.post('/login', data={'username': 'gibtesnicht', 'password': 'x'})
    _reset()
    b = client.post('/login', data={'username': 'admin', 'password': 'falsch'})
    _reset()
    assert a.get_data(as_text=True).count('Ungültiger') == b.get_data(as_text=True).count('Ungültiger')


def test_login_mit_kaputtem_hash_scheitert_nicht_mit_500(client, db):
    """Regression: ein password_hash, der nicht von generate_password_hash()
    stammt (z.B. aus einer eingespielten, gebastelten Sicherungsdatei mit
    einem nicht-leeren, aber ungueltigen Hash wie 'x$y$z' - der zweite Riegel
    vor init_db() prueft nur auf 'nicht leerer Text', nicht auf ein
    gueltiges Hash-Format), darf das betroffene Konto nicht dauerhaft
    aussperren. Ohne die Absicherung wirft check_password_hash() ValueError,
    unbehandelt -> 500 bei JEDEM Loginversuch, behebbar nur per
    Datenbankzugriff."""
    _reset()
    db.execute("INSERT INTO users (username, password_hash, role, is_active) "
               "VALUES ('kaputt', 'x$y$z', 'member', 1)")
    db.commit()
    try:
        res = client.post('/login', data={'username': 'kaputt', 'password': 'irgendwas'})
        assert res.status_code != 500, 'Login wirft einen 500er statt falsches Passwort zu melden'
        assert res.status_code == 200, 'Erwartet: zurueck auf die Login-Seite (falsches Passwort)'
    finally:
        db.execute("DELETE FROM users WHERE username = 'kaputt'")
        db.commit()


def test_change_password_mit_kaputtem_altem_hash_scheitert_nicht_mit_500(auth_client, db, csrf):
    """Dieselbe Absicherung fuer /change_password: ein kaputter
    password_hash darf die alte-Passwort-Pruefung nicht mit einem 500er
    scheitern lassen, sondern muss als 'altes Passwort falsch' behandelt
    werden."""
    db.execute("UPDATE users SET password_hash = 'x$y$z' WHERE username = 'admin'")
    db.commit()
    try:
        res = auth_client.post('/change_password',
                               data={'old_password': 'irgendwas', 'new_password': 'neues-passwort-123'},
                               headers={'X-CSRF-Token': csrf})
        assert res.status_code == 400, 'Erwartet: 400 (altes Passwort falsch), kein 500'
        assert res.get_json()['success'] is False
    finally:
        from werkzeug.security import generate_password_hash
        db.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'",
                   (generate_password_hash('test-password-123'),))
        db.commit()


# ── Fuenfte Nachpruefung: Kosten statt Ausnahmen ─────────────────────────────
#
# Ein gespeicherter Hash traegt seine Kostenparameter selbst. Ohne Obergrenze
# rechnet check_password_hash() bei 'pbkdf2:sha256:2147483647$...' ueber zwei
# Minuten, und Iterationszahlen jenseits von 2**31 werfen OverflowError - die
# fehlte im frueheren `except (ValueError, TypeError)`.
#
# Die Tests messen NICHT die langsame Rechnung selbst (das hiesse bei einer
# Regression: 150 Sekunden Haenger). Stattdessen protokolliert ein Spion jeden
# Aufruf von check_password_hash und rechnet den boesen Hash nie wirklich
# durch. Rot wird der Test, sobald der boese Hash die Funktion ueberhaupt
# erreicht - sofort, ohne zu warten.

_HEX64 = 'ab' * 32

UNPLAUSIBLE_HASHES = {
    'pbkdf2_maximal': f'pbkdf2:sha256:2147483647$salzsalzsalzsalz${_HEX64}',
    'pbkdf2_ueberlauf_c_long': f'pbkdf2:sha256:99999999999999999999$salz${_HEX64}',
    'pbkdf2_ueberlauf_iteration': f'pbkdf2:sha256:2147483648$salz${_HEX64}',
    'scrypt_speicher': f'scrypt:1073741824:8:1$salzsalzsalzsalz${_HEX64}',
    'scrypt_rechenzeit_p': f'scrypt:32768:8:100000$salzsalzsalzsalz${_HEX64}',
}


def _kdf_spion(monkeypatch, boeser_hash):
    """Ersetzt check_password_hash in main.py durch einen Spion.

    Der Spion rechnet jeden Hash echt durch - ausser dem boesen: der wird nur
    notiert und sofort mit False beantwortet. So bleibt der Test auch dann
    schnell, wenn die Absicherung fehlt."""
    echt = m.check_password_hash
    aufrufe = []

    def spion(pwhash, password):
        aufrufe.append(pwhash)
        if pwhash == boeser_hash:
            return False
        return echt(pwhash, password)

    monkeypatch.setattr(m, 'check_password_hash', spion)
    return aufrufe


@pytest.mark.parametrize('boeser_hash', list(UNPLAUSIBLE_HASHES.values()),
                         ids=list(UNPLAUSIBLE_HASHES))
def test_login_rechnet_hash_mit_unplausiblen_kosten_nicht(client, db, monkeypatch, boeser_hash):
    _reset()
    db.execute("INSERT INTO users (username, password_hash, role, is_active) "
               "VALUES ('teuer', ?, 'member', 1)", (boeser_hash,))
    db.commit()
    aufrufe = _kdf_spion(monkeypatch, boeser_hash)
    try:
        start = time.monotonic()
        res = client.post('/login', data={'username': 'teuer', 'password': 'irgendwas'})
        dauer = time.monotonic() - start
        assert res.status_code == 200, 'Erwartet: zurueck auf die Login-Seite (falsches Passwort)'
        assert 'Ungültiger' in res.get_data(as_text=True)
        assert boeser_hash not in aufrufe, (
            'Der Hash mit unplausiblen Kosten wurde an check_password_hash gegeben')
        # Der Dummy-Vergleich muss trotzdem laufen (Zeitkanal, Befund I3).
        assert m._DUMMY_PASSWORT_HASH in aufrufe
        assert dauer < 2, f'Login brauchte {dauer:.1f} s'
    finally:
        db.execute("DELETE FROM users WHERE username = 'teuer'")
        db.commit()
        _reset()


@pytest.mark.parametrize('boeser_hash', list(UNPLAUSIBLE_HASHES.values()),
                         ids=list(UNPLAUSIBLE_HASHES))
def test_change_password_rechnet_hash_mit_unplausiblen_kosten_nicht(
        auth_client, db, csrf, monkeypatch, boeser_hash):
    from werkzeug.security import generate_password_hash
    db.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'", (boeser_hash,))
    db.commit()
    aufrufe = _kdf_spion(monkeypatch, boeser_hash)
    try:
        start = time.monotonic()
        res = auth_client.post('/change_password',
                               data={'old_password': 'irgendwas',
                                     'new_password': 'neues-passwort-123'},
                               headers={'X-CSRF-Token': csrf})
        dauer = time.monotonic() - start
        assert res.status_code == 400, 'Erwartet: 400 (altes Passwort falsch), kein 500'
        assert res.get_json()['error'] == translate('de', 'srv.old_password_wrong')
        assert boeser_hash not in aufrufe, (
            'Der Hash mit unplausiblen Kosten wurde an check_password_hash gegeben')
        assert dauer < 2, f'Passwortwechsel brauchte {dauer:.1f} s'
    finally:
        db.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'",
                   (generate_password_hash('test-password-123'),))
        db.commit()


# Hosentraeger zum Guertel: selbst wenn die Kostenpruefung einen Hash
# faelschlich durchliesse, darf eine Ausnahme aus werkzeug keinen 500er
# ausloesen. Die Kostenpruefung wird dafuer abgeschaltet, der Hash wirft
# echt (OverflowError) - der Fall, den `except (ValueError, TypeError)`
# verpasste.
_UEBERLAUF_HASH = UNPLAUSIBLE_HASHES['pbkdf2_ueberlauf_iteration']


def test_login_ueberlebt_ausnahme_aus_check_password_hash(client, db, monkeypatch):
    _reset()
    monkeypatch.setattr(m, '_hash_kosten_sind_plausibel', lambda h: True)
    db.execute("INSERT INTO users (username, password_hash, role, is_active) "
               "VALUES ('ueberlauf', ?, 'member', 1)", (_UEBERLAUF_HASH,))
    db.commit()
    try:
        start = time.monotonic()
        res = client.post('/login', data={'username': 'ueberlauf', 'password': 'irgendwas'})
        assert res.status_code == 200, 'OverflowError fuehrte zu einem 500er'
        assert 'Ungültiger' in res.get_data(as_text=True)
        assert time.monotonic() - start < 2
    finally:
        db.execute("DELETE FROM users WHERE username = 'ueberlauf'")
        db.commit()
        _reset()


def test_change_password_ueberlebt_ausnahme_aus_check_password_hash(
        auth_client, db, csrf, monkeypatch):
    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(m, '_hash_kosten_sind_plausibel', lambda h: True)
    db.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'",
               (_UEBERLAUF_HASH,))
    db.commit()
    try:
        start = time.monotonic()
        res = auth_client.post('/change_password',
                               data={'old_password': 'irgendwas',
                                     'new_password': 'neues-passwort-123'},
                               headers={'X-CSRF-Token': csrf})
        assert res.status_code == 400, 'OverflowError fuehrte zu einem 500er'
        assert res.get_json()['error'] == translate('de', 'srv.old_password_wrong')
        assert time.monotonic() - start < 2
    finally:
        db.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'",
                   (generate_password_hash('test-password-123'),))
        db.commit()


# Und das Gegenstueck: echte Hashes muessen unveraendert durchgehen.
@pytest.mark.parametrize('verfahren', [
    None,                       # Voreinstellung (scrypt)
    'scrypt',
    'pbkdf2',                   # aktuelle werkzeug-Voreinstellung
    'pbkdf2:sha256:600000',     # Werkzeug 3.0
    'pbkdf2:sha256:260000',     # Werkzeug 2.x
    'pbkdf2:sha512',
])
def test_echter_hash_ist_plausibel(verfahren):
    from werkzeug.security import generate_password_hash
    h = generate_password_hash('x') if verfahren is None else generate_password_hash('x', verfahren)
    assert m._hash_kosten_sind_plausibel(h), h


def test_voreinstellung_von_werkzeug_liegt_unter_der_obergrenze():
    """Stolperdraht fuer ein werkzeug-Update: hebt es die Voreinstellung
    ueber die Obergrenze, fiele sonst jeder frisch gesetzte Hash beim Login
    durch - das soll hier auffallen, nicht bei den Nutzern."""
    from werkzeug.security import generate_password_hash
    for verfahren in ('scrypt', 'pbkdf2'):
        h = generate_password_hash('x', verfahren)
        assert m._hash_kosten_sind_plausibel(h), (verfahren, h.split('$')[0])
    assert m._hash_kosten_sind_plausibel(m._DUMMY_PASSWORT_HASH)


def test_echter_login_und_passwortwechsel_unveraendert(client, db):
    """Ein normal gesetztes Passwort meldet an, ein falsches nicht."""
    _reset()
    assert client.post('/login', data={'username': 'admin',
                                       'password': 'falsch'}).status_code == 200
    res = client.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
    assert res.status_code == 302
    token = client.get('/api/csrf-token').get_json()['csrf_token']
    from werkzeug.security import generate_password_hash
    try:
        res = client.post('/change_password',
                          data={'old_password': 'test-password-123',
                                'new_password': 'ein-anderes-passwort'},
                          headers={'X-CSRF-Token': token})
        assert res.status_code == 200 and res.get_json()['success'] is True
    finally:
        db.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'",
                   (generate_password_hash('test-password-123'),))
        db.commit()
        _reset()
