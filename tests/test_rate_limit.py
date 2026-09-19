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


# ── Die 429-Antwort muss benutzbar sein (Befund I4) ─────────────────────────

def test_gebremster_api_aufruf_antwortet_mit_json(eigene_app):
    """Ohne eigenen Handler liefert Werkzeug hier HTML.

    Das Frontend ruft auf jede Antwort `res.json()` auf - bei HTML wirft das,
    der Fehler erreicht die Benutzerin nie und der Klick bleibt stumm.

    Bewusst ein GET-Endpunkt: die Limiter-Schicht sitzt an der View, der
    CSRF-Schutz dagegen schon in before_request - ein POST ohne Token kaeme
    also nie beim Limit an. Ob der Aufruf inhaltlich erlaubt ist, spielt keine
    Rolle; geprueft wird einzig die FORM der 429-Antwort.
    """
    client = eigene_app.test_client()
    antworten = [client.get('/api/backup/download/kassenstuerzle_x.db')
                 for _ in range(13)]
    gebremst = [a for a in antworten if a.status_code == 429]
    assert gebremst, ('Das Limit 10/Minute hat nicht gegriffen - Statuscodes: '
                      f'{[a.status_code for a in antworten]}')

    antwort = gebremst[0]
    assert antwort.mimetype == 'application/json', (
        f'429 kommt als {antwort.mimetype} statt als JSON zurueck - '
        'res.json() im Frontend wirft darauf')
    daten = antwort.get_json()
    assert daten['success'] is False
    assert daten['error'], 'Die 429-Antwort traegt keinen Fehlertext'


def test_gebremster_seitenaufruf_liefert_deutsche_seite(eigene_app):
    """Wer sich ein paar Mal beim Passwort vertippt, landete auf Werkzeugs
    unmarkierter englischer Standardseite - mitten in einer deutschen App."""
    client = eigene_app.test_client()
    antworten = [client.post('/login', data={'username': f'wer{i}', 'password': 'x'})
                 for i in range(13)]
    gebremst = [a for a in antworten if a.status_code == 429]
    assert gebremst, 'Login-Limit hat nicht gegriffen'

    html = gebremst[0].get_data(as_text=True)
    assert 'text/html' in gebremst[0].content_type
    from app.translations import translate
    assert translate('de', 'error.rate_limited_title') in html, (
        'Die 429-Seite ist nicht die eigene, uebersetzte Seite')
    # Gegenprobe auf Werkzeugs Standardtext.
    assert 'Too Many Requests' not in html


def test_tote_rate_limit_helfer_sind_entfernt():
    """`_rate_limit()` und `_get_limiter()` wurden von nichts aufgerufen und
    reichten den Limit-String obendrein an `callable_name` weiter - sie haetten
    also auch als Aufruf nichts durchgesetzt. Ihr blosses Dasein liess vier
    Pruefdurchgaenge glauben, es gebe eine Durchsetzung."""
    from app.routes import main as m

    assert not hasattr(m, '_rate_limit'), (
        '_rate_limit() ist zurueck - ein Helfer, der nichts durchsetzt, '
        'taeuscht eine Absicherung vor')
    assert not hasattr(m, '_get_limiter'), '_get_limiter() ist zurueck'


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

    # Bewusst ueber die oeffentliche Schnittstelle (decorated_limits) statt
    # ueber `_decorated_limits`: das private Feld kann sich mit jeder
    # flask-limiter-Version aendern. Der Name wird ausserdem exakt so gebildet
    # wie flask-limiter ihn selbst ablegt - der frueher benutzte
    # Suffix-Vergleich liess zwei gleichnamige Views einander gegenseitig
    # "bestaetigen", ein Limit auf der einen galt also auch fuer die andere.
    from flask_limiter.util import get_qualified_name

    limiter = eigene_app.extensions['limiter']
    ohne_limit = [
        e for e in RATE_LIMITS
        if not limiter.limit_manager.decorated_limits(
            get_qualified_name(eigene_app.view_functions[e]))
    ]
    assert not ohne_limit, f'Kein Limit vermerkt fuer: {ohne_limit}'
