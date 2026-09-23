"""Regressionstests für Befund I7 (Sicherheitsprüfung 2026-09-18):

  * `SESSION_COOKIE_SECURE` wurde aus `HTTPS` abgeleitet, mit `'0'` als
    Default - und das Dockerfile setzte selbst `HTTPS=0`. Die Sitzungscookies
    gingen also ohne `Secure`-Flag hinaus.
  * `docker-compose.yml` veröffentlichte `5000:5000`, also auf ALLEN
    Host-Interfaces. Zusammen ergab das einen Klartext-Pfad an dem
    TLS-terminierenden Reverse-Proxy vorbei, auf dem das Cookie mitlesbar war.

Die Dateitests lesen nur Text im Projekt - kein Docker-Build, kein Zugriff auf
data/ oder data_/.
"""
import pathlib

import pytest

import app as app_paket
from app import create_app

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── Cookie-Default ────────────────────────────────────────────────────────────

def test_secure_cookie_ist_im_container_der_default(monkeypatch):
    """DER eigentliche Regressionswächter.

    `HTTPS` ist nicht gesetzt, wir laufen erkennbar im Container: das Cookie
    MUSS `Secure` sein. Mit dem alten Code (`os.environ.get('HTTPS', '0')`)
    kam hier False heraus - genau die Lücke aus I7.
    """
    monkeypatch.delenv('HTTPS', raising=False)
    monkeypatch.setenv('KASSENSTUERZLE_IM_CONTAINER', '1')

    anwendung = create_app()
    assert anwendung.config['SESSION_COOKIE_SECURE'] is True, (
        'Ohne gesetztes HTTPS muss im Container der sichere Default gelten')


def test_secure_cookie_nur_bei_ausdruecklichem_ausstieg_aus(monkeypatch):
    """`HTTPS=0` ist der bewusste Ausstieg des Betreibers und hat Vorrang.

    Hinweis: diese Prüfung allein wäre KEIN Regressionswächter - sie war auch
    mit dem alten Code grün. Sie sichert die andere Hälfte des Vertrags ab:
    der sichere Default darf abschaltbar bleiben, sonst weicht ihn jemand
    anders auf.
    """
    monkeypatch.setenv('KASSENSTUERZLE_IM_CONTAINER', '1')
    monkeypatch.setenv('HTTPS', '0')

    anwendung = create_app()
    assert anwendung.config['SESSION_COOKIE_SECURE'] is False, (
        'HTTPS=0 muss den sicheren Default ausdrücklich abschalten können')


def test_lokale_entwicklung_wird_nicht_ausgesperrt(monkeypatch):
    """`python run.py` spricht Klartext-HTTP auf 127.0.0.1.

    Ein Secure-Cookie würde der Browser dort nicht zurücksenden - die Anmeldung
    fiele endlos auf die Login-Seite zurück. Ohne Container-Merkmal und ohne
    gesetztes HTTPS bleibt der Default deshalb aus.

    Auch diese Prüfung war mit dem alten Code grün; sie bewacht nicht I7,
    sondern dass die Behebung die lokale Entwicklung nicht bricht.
    """
    monkeypatch.delenv('HTTPS', raising=False)
    monkeypatch.delenv('KASSENSTUERZLE_IM_CONTAINER', raising=False)
    monkeypatch.setattr(app_paket, '_laeuft_im_container', lambda: False)

    anwendung = create_app()
    assert anwendung.config['SESSION_COOKIE_SECURE'] is False


@pytest.mark.parametrize('flag', ['SESSION_COOKIE_HTTPONLY', 'SESSION_COOKIE_SAMESITE'])
def test_uebrige_cookie_flags_bleiben_gesetzt(monkeypatch, flag):
    """HttpOnly und SameSite waren bereits korrekt - sie dürfen bei der
    Umstellung des Secure-Defaults nicht verlorengehen."""
    monkeypatch.setenv('KASSENSTUERZLE_IM_CONTAINER', '1')
    anwendung = create_app()
    erwartet = {'SESSION_COOKIE_HTTPONLY': True, 'SESSION_COOKIE_SAMESITE': 'Strict'}
    assert anwendung.config[flag] == erwartet[flag]


# ── Dockerfile / docker-compose.yml ───────────────────────────────────────────

def test_dockerfile_setzt_https_an(monkeypatch):
    """`ENV HTTPS=0` im Dockerfile war die zweite Hälfte von I7: selbst ein
    korrekter Code-Default wäre davon überschrieben worden."""
    inhalt = (PROJECT_ROOT / 'Dockerfile').read_text(encoding='utf-8')
    env_zeilen = [z.strip() for z in inhalt.splitlines()
                  if z.strip().startswith('ENV ') and 'HTTPS' in z]

    assert env_zeilen, 'Dockerfile setzt HTTPS gar nicht'
    assert all(z.split('HTTPS', 1)[1].lstrip().startswith('=1') for z in env_zeilen), (
        f'Dockerfile setzt HTTPS nicht auf 1: {env_zeilen}')


