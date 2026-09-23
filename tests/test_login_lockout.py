from app.routes import main as m


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
