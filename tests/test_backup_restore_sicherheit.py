"""Regressionstests zu den Befunden K2, I4 und M2 der Sicherheitspruefung.

K2: /api/backup/restore akzeptierte jede Datei, die sich als SQLite oeffnen
    liess - eine handgebaute users-Tabelle uebernahm die Instanz, eine
    0-Byte-Datei loeschte sie.
I4: Bei gesetztem Schluessel wurde auf eine schluessellose Verbindung
    zurueckgefallen; eine Klartext-Sicherung legte die Instanz dauerhaft lahm.
M2: Ein selbst vergebenes Label 'pre-restore' verdraengte die automatischen
    Schutzkopien - also genau den Rueckweg aus einem misslungenen Restore.
"""
import os
import sqlite3 as _sqlite3
from pathlib import Path

import pytest

from app.routes import main as m
from app.translations import translate


@pytest.fixture()
def kein_dateitausch(monkeypatch):
    """Faengt den Dateitausch ab und protokolliert ihn.

    Zweierlei Nutzen: die Behauptung 'die Datenbank wurde NICHT angefasst'
    laesst sich direkt pruefen, und wenn der Fix zum Gegenbeweis
    zurueckgenommen wird, ueberschreibt der dann durchlaufende Restore nicht
    die Datenbank der Testsession.
    """
    aufrufe = []

    def gefangenes_copy(src, dst, *args, **kwargs):
        aufrufe.append((str(src), str(dst)))
        return dst

    monkeypatch.setattr(m.shutil, 'copy', gefangenes_copy)
    return aufrufe


def _hochladen(auth_client, csrf, pfad, name=None, bestaetigt=False):
    with open(pfad, 'rb') as fh:
        daten = {'file': (fh, name or Path(pfad).name)}
        if bestaetigt:
            daten['fremde_instanz_bestaetigt'] = 'true'
        return auth_client.post('/api/backup/restore',
                                data=daten,
                                headers={'X-CSRF-Token': csrf},
                                content_type='multipart/form-data')


def _benutzernamen_der_laufenden_instanz():
    conn = m._open_db(m.DB_PATH)
    try:
        return {r[0] for r in conn.execute('SELECT username FROM users')}
    finally:
        conn.close()


# ── K2 ───────────────────────────────────────────────────────────────────────

def test_restore_lehnt_gebastelte_users_datei_ab(auth_client, csrf, tmp_path,
                                                 kein_dateitausch):
    """Der Angriff aus dem Bericht: eine 3 kB grosse Datei, die nur eine
    users-Zeile mit role='admin' enthaelt. Vorher: 200, danach war das echte
    Adminkonto weg."""
    boese = tmp_path / 'evil.db'
    con = _sqlite3.connect(str(boese))
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,"
                " role TEXT NOT NULL DEFAULT 'member',"
                " is_active INTEGER NOT NULL DEFAULT 1,"
                " session_epoch INTEGER NOT NULL DEFAULT 0,"
                " must_change_password INTEGER NOT NULL DEFAULT 0,"
                " person_id INTEGER)")
    con.execute("INSERT INTO users (username, password_hash, role)"
                " VALUES ('angreifer', 'egal', 'admin')")
    con.commit()
    con.close()

    res = _hochladen(auth_client, csrf, boese)

    assert res.status_code == 400, 'Eine fremde Bastel-Datenbank wurde uebernommen'
    assert res.get_json()['success'] is False
    assert kein_dateitausch == [], 'Die Datenbank wurde trotz Ablehnung ersetzt'
    assert 'angreifer' not in _benutzernamen_der_laufenden_instanz()
    assert 'admin' in _benutzernamen_der_laufenden_instanz()


def test_restore_lehnt_minimaldatei_mit_passendem_adminnamen_ab(
        auth_client, csrf, tmp_path, kein_dateitausch):
    """Die gefaehrlichere Variante: der Angreifer kennt den Adminnamen und legt
    ihn mit SEINEM Passwort-Hash an. Die Herkunftspruefung allein wuerde das
    durchlassen - erst die Schemapruefung faengt es ab, weil einer echten
    Sicherung dieser Anwendung nie die uebrigen Tabellen fehlen."""
    boese = tmp_path / 'admin_uebernahme.db'
    con = _sqlite3.connect(str(boese))
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,"
                " role TEXT NOT NULL DEFAULT 'member',"
                " is_active INTEGER NOT NULL DEFAULT 1,"
                " session_epoch INTEGER NOT NULL DEFAULT 0,"
                " must_change_password INTEGER NOT NULL DEFAULT 0,"
                " person_id INTEGER)")
    con.execute("INSERT INTO users (username, password_hash, role)"
                " VALUES ('admin', 'hash-des-angreifers', 'admin')")
    con.commit()
    con.close()

    res = _hochladen(auth_client, csrf, boese)

    assert res.status_code == 400
    assert res.get_json()['error'].startswith(
        translate('de', 'srv.restore_missing_tables', fehlend='')[:40])
    assert kein_dateitausch == []


