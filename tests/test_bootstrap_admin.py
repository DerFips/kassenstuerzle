"""Befund I8: das Bootstrap-Passwort des ersten Admins ist ein Einmalpasswort.

Ohne ADMIN_PASSWORD erzeugt init_db() ein Zufallspasswort und schreibt es
vollstaendig ins Log (WARNING) - der Betreiber braucht bei einer frischen
Installation irgendeinen Weg hinein. Damit steht es aber im Docker-Log, das
Log-Treiber typischerweise aufbewahren und weiterreichen. Frueher setzte der
Bootstrap-INSERT `must_change_password` nicht (Spaltendefault 0), das geloggte
Passwort blieb also unbegrenzt gueltig.

Diese Tests laufen bewusst gegen eine eigene, frisch gebootstrappte
Wegwerf-Datenbank: die Datenbank der uebrigen Testsuite wird von conftest
absichtlich vom Zwang befreit, damit sich die anderen Tests direkt anmelden
koennen.
"""
import logging

import pytest

from app.routes import main as m


@pytest.fixture()
def frische_instanz(app, tmp_path, monkeypatch):
    """Bootstrappt eine komplett leere Datenbank wie bei einer Neuinstallation."""
    db_pfad = str(tmp_path / 'frisch.db')
    monkeypatch.setattr(m, 'DB_PATH', db_pfad)
    with app.app_context():
        m.init_db()
    return db_pfad


def _admin_zeile(db_pfad):
    conn = m._open_db(db_pfad)
    conn.row_factory = m.sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, must_change_password FROM users WHERE username='admin'").fetchone()
    finally:
        conn.close()


def test_bootstrap_admin_muss_passwort_wechseln(frische_instanz):
    """Das Kernversprechen: das Initialpasswort ist nur einmal benutzbar."""
    zeile = _admin_zeile(frische_instanz)
    assert zeile is not None, 'Bootstrap hat keinen admin angelegt'
    assert zeile['must_change_password'] == 1, (
        'Der frisch gebootstrappte Admin muss zum Passwortwechsel gezwungen '
        'werden - sonst bleibt das im Klartext geloggte Initialpasswort '
        'unbegrenzt gueltig'
    )


def test_bootstrap_admin_landet_beim_ersten_login_auf_der_wechselseite(app, frische_instanz):
    """Das Flag muss sich auch tatsaechlich auswirken: der erste Request nach
    dem Login geht nicht auf die Startseite, sondern auf /password/aendern."""
    client = app.test_client()
    res = client.post('/login', data={'username': 'admin',
                                      'password': 'test-password-123'})
    assert res.status_code == 302, 'Login mit dem Initialpasswort schlug fehl'

    weiter = client.get('/')
    assert weiter.status_code == 302
    assert '/password/aendern' in weiter.headers['Location'], (
        'Der erste Login mit dem Initialpasswort muss in den erzwungenen '
        f'Passwortwechsel laufen, ging aber nach {weiter.headers["Location"]}'
    )

    # Und nach dem Wechsel ist der Zwang weg.
    with client.session_transaction() as sess:
        token = sess['csrf_token']
    gewechselt = client.post('/password/aendern',
                             data={'new_password': 'ein-neues-geheimnis-123',
                                   'new_password2': 'ein-neues-geheimnis-123',
                                   'csrf_token': token})
    assert gewechselt.status_code == 302
    assert _admin_zeile(frische_instanz)['must_change_password'] == 0
    # '/' leitet je nach Zustand auf Setup-Assistent bzw. Monatsseite weiter -
    # entscheidend ist nur, dass es nicht mehr die Wechselseite ist.
    danach = client.get('/')
    assert '/password/aendern' not in danach.headers.get('Location', '')


def test_zufallspasswort_wird_mit_warnhinweis_geloggt(app, tmp_path, monkeypatch, caplog):
    """Ohne ADMIN_PASSWORD bleibt das Passwort im Log (der Betreiber braucht
    einen Weg hinein), aber die Meldung muss den Erstwechsel und die
    Vertraulichkeit des Logs benennen."""
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    monkeypatch.setattr(m, 'DB_PATH', str(tmp_path / 'ohne-env.db'))
    with caplog.at_level(logging.WARNING, logger=m.__name__):
        with app.app_context():
            m.init_db()

    meldungen = [r.getMessage() for r in caplog.records]
    passend = [t for t in meldungen if 'Einmal-Passwort' in t]
    assert passend, f'Keine Bootstrap-Warnung im Log gefunden: {meldungen}'
    text = passend[0]
    assert 'ersten Login' in text
    assert 'Klartext' in text and 'Log' in text

    assert _admin_zeile(str(tmp_path / 'ohne-env.db'))['must_change_password'] == 1
