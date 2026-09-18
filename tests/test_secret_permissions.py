"""Regressionstests für Befund I2 (Sicherheitsprüfung 2026-09-18):
Geheimnisse und Nutzdaten wurden mit Modus 644 angelegt, das
Datenverzeichnis mit den Standardrechten des Prozess-umask - jeder mit
Lesezugriff auf den Datenträger bekäme z.B. den Flask-Signierschlüssel und
könnte damit eine Admin-Sitzung fälschen, ganz ohne Passwort.

WICHTIG: Jeder Test hier arbeitet ausschließlich in einem Wegwerf-Verzeichnis
(tmp_path bzw. dem Testdatenverzeichnis aus DATABASE_PATH, siehe conftest.py).
Das echte data/ bzw. data_/ Verzeichnis des Projekts wird nirgends berührt.
"""
import io
import os
import stat
from pathlib import Path

import app as app_package
from app.routes import main as m


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_ensure_data_dir_and_secret_creates_0700_dir(tmp_path):
    """Ein neu angelegtes Datenverzeichnis muss 0700 sein (nur der eigene
    Benutzer darf hineinsehen - es enthält den Signierschlüssel)."""
    data_dir = tmp_path / 'frisches_datenverzeichnis'
    assert not data_dir.exists()

    app_package._ensure_data_dir_and_secret(str(data_dir))

    assert data_dir.is_dir()
    assert _mode(data_dir) == 0o700, (
        f"Datenverzeichnis hat Modus {oct(_mode(data_dir))}, erwartet 0700")


def test_ensure_data_dir_and_secret_creates_0600_key(tmp_path):
    """Ein frisch erzeugter Secret-Key darf nur vom eigenen Benutzer lesbar
    sein - er signiert die Sitzungscookies. Mit Modus 644 könnte jeder mit
    Dateisystemzugriff eine Admin-Sitzung fälschen (siehe Befund I2)."""
    data_dir = tmp_path / 'noch_ein_datenverzeichnis'

    secret = app_package._ensure_data_dir_and_secret(str(data_dir))

    secret_file = data_dir / '.secret_key'
    assert secret_file.is_file()
    assert secret_file.read_bytes() == secret
    assert _mode(secret_file) == 0o600, (
        f"Secret-Key hat Modus {oct(_mode(secret_file))}, erwartet 0600")


def test_ensure_data_dir_and_secret_leaves_existing_dir_alone(tmp_path):
    """Ein bereits bestehendes Datenverzeichnis (z.B. ein Bind-Mount mit
    bewusst anderen Rechten) darf NICHT nachträglich verändert werden - nur
    bei Neuanlage wird chmod aufgerufen."""
    data_dir = tmp_path / 'bereits_da'
    data_dir.mkdir(mode=0o755)
    os.chmod(data_dir, 0o755)  # mkdir(mode=...) unterliegt dem umask, explizit setzen

    app_package._ensure_data_dir_and_secret(str(data_dir))

    assert _mode(data_dir) == 0o755, 'Ein bereits bestehendes Verzeichnis wurde veraendert'


def test_create_backup_is_0600(app):
    """Eine Sicherung enthält dieselben Finanzdaten wie die Hauptdatenbank
    und darf nicht mit dem Standard-umask (644) abgelegt werden."""
    with app.app_context():
        dest = m.create_backup('berechtigungstest')
    try:
        assert dest.exists()
        assert _mode(dest) == 0o600, (
            f"Sicherung hat Modus {oct(_mode(dest))}, erwartet 0600")
    finally:
        if dest.exists():
            dest.unlink()