def test_restore_lehnt_fremde_sicherung_ohne_aktuellen_admin_ab(
        auth_client, csrf, sicherung_bauen, kein_dateitausch):
    """Schema vollstaendig, aber das angemeldete Konto kommt darin nicht vor.

    Genau die realistische Variante des Angriffs: eine vollwertige, aus einer
    anderen Instanz stammende Sicherung wuerde die Anlage an fremde Konten
    uebergeben - _ensure_admin() haette eine solche Datei sogar noch repariert.
    """
    fremd = sicherung_bauen('fremd.db',
                            benutzer=(('angreifer', 'geheim-123', 'admin'),))

    res = _hochladen(auth_client, csrf, fremd)

    assert res.status_code == 400
    assert res.get_json()['error'] == translate('de', 'srv.restore_admin_missing',
                                                benutzer='admin')
    assert kein_dateitausch == []
    assert 'angreifer' not in _benutzernamen_der_laufenden_instanz()


def test_restore_lehnt_null_byte_datei_ab(auth_client, csrf, tmp_path,
                                          kein_dateitausch):
    """Eine 0-Byte-Datei ist eine gueltige LEERE SQLite-Datenbank - vorher
    bestand sie die Pruefung und loeschte den kompletten Bestand."""
    leer = tmp_path / 'evil.db'
    leer.write_bytes(b'')

    res = _hochladen(auth_client, csrf, leer)

    assert res.status_code == 400
    assert res.get_json()['error'] == translate('de', 'srv.restore_empty')
    assert kein_dateitausch == []


@pytest.mark.parametrize('zusatz, bezeichnung', [
    ("CREATE TRIGGER boeser_trigger AFTER INSERT ON expenses BEGIN"
     " UPDATE users SET role='admin'; END", 'boeser_trigger'),
    ("CREATE VIEW heimliche_sicht AS SELECT * FROM users", 'heimliche_sicht'),
])
def test_restore_lehnt_trigger_und_views_ab(auth_client, csrf, sicherung_bauen,
                                            kein_dateitausch, zusatz, bezeichnung):
    """Kassenstuerzle legt weder Views noch Trigger an. Ein Trigger in der
    hochgeladenen Datei wuerde bei jedem spaeteren Schreibzugriff fremden
    SQL-Code im Kontext der Anwendung ausfuehren."""
    praepariert = sicherung_bauen('mit_trigger.db', zusatz_sql=(zusatz,))

    res = _hochladen(auth_client, csrf, praepariert)

    assert res.status_code == 400
    assert res.get_json()['error'] == translate('de', 'srv.restore_objects',
                                                objekte=bezeichnung)
    assert kein_dateitausch == []


# ── Blocker 1: Herkunftsnachweis ueber die Instanzkennung ────────────────────

def test_instanzkennung_wird_angelegt_und_landet_in_jeder_sicherung(app):
    """Grundlage des Nachweises: die Kennung existiert nach dem Start, und
    eine mit den Bordmitteln erzeugte Sicherung traegt sie mit."""
    conn = m._open_db(m.DB_PATH)
    try:
        eigene = m._lies_instanz_id(conn)
    finally:
        conn.close()
    assert eigene, 'Die Instanzkennung fehlt - der Herkunftsnachweis ist wirkungslos'
    assert len(eigene) >= 32, f'Zu kurze/ratbare Instanzkennung: {eigene!r}'

    backup_dir = Path(os.path.dirname(m.DB_PATH)) / 'backups'
    backup_dir.mkdir(exist_ok=True)
    with app.app_context():
        sicherung = m.create_backup('instanzprobe')
    try:
        con = _sqlite3.connect(str(sicherung))
        try:
            in_sicherung = m._lies_instanz_id(con)
        finally:
            con.close()
        assert in_sicherung == eigene, (
            'Die Sicherung traegt die Instanzkennung nicht mit - damit liesse '
            'sich keine einzige echte Sicherung mehr einspielen')
    finally:
        sicherung.unlink(missing_ok=True)


