"""Regression: die Rate-Limit-Schicht muss tatsaechlich greifen.

Befund aus der Sicherheitspruefung: `create_app()` wandte `limiter.limit(...)`
zwar auf jede View aus RATE_LIMITS an, verwarf aber den zurueckgegebenen
Wrapper. `app.view_functions` zeigte damit weiter auf die ungebremste
Originalfunktion - die gesamte Schicht war wirkungslos, ohne dass irgendetwas
das gemeldet haette. 15 Login-Versuche liefen ohne einen einzigen 429 durch.

Alle vier Pruefungen der Sicherheitsanalyse haben das uebersehen, weil die
Tabelle RATE_LIMITS korrekt aussieht. Nur ein Versuch gegen die laufende App
deckt es auf - deshalb dieser Test.
"""
import os
import tempfile

import pytest

from app import RATE_LIMITS, create_app


@pytest.fixture
def eigene_app():
    """Eigene App-Instanz mit eigenem In-Memory-Limit-Speicher.

    Bewusst nicht die Session-App aus conftest: Rate-Limit-Zaehler sind
    prozessweit und wuerden sonst in andere Tests ueberlaufen.
    """
    verzeichnis = tempfile.mkdtemp(prefix='kassenstuerzle-ratelimit-')
    alt_db = os.environ.get('DATABASE_PATH')
    alt_schalter = os.environ.get('DISABLE_RATE_LIMIT')
    os.environ['DATABASE_PATH'] = os.path.join(verzeichnis, 'wegwerf.db')
    # conftest schaltet die Limits fuer die uebrige Suite ab - hier sind sie
    # der Pruefgegenstand und muessen an sein.
    os.environ.pop('DISABLE_RATE_LIMIT', None)
    try:
        yield create_app()
    finally:
        if alt_db is not None:
            os.environ['DATABASE_PATH'] = alt_db
        if alt_schalter is not None:
            os.environ['DISABLE_RATE_LIMIT'] = alt_schalter


def test_login_limit_greift_tatsaechlich(eigene_app):
    """Ueber das konfigurierte Limit hinaus muss 429 kommen.

    Bewusst mit jedes Mal anderem Benutzernamen, damit NICHT der eigene
    Lockout aus main.py anschlaegt (der ist pro Benutzername) - so misst der
    Test wirklich die Flask-Limiter-Schicht und nicht die Sperre daneben.
    """
    client = eigene_app.test_client()
    codes = [client.post('/login',
                         data={'username': f'unbekannt{i}', 'password': 'falsch'}).status_code
             for i in range(15)]

    assert 429 in codes, (
        'Kein einziger Request wurde gebremst - die Rate-Limit-Schicht ist '
        f'wirkungslos. Statuscodes: {codes}')
    # Das konfigurierte Limit ist 10/Minute: die ersten zehn muessen durch,
    # danach muss gesperrt werden. Das haelt den Test ehrlich - ein Limit von
    # 1/Minute wuerde die Behauptung oben auch erfuellen.
    assert codes[:10] == [200] * 10, f'Zu frueh gebremst: {codes}'
    assert codes[10:] == [429] * 5, f'Nicht durchgehend gebremst: {codes}'


def test_kein_eintrag_in_rate_limits_laeuft_ins_leere(eigene_app):
    """Jeder Endpunktname aus RATE_LIMITS muss es auch wirklich geben.

    ACHTUNG - was dieser Test NICHT leistet: er beweist keine Durchsetzung.
    Flask-Limiter vermerkt ein Limit bereits als Seiteneffekt des
    Dekorator-Aufrufs, also auch dann, wenn der zurueckgegebene Wrapper
    anschliessend verworfen wird. Genau das war der Befund. Die Durchsetzung
    sichert `test_login_limit_greift_tatsaechlich` oben ab.

    Was er leistet: ein Tippfehler im Endpunktnamen (oder eine umbenannte
    View) laesst den Eintrag lautlos ins Leere laufen - die Tabelle sieht
    weiter richtig aus, der Endpunkt ist aber ungebremst.
    """
    unbekannt = [e for e in RATE_LIMITS if e not in eigene_app.view_functions]
    assert not unbekannt, (
        f'Eintrag in RATE_LIMITS ohne zugehoerige View: {unbekannt}')

    limiter = eigene_app.extensions['limiter']
    vermerkt = limiter.limit_manager._decorated_limits
    ohne_limit = [e for e in RATE_LIMITS
                  if not any(k.endswith('.' + e.split('.')[-1]) for k in vermerkt)]
    assert not ohne_limit, f'Kein Limit vermerkt fuer: {ohne_limit}'
