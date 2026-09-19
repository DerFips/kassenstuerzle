"""Regressionstests für Befund I9 (Sicherheitsprüfung 2026-09-18):
stille Klartext-Rückfallebene.

`create_app()` rief `_migrate_to_encrypted()` nur auf, wenn
`_SQLCIPHER_AVAILABLE` bereits wahr war. Die einzige Warnung für den
Gegenfall lag *innerhalb* dieser Funktion und konnte von dort aus nie feuern;
`_open_db()` überspringt `PRAGMA key` ebenfalls kommentarlos. Scheiterte bei
einem Rebuild die Installation von `sqlcipher3`, lief die App vollständig
unverschlüsselt weiter, während der Betreiber seine Finanzdaten für geschützt
hielt.

Diese Tests fassen weder data/ noch data_/ an: die Testsuite arbeitet auf
einer Wegwerf-Datenbank (siehe conftest.py), und der fatale Fall bricht ab,
bevor überhaupt eine Verbindung geöffnet wird.
"""
import logging

import pytest
from flask import Flask

from app import create_app
from app.routes import main as main_module


@pytest.fixture()
def treiber_fehlt_aber_schluessel_gesetzt(monkeypatch):
    """Stellt genau die gefährliche Kombination her: Betreiber hat einen
    Schlüssel konfiguriert, der SQLCipher-Treiber ist aber nicht da.

    Der Schlüssel wird am Modul gesetzt, nicht in der Umgebung: `_DB_KEY`
    wird beim Import von main.py einmalig ausgewertet (main.py:47), eine
    nachträgliche ENV-Variable sähe die App nicht mehr. Es ist ein
    Wegwerf-Wert - die echte .env wird nicht gelesen.
    """
    monkeypatch.setattr(main_module, '_SQLCIPHER_AVAILABLE', False)
    monkeypatch.setattr(main_module, '_DB_KEY', 'wegwerf-testschluessel')
    monkeypatch.delenv('ALLOW_UNENCRYPTED', raising=False)


def test_start_wird_verweigert_wenn_schluessel_gesetzt_aber_treiber_fehlt(
        treiber_fehlt_aber_schluessel_gesetzt, caplog):
    """DER Regressionswächter: die App darf in dieser Lage nicht starten.

    Mit dem alten Code lief `create_app()` hier klaglos durch und legte die
    Datenbank im Klartext an.
    """
    with caplog.at_level(logging.ERROR, logger='app'):
        with pytest.raises(RuntimeError) as fehler:
            create_app()

    text = str(fehler.value)
    assert 'DB_ENCRYPTION_KEY' in text
    assert 'ALLOW_UNENCRYPTED' in text, (
        'Die Fehlermeldung muss den Notausgang nennen, sonst steht der '
        'Betreiber ohne Handlungsanweisung da')

    fehlermeldungen = [s for s in caplog.records if s.levelno >= logging.ERROR]
    assert fehlermeldungen, 'Es wurde nichts auf ERROR-Level geloggt'
    assert any('sqlcipher3' in s.getMessage() for s in fehlermeldungen), (
        'Das Log nennt den fehlenden Treiber nicht beim Namen')


def test_allow_unencrypted_laesst_den_start_durch(
        treiber_fehlt_aber_schluessel_gesetzt, monkeypatch, caplog):
    """Notausgang für alle, die wirklich Klartext wollen.

    Hinweis: allein wäre diese Prüfung KEIN Regressionswächter - sie war auch
    mit dem alten Code grün. Sie sichert, dass die Sperre eine dokumentierte
    Tür hat und nicht als unumgehbar gebaut wurde.
    """
    monkeypatch.setenv('ALLOW_UNENCRYPTED', '1')

    with caplog.at_level(logging.ERROR, logger='app'):
        anwendung = create_app()

    assert isinstance(anwendung, Flask)
    assert any('ALLOW_UNENCRYPTED' in s.getMessage()
               for s in caplog.records if s.levelno >= logging.ERROR), (
        'Auch der bewusste Klartextbetrieb muss unübersehbar protokolliert werden')


def test_normale_testkonfiguration_startet_weiterhin_still(caplog):
    """Kein Schlüssel und kein Treiber ist ein gültiger unverschlüsselter
    Aufbau - genau so läuft diese Testsuite (conftest.py entfernt
    DB_ENCRYPTION_KEY). Diese Kombination darf weder abbrechen noch lärmen.

    Auch das ist kein Wächter für I9 selbst, sondern dagegen, dass die neue
    Sperre zu breit feuert.
    """
    assert main_module._DB_KEY == '', 'conftest soll ohne DB_ENCRYPTION_KEY laufen'
    assert main_module._SQLCIPHER_AVAILABLE is False, (
        'Erwartet wird eine Umgebung ohne sqlcipher3')

    with caplog.at_level(logging.ERROR, logger='app'):
        anwendung = create_app()

    assert isinstance(anwendung, Flask)
    assert not [s for s in caplog.records if s.levelno >= logging.ERROR], (
        'Der unverschlüsselte Normalfall darf nichts auf ERROR-Level loggen: '
        f'{[s.getMessage() for s in caplog.records]}')