def test_restore_lehnt_vollstaendige_bastel_db_mit_adminnamen_ab(
        auth_client, csrf, sicherung_bauen, kein_dateitausch):
    """DER Angriff aus dem Bericht, in seiner wirksamen Form.

    Der Angreifer baut keine 3-kB-Stummeldatei, sondern eine vollstaendige
    Kassenstuerzle-Datenbank - alle Pflichttabellen, Integritaet in Ordnung,
    keine Views/Trigger - und nennt sein Konto 'admin', genau wie der
    gebootstrappte Admin dieser Installation heisst. Die alte Herkunfts-
    pruefung (Benutzername des Angemeldeten kommt in der Datei vor) bestand
    er damit muehelos: 200, danach war die Instanz seine.

    Was ihn jetzt aufhaelt, ist einzig die fehlende Instanzkennung.
    """
    boese = sicherung_bauen(
        'uebernahme_komplett.db',
        benutzer=(('admin', 'passwort-des-angreifers', 'admin'),),
        instanz_id=None)

    res = _hochladen(auth_client, csrf, boese)

    assert res.status_code == 400, (
        'Eine vollstaendig gebaute Fremddatenbank mit dem Konto "admin" wurde '
        'uebernommen - der Angreifer ist jetzt Admin dieser Installation')
    daten = res.get_json()
    assert daten['success'] is False
    assert daten['grund'] == 'srv.restore_keine_instanz_id'
    assert daten['error'] == translate('de', 'srv.restore_keine_instanz_id')
    assert kein_dateitausch == [], 'Die Datenbank wurde trotz Ablehnung ersetzt'

    # Und das echte Adminkonto ist noch da - mit dem echten Passwort.
    assert 'admin' in _benutzernamen_der_laufenden_instanz()
    nachher = auth_client.get('/settings')
    assert nachher.status_code == 200


def test_restore_lehnt_sicherung_einer_fremden_installation_ab(
        auth_client, csrf, sicherung_bauen, kein_dateitausch):
    """Eine echte, vollwertige Sicherung - nur eben aus einer anderen
    Installation. Hier hilft auch keine Bestaetigung: das ist nie ein
    gewolltes Wiederherstellen, sondern immer eine Verwechslung oder ein
    untergeschobenes Backup."""
    fremd = sicherung_bauen('fremde_installation.db',
                            instanz_id='ff' * 16)

    for bestaetigt in (False, True):
        res = _hochladen(auth_client, csrf, fremd, bestaetigt=bestaetigt)
        assert res.status_code == 400, (
            f'Fremde Installation angenommen (bestaetigt={bestaetigt})')
        assert res.get_json()['error'] == translate('de', 'srv.restore_fremde_instanz')
        assert res.get_json()['grund'] == 'srv.restore_fremde_instanz'
    assert kein_dateitausch == []


def test_alte_sicherung_ohne_kennung_nur_mit_ausdruecklicher_bestaetigung(
        app, auth_client, csrf, sicherung_bauen, caplog):
    """Sicherungen von VOR dieser Aenderung tragen keine Kennung. Sie
    pauschal abzulehnen wuerde dem Betreiber jeden Rueckweg nehmen - also
    Ablehnung im ersten Anlauf, Annahme erst nach dem ausdruecklichen
    Kennzeichen, das die Oberflaeche nur nach einer Rueckfrage setzt."""
    alt = sicherung_bauen('alte_sicherung.db', instanz_id=None)
    con = _sqlite3.connect(str(alt))
    con.execute("INSERT INTO persons (name) VALUES ('Altbestand')")
    con.commit()
    con.close()

    backup_dir = Path(os.path.dirname(m.DB_PATH)) / 'backups'
    backup_dir.mkdir(exist_ok=True)
    for f in backup_dir.glob('kassenstuerzle_*.db'):
        f.unlink()
    try:
        ohne = _hochladen(auth_client, csrf, alt)
        assert ohne.status_code == 400
        assert ohne.get_json()['grund'] == 'srv.restore_keine_instanz_id'

        import logging
        with caplog.at_level(logging.WARNING):
            mit = _hochladen(auth_client, csrf, alt, bestaetigt=True)
        assert mit.status_code == 200, (
            'Mit ausdruecklicher Bestaetigung muss die Altsicherung durchgehen - '
            'sonst ist jede bestehende Sicherung des Betreibers wertlos')
        assert mit.get_json() == {'success': True}

        # Die Uebersteuerung MUSS nachvollziehbar sein.
        assert any('OHNE Herkunftsnachweis' in r.getMessage()
                   for r in caplog.records if r.levelname == 'WARNING'), (
            'Die Freigabe ohne Herkunftsnachweis wurde nicht protokolliert')

        conn = m._open_db(m.DB_PATH)
        try:
            namen = {r[0] for r in conn.execute('SELECT name FROM persons')}
        finally:
            conn.close()
        assert 'Altbestand' in namen
    finally:
        conn = m._open_db(m.DB_PATH)
        conn.execute("DELETE FROM persons WHERE name='Altbestand'")
        conn.commit()
        conn.close()
        with app.app_context():
            m.init_db()
        for f in backup_dir.glob('kassenstuerzle_*.db'):
            f.unlink()