def test_compose_veroeffentlicht_port_nur_auf_localhost():
    """`"5000:5000"` bindet auf 0.0.0.0 - die App ist dann am Reverse-Proxy
    vorbei erreichbar. Jede veröffentlichte Portzuordnung muss an 127.0.0.1
    (bzw. ::1) gebunden sein."""
    zeilen = (PROJECT_ROOT / 'docker-compose.yml').read_text(
        encoding='utf-8').splitlines()

    in_ports = False
    mappings = []
    for zeile in zeilen:
        blank = zeile.strip()
        if not blank or blank.startswith('#'):
            continue
        if blank.rstrip(':') == 'ports' and blank.endswith(':'):
            in_ports = True
            continue
        if in_ports:
            if not blank.startswith('- '):
                in_ports = False
                continue
            mappings.append(blank[2:].strip().strip('"\''))

    assert mappings, 'docker-compose.yml veröffentlicht keinen Port mehr - Fixture kaputt?'
    unsicher = [m for m in mappings
                if not (m.startswith('127.0.0.1:') or m.startswith('[::1]:'))]
    assert not unsicher, (
        f'Portzuordnung(en) nicht an localhost gebunden: {unsicher} - '
        'die App ist damit am TLS-terminierenden Reverse-Proxy vorbei erreichbar')


# ── Befund M8: HTTPS=0 aus der .env darf nicht lautlos durchgehen ───────────

def test_https_null_im_container_wird_laut_gewarnt(monkeypatch, caplog):
    """`env_file: .env` in docker-compose.yml ueberschreibt das `ENV HTTPS=1`
    aus dem Dockerfile. Ein stehengebliebenes HTTPS=0 in einer alten .env
    schaltet damit das Secure-Flag wieder ab - vorher vollkommen lautlos."""
    import logging

    monkeypatch.setenv('KASSENSTUERZLE_IM_CONTAINER', '1')
    monkeypatch.setenv('HTTPS', '0')

    with caplog.at_level(logging.WARNING):
        aktiv = app_paket._secure_cookie_aktiv()

    assert aktiv is False, 'HTTPS=0 bleibt der ausdrueckliche Ausstieg'
    meldungen = [r.getMessage() for r in caplog.records if r.levelname == 'WARNING']
    assert any('HTTPS=0' in m and 'Secure' in m for m in meldungen), (
        'Kein Warnhinweis beim Start - ein vergessenes HTTPS=0 in der .env '
        f'faellt damit niemandem auf. Meldungen: {meldungen}')


def test_kein_warnhinweis_beim_bewussten_klartextlauf_ausserhalb(monkeypatch, caplog):
    """Gegenprobe: lokal (kein Container) ist HTTPS=0 der Normalfall - dort
    waere die Warnung nur Rauschen und wuerde mit der Zeit ignoriert."""
    import logging

    monkeypatch.delenv('KASSENSTUERZLE_IM_CONTAINER', raising=False)
    monkeypatch.setattr(app_paket.os.path, 'exists', lambda p: False)
    monkeypatch.setenv('HTTPS', '0')

    with caplog.at_level(logging.WARNING):
        assert app_paket._secure_cookie_aktiv() is False
    assert not [r for r in caplog.records
                if r.levelname == 'WARNING' and 'HTTPS=0' in r.getMessage()]


# ── Befund M8: DATABASE_PATH ohne Verzeichnisanteil ────────────────────────

def test_blosser_dateiname_in_database_path_bricht_den_start_nicht(monkeypatch, tmp_path):
    """`os.path.dirname('kassenstuerzle.db')` ist der leere String, und
    `os.makedirs('')` wirft FileNotFoundError - der Start scheiterte also,
    bevor irgendetwas lief. Gemeint ist in dem Fall das Arbeitsverzeichnis."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DATABASE_PATH', 'kassenstuerzle.db')
    monkeypatch.setenv('DISABLE_RATE_LIMIT', '1')

    # Bewusst ueber create_app(): dort steht die Ableitung des
    # Datenverzeichnisses, um die es geht. Die Datenbank selbst liegt weiter
    # am Pfad, den main.py beim Import ermittelt hat - dieser Test fasst also
    # weder data/ noch data_/ an.
    create_app()

    assert (tmp_path / '.secret_key').exists(), (
        'Der Signierschluessel landete nicht im Arbeitsverzeichnis')