def test_receipt_upload_is_0600(auth_client, csrf):
    """Ein hochgeladener Beleg enthält personenbezogene Finanzdaten und darf
    nicht mit dem Standard-umask (644) auf der Platte landen.

    Ein PDF statt eines Bildes, damit der Test nicht von Pillow abhängt
    (im Dev-venv nicht installiert, siehe requirements-dev.txt) - die
    Belegprüfung braucht dafür nur die Magic Bytes `%PDF-`.
    """
    buf = io.BytesIO(b'%PDF-1.4\n%Wegwerf-Testbeleg, kein echtes PDF\n')

    res = auth_client.post(
        '/api/receipt/upload',
        data={'file': (buf, 'beleg.pdf')},
        headers={'X-CSRF-Token': csrf},
        content_type='multipart/form-data',
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body['success'] is True
    filename = body['filename']
    dest = Path(m.RECEIPTS_DIR) / filename
    try:
        assert dest.is_file()
        assert _mode(dest) == 0o600, (
            f"Beleg hat Modus {oct(_mode(dest))}, erwartet 0600")
    finally:
        if dest.exists():
            dest.unlink()


def test_migration_removes_unencrypted_backup(tmp_path, monkeypatch):
    """Regression Befund I1: nach einer erfolgreich VERIFIZIERTEN Migration
    darf keine Klartext-Kopie (`<db>.unencrypted.bak`) mehr auf der Platte
    liegen - sie hebt sonst die Verschlüsselung der gesamten Finanzhistorie
    auf, solange sie im selben Verzeichnis liegt.

    sqlcipher3 ist im Dev-venv nicht installiert (siehe requirements-dev.txt),
    deshalb wird hier die SQLCipher-spezifische Verbindung simuliert (ATTACH
    ... KEY / sqlcipher_export gibt es in Standard-sqlite3 nicht). Getestet
    wird damit die tatsächliche Ablaufsteuerung von `_migrate_to_encrypted`
    (inkl. Verifikation und Aufräumen) - nicht deren SQLCipher-Interna.
    """
    db_path = tmp_path / 'wegwerf_kassenstuerzle.db'
    db_path.write_bytes(b'unverschluesselter platzhalter-inhalt')
    key = 'wegwerf-schluessel'

    class FakeConn:
        _pragma_key_calls = 0

        def execute(self, sql, params=()):
            if sql.startswith('PRAGMA key'):
                type(self)._pragma_key_calls += 1
                if type(self)._pragma_key_calls == 1:
                    # Erste Pruefung ("ist die DB schon verschluesselt?"):
                    # schlaegt fehl -> Migration wird ausgeloest.
                    raise Exception('Simulation: noch nicht verschluesselt')
                # Zweite Pruefung = Verifikation NACH der Migration: erfolgreich.
            return self

        def fetchone(self):
            return (0,)

        def close(self):
            pass

    monkeypatch.setattr(m, '_SQLCIPHER_AVAILABLE', True)
    monkeypatch.setattr(m.sqlite3, 'connect', lambda path: FakeConn())

    m._migrate_to_encrypted(str(db_path), key)

    backup_path = str(db_path) + '.unencrypted.bak'
    assert not os.path.exists(backup_path), (
        'Klartext-Sicherung wurde nach erfolgreicher Migration nicht entfernt')


def test_migration_keeps_backup_if_verification_fails(tmp_path, monkeypatch):
    """Gegenprobe: schlaegt die Verifikation der neuen Datenbank fehl, MUSS
    die Klartext-Sicherung als Rueckfallebene erhalten bleiben."""
    db_path = tmp_path / 'wegwerf_kassenstuerzle2.db'
    db_path.write_bytes(b'unverschluesselter platzhalter-inhalt')
    key = 'wegwerf-schluessel'

    class FakeConn:
        _calls = 0

        def execute(self, sql, params=()):
            if sql.startswith('PRAGMA key'):
                type(self)._calls += 1
                # Sowohl die initiale Pruefung als auch die Verifikation
                # danach schlagen fehl -> Migration bleibt "unsicher".
                raise Exception('Simulation: Datenbank oeffnet nicht')
            return self

        def fetchone(self):
            return (0,)

        def close(self):
            pass

    monkeypatch.setattr(m, '_SQLCIPHER_AVAILABLE', True)
    monkeypatch.setattr(m.sqlite3, 'connect', lambda path: FakeConn())

    m._migrate_to_encrypted(str(db_path), key)

    backup_path = str(db_path) + '.unencrypted.bak'
    assert os.path.exists(backup_path), (
        'Klartext-Sicherung wurde entfernt, obwohl die Verifikation fehlschlug')