# ── I4 ───────────────────────────────────────────────────────────────────────

def test_restore_lehnt_klartext_datei_bei_gesetztem_schluessel_ab(
        auth_client, csrf, sicherung_bauen, kein_dateitausch, monkeypatch):
    """Befund I4: eine Datei, die sich NUR ohne Schluessel oeffnen laesst, darf
    eine verschluesselte Instanz nicht erreichen.

    sqlcipher3 ist in .venv-dev nicht installiert, die verschluesselte
    Konfiguration wird deshalb nachgestellt: `_open_db` verhaelt sich fuer
    fremde Pfade so, wie SQLCipher sich bei einer Klartextdatei verhaelt
    ('file is not a database'), fuer die laufende Datenbank unveraendert.
    Ohne den Fix faellt die Pruefung auf eine schluessellose Verbindung zurueck,
    nimmt die Datei an - und danach scheitert JEDER Request inklusive /login.
    """
    echt_open_db = m._open_db

    def nur_mit_passendem_schluessel(pfad):
        if os.path.abspath(str(pfad)) != os.path.abspath(m.DB_PATH):
            raise m.sqlite3.DatabaseError('file is not a database')
        return echt_open_db(pfad)

    monkeypatch.setattr(m, '_open_db', nur_mit_passendem_schluessel)
    monkeypatch.setattr(m, '_SQLCIPHER_AVAILABLE', True)
    monkeypatch.setattr(m, '_DB_KEY', 'geheimer-testschluessel')

    # Inhaltlich voellig einwandfreie Sicherung - nur eben im Klartext.
    klartext = sicherung_bauen('unverschluesselt.db')

    res = _hochladen(auth_client, csrf, klartext)

    assert res.status_code == 400, (
        'Eine nur schluessellos lesbare Datei wurde uebernommen - danach ist '
        'die Instanz nur noch per Shell-Zugriff zu retten')
    assert res.get_json()['error'] == translate('de', 'srv.restore_wrong_key')
    assert kein_dateitausch == []


# ── M2 ───────────────────────────────────────────────────────────────────────

def _schutzkopien_anlegen(backup_dir, anzahl):
    erzeugt = []
    for i in range(anzahl):
        pfad = backup_dir / f'kassenstuerzle_2026-01-01_00-{i:02d}_pre-restore.db'
        pfad.write_bytes(b'echte schutzkopie')
        os.utime(pfad, (1_000_000 + i, 1_000_000 + i))
        erzeugt.append(pfad)
    return erzeugt


def test_benutzerlabel_kann_schutzkopien_nicht_verdraengen(auth_client, csrf):
    """Befund M2: fuenfmal POST mit dem Label 'pre-restore' verdraengte jede
    echte Schutzkopie - den einzigen Rueckweg aus einem misslungenen Restore."""
    backup_dir = Path(os.path.dirname(m.DB_PATH)) / 'backups'
    backup_dir.mkdir(exist_ok=True)
    for f in backup_dir.glob('kassenstuerzle_*.db'):
        f.unlink()
    try:
        echte = _schutzkopien_anlegen(backup_dir, m._MAX_SCHUTZ_BACKUPS)

        for _ in range(m._MAX_SCHUTZ_BACKUPS):
            res = auth_client.post('/api/backup/create', json={'label': 'pre-restore'},
                                   headers={'X-CSRF-Token': csrf})
            assert res.status_code == 400, 'Reserviertes Label wurde angenommen'
            assert res.get_json()['error'] == translate(
                'de', 'srv.reserved_backup_label', label='pre-restore')

        assert all(p.exists() for p in echte), (
            'Ein selbst vergebenes Label hat die echten Schutzkopien verdraengt')
    finally:
        for f in backup_dir.glob('kassenstuerzle_*.db'):
            f.unlink()


def test_create_backup_stuft_nur_nach_aufrufer_als_schutzkopie_ein(app):
    """Die Einstufung haengt am Aufrufer, nicht am Dateinamen: selbst wenn ein
    kuenftiger Aufrufer ein ungeprueftes Label durchreicht, darf daraus keine
    Datei entstehen, die die Rotation als Schutzkopie zaehlt."""
    backup_dir = Path(os.path.dirname(m.DB_PATH)) / 'backups'
    backup_dir.mkdir(exist_ok=True)
    for f in backup_dir.glob('kassenstuerzle_*.db'):
        f.unlink()
    try:
        with app.app_context():
            vom_benutzer = m.create_backup('pre-restore')
            intern = m.create_backup('pre-restore', schutz=True)

        assert not m._ist_schutzkopie(vom_benutzer.name), (
            f'Benutzer-Label ergab eine Schutzkopie: {vom_benutzer.name}')
        assert m._ist_schutzkopie(intern.name), (
            'Die interne Schutzkopie wird nicht mehr als solche erkannt - '
            f'{intern.name}')
        assert vom_benutzer.name != intern.name
    finally:
        for f in backup_dir.glob('kassenstuerzle_*.db'):
            f.unlink()


# ── Gegenprobe ───────────────────────────────────────────────────────────────

def test_echte_sicherung_wird_weiterhin_eingespielt(app, auth_client, csrf,
                                                    sicherung_bauen):
    """Eine Pruefung, die alles ablehnt, waere keine Behebung: eine echte
    Sicherung dieser Instanz muss sich unveraendert einspielen lassen."""
    gut = sicherung_bauen('echte_sicherung.db')
    con = _sqlite3.connect(str(gut))
    con.execute("INSERT INTO persons (name) VALUES ('Sicherungsprobe')")
    con.commit()
    con.close()

    backup_dir = Path(os.path.dirname(m.DB_PATH)) / 'backups'
    backup_dir.mkdir(exist_ok=True)
    for f in backup_dir.glob('kassenstuerzle_*.db'):
        f.unlink()

    try:
        res = _hochladen(auth_client, csrf, gut)

        assert res.status_code == 200
        assert res.get_json() == {'success': True}

        conn = m._open_db(m.DB_PATH)
        try:
            namen = {r[0] for r in conn.execute('SELECT name FROM persons')}
            benutzer = {r[0] for r in conn.execute('SELECT username FROM users')}
        finally:
            conn.close()
        assert 'Sicherungsprobe' in namen, 'Die Sicherung wurde nicht eingespielt'
        assert 'admin' in benutzer

        # Der Rueckweg muss weiter angelegt werden: ohne diese Schutzkopie
        # waere ein unbeabsichtigter Restore nicht mehr rueckgaengig zu machen.
        schutzkopien = [f for f in backup_dir.glob('kassenstuerzle_*.db')
                        if f.name.endswith('_pre-restore.db')]
        assert schutzkopien, 'Vor dem Tausch wurde keine pre-restore-Sicherung angelegt'
        assert all(m._ist_schutzkopie(f.name) for f in schutzkopien)

        # Und die Instanz ist danach ganz normal benutzbar.
        client = app.test_client()
        anmeldung = client.post('/login', data={'username': 'admin',
                                                'password': 'test-password-123'})
        assert anmeldung.status_code == 302
        assert '/login' not in anmeldung.headers['Location']
        assert client.get('/overview').status_code == 200
    finally:
        conn = m._open_db(m.DB_PATH)
        conn.execute("DELETE FROM persons WHERE name='Sicherungsprobe'")
        conn.commit()
        conn.close()
        with app.app_context():
            m.init_db()
        backup_dir = Path(os.path.dirname(m.DB_PATH)) / 'backups'
        for f in backup_dir.glob('kassenstuerzle_*.db'):
            f.unlink()
