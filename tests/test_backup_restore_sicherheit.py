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


# ── Nacharbeit zu Blocker 1: der Nachweis selbst war angreifbar ──────────────
#
# Drei Befunde der Nachpruefung, hier jeweils als vollstaendige Angriffskette:
#  1. Der Vergleich fiel offen aus: ohne lesbare eigene Kennung wurde der
#     Zweig fuer die fremde Installation uebersprungen.
#  2. Die hochgeladene Datei bestimmte das SCHEMA von app_meta mit. Eine
#     Tabelle ohne Primaerschluessel machte `INSERT OR REPLACE` zum Anhaengen
#     und die eigene Kennung dauerhaft unlesbar - eine einzige Bestaetigung
#     im gewohnten Altsicherungs-Dialog genuegte.
#  3. Ein bestaetigter Altrestore rotierte die Kennung und entwertete damit
#     alle eigenen Sicherungen, auch die Sekunden zuvor angelegte Schutzkopie.


def _pfade_der_schutzkopien():
    backup_dir = Path(os.path.dirname(m.DB_PATH)) / 'backups'
    return sorted(backup_dir.glob('kassenstuerzle_*_pre-restore.db'))


@pytest.fixture()
def sauberes_backupverzeichnis(app):
    """Leeres Sicherungsverzeichnis, und danach wieder eine intakte Instanz.

    Die Tests hier tauschen die Datenbank der Testsession wirklich aus - sie
    muessen sie also auch wieder in einen benutzbaren Zustand bringen.
    """
    backup_dir = Path(os.path.dirname(m.DB_PATH)) / 'backups'
    backup_dir.mkdir(exist_ok=True)
    for f in backup_dir.glob('kassenstuerzle_*.db'):
        f.unlink()
    yield backup_dir
    conn = m._open_db(m.DB_PATH)
    try:
        conn.execute("DELETE FROM persons WHERE name IN ('Altbestand','VorDemRestore')")
        conn.commit()
    finally:
        conn.close()
    with app.app_context():
        m.init_db()
    for f in backup_dir.glob('kassenstuerzle_*.db'):
        f.unlink()


def test_ensure_instanz_id_baut_gebasteltes_app_meta_neu_auf(tmp_path):
    """Kern des zweiten Befunds, als Einzelteil.

    init_db() legte app_meta mit `CREATE TABLE IF NOT EXISTS` an - eine in
    der eingespielten Datei mitgelieferte Tabelle `app_meta (key, value)`
    OHNE Primaerschluessel blieb damit bestehen. Darin ersetzt
    `INSERT OR REPLACE` nichts, es haengt an; `SELECT ... fetchone()` liefert
    danach fuer immer die erste, vom Angreifer gewaehlte Zeile.
    """
    pfad = tmp_path / 'gebastelt.db'
    con = _sqlite3.connect(str(pfad))
    con.execute('CREATE TABLE app_meta (key, value)')
    con.execute("INSERT INTO app_meta (key, value) VALUES ('instance_id', '')")
    con.execute("INSERT INTO app_meta (key, value) VALUES ('instance_id', 'zweite')")
    con.commit()
    try:
        kennung = m._ensure_instanz_id(con)

        assert kennung, 'Keine Kennung erzeugt - der Herkunftsnachweis waere tot'
        assert len(kennung) >= 32, f'Zu kurze Kennung: {kennung!r}'
        assert m._lies_instanz_id(con) == kennung, (
            'Die Kennung ist nach dem Anlegen nicht lesbar - genau der '
            'Zustand, in dem jede fremde Sicherung durchgewunken wurde')

        form = [(r[1], str(r[2] or '').upper(), r[5])
                for r in con.execute('PRAGMA table_info(app_meta)')]
        assert form == [('key', 'TEXT', 1), ('value', 'TEXT', 0)], (
            f'app_meta hat weiterhin die untergeschobene Form: {form}')
        anzahl = con.execute("SELECT COUNT(*) FROM app_meta WHERE key='instance_id'"
                             ).fetchone()[0]
        assert anzahl == 1, f'{anzahl} Zeilen fuer instance_id - es darf nur eine geben'
    finally:
        con.close()


def test_ensure_instanz_id_ueberlebt_app_meta_ohne_spalte_value(tmp_path):
    """Zweiter Befund, andere Bauart: app_meta ohne die Spalte `value`.

    `SELECT value FROM app_meta` lief ohne try/except - init_db() flog mit
    'no such column: value' auseinander. Weil init_db() auch in create_app()
    laeuft, startete die Anwendung danach ueberhaupt nicht mehr.
    """
    pfad = tmp_path / 'ohne_value.db'
    con = _sqlite3.connect(str(pfad))
    con.execute('CREATE TABLE app_meta (key TEXT PRIMARY KEY)')
    con.execute("INSERT INTO app_meta (key) VALUES ('instance_id')")
    con.commit()
    try:
        kennung = m._ensure_instanz_id(con)   # darf nicht werfen
        assert kennung and m._lies_instanz_id(con) == kennung
    finally:
        con.close()


@pytest.mark.parametrize('wert, erwartet', [
    (None, None),
    ('', None),
    ('a' * 32, 'a' * 32),
])
def test_lies_instanz_id_wertet_leer_und_null_als_fehlend(tmp_path, wert, erwartet):
    """Der NULL/Leerstring-Zweig von _lies_instanz_id.

    Er ist sicherheitsrelevant: ein leerer Wert MUSS 'fehlt' bedeuten, nicht
    'Kennung ist der leere Text'. Sonst wuerde eine hochgeladene Datei mit
    instance_id='' zur Kennung dieser Installation passen, sobald deren
    eigene Kennung aus irgendeinem Grund ebenfalls leer ist - und der
    Vergleich waere wertlos.
    """
    pfad = tmp_path / 'werte.db'
    con = _sqlite3.connect(str(pfad))
    con.execute('CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT)')
    con.execute("INSERT INTO app_meta (key, value) VALUES ('instance_id', ?)", (wert,))
    con.commit()
    try:
        assert m._lies_instanz_id(con) == erwartet
    finally:
        con.close()


def test_restore_faellt_ohne_eigene_kennung_zu(auth_client, csrf, sicherung_bauen,
                                               kein_dateitausch, monkeypatch):
    """Erster Befund: `elif eigene is not None and fremde != eigene`.

    Konnte die laufende Installation ihre eigene Kennung nicht lesen, wurde
    der ganze Zweig uebersprungen - und JEDE fremde Sicherung angenommen,
    ohne Bestaetigung, ohne Rueckfrage. Fehlt der Nachweis, muss der Restore
    zufallen, nicht aufgehen.
    """
    monkeypatch.setattr(m, '_eigene_instanz_id', lambda: None)
    fremd = sicherung_bauen('fremde_installation.db', instanz_id='ff' * 16)

    for bestaetigt in (False, True):
        res = _hochladen(auth_client, csrf, fremd, bestaetigt=bestaetigt)
        assert res.status_code == 400, (
            'Ohne eigene Instanzkennung wurde eine fremde Sicherung '
            f'eingespielt (bestaetigt={bestaetigt})')
        assert res.get_json()['grund'] == 'srv.restore_kein_eigener_nachweis'
        assert res.get_json()['error'] == translate(
            'de', 'srv.restore_kein_eigener_nachweis')
    assert kein_dateitausch == [], 'Die Datenbank wurde trotz Ablehnung ersetzt'


def test_bestaetigte_altsicherung_macht_die_instanz_nicht_dauerhaft_annahmebereit(
        app, auth_client, csrf, sicherung_bauen, sauberes_backupverzeichnis):
    """Die vollstaendige Kette aus der Nachpruefung.

    Der Angreifer liefert eine "Sicherung von vor dem Update": app_meta mit
    dem Schema `(key, value)` - ohne Primaerschluessel - und instance_id=''.
    Der Admin sieht den ganz gewoehnlichen Altsicherungs-Dialog, denselben,
    den jede echte alte Sicherung ausloest, und bestaetigt einmal.

    Danach war die Installation frueher dauerhaft annahmebereit: ihre eigene
    Kennung liess sich nicht mehr lesen, und jede weitere fremde Datenbank
    ging OHNE jede Bestaetigung mit 200 durch.

    Die Behauptung ist jetzt schaerfer als zuvor: diese Datei kommt gar nicht
    mehr bis zum Bestaetigungsdialog. Ihre app_meta hat nicht den Wortlaut,
    den diese Anwendung selbst schreibt, und wird schon am Tor abgewiesen -
    mit UND ohne Bestaetigungskennzeichen. Frueher stand hier nur, dass die
    Kennung den Restore ueberlebt; das war auch dann noch wahr, wenn man den
    Neuaufbau von app_meta zurueckdrehte.
    """
    praepariert = sicherung_bauen('vor_update_sicherung.db', app_meta_sql=(
        'CREATE TABLE app_meta (key, value)',
        "INSERT INTO app_meta (key, value) VALUES ('instance_id', '')",
    ))
    kennung_vorher = m._eigene_instanz_id()
    assert kennung_vorher

    # Schritt 1: die Datei wird abgewiesen - und zwar unabhaengig davon, ob
    # der Admin den Altsicherungs-Dialog bestaetigt.
    for bestaetigt in (False, True):
        res = _hochladen(auth_client, csrf, praepariert, bestaetigt=bestaetigt)
        assert res.status_code == 400, (
            f'Die praeparierte app_meta ging durch (bestaetigt={bestaetigt})')
        assert res.get_json()['grund'] == 'srv.restore_app_meta_fremd', (
            'Erwartet wird die Ablehnung wegen der fremden app_meta-Form, '
            f"nicht {res.get_json()['grund']!r}")

    # Schritt 2: die Installation kennt ihre eigene Herkunft unveraendert.
    assert m._eigene_instanz_id() == kennung_vorher, (
        'Die eigene Kennung hat sich durch den Angriffsversuch veraendert - '
        'damit waeren alle bestehenden Sicherungen unerreichbar')

    # Schritt 3: und deshalb prallt die naechste fremde Datei weiter ab -
    # ohne Flag, ohne Rueckfrage, mit dem Grund, der nicht uebersteuerbar ist.
    fremd = sicherung_bauen('uebernahme.db', instanz_id='ff' * 16,
                            benutzer=(('admin', 'passwort-des-angreifers', 'admin'),))
    res = _hochladen(auth_client, csrf, fremd)
    assert res.status_code == 400, (
        'Nach dem Angriffsversuch wurde eine voellig fremde Datenbank ohne '
        'jedes Kennzeichen uebernommen - der Angreifer ist jetzt Admin')
    assert res.get_json()['grund'] == 'srv.restore_fremde_instanz'


def test_create_app_startet_nach_restore_mit_kaputtem_app_meta(
        app, auth_client, csrf, sicherung_bauen, sauberes_backupverzeichnis):
    """Der Bricking-Fall, end to end.

    app_meta ohne die Spalte `value` liess init_db() nach dem Dateitausch mit
    'no such column: value' scheitern: 500 zurueck, die Datenbank aber schon
    ersetzt. Weil init_db() auch in create_app() laeuft, startete die
    Anwendung danach gar nicht mehr - mit `restart: unless-stopped` eine
    Neustartschleife, aus der nur ein Shell-Zugriff auf das Volume herausfuehrt.
    """
    from app import create_app

    kaputt = sicherung_bauen('kaputtes_app_meta.db', app_meta_sql=(
        'CREATE TABLE app_meta (key TEXT PRIMARY KEY)',
        "INSERT INTO app_meta (key) VALUES ('instance_id')",
    ))

    antwort = _hochladen(auth_client, csrf, kaputt, bestaetigt=True)
    # Frueher stand hier `in (200, 400)` - das galt, ob die Datei angenommen
    # oder abgelehnt wurde, und bewachte damit nichts. Gewollt ist genau ein
    # Ausgang: die Datei kommt nicht herein. Ihre app_meta hat nicht den
    # Wortlaut, den diese Anwendung schreibt.
    assert antwort.status_code == 400, (
        f'Unerwarteter Status {antwort.status_code} - erwartet wird die '
        'Ablehnung am Tor; ein 500 bedeutet: Datei schon getauscht, '
        'Migration gescheitert')
    assert antwort.get_json()['grund'] == 'srv.restore_app_meta_fremd'

    # Die entscheidende Aussage: die Anwendung startet danach noch.
    zweite = create_app()
    assert zweite is not None
    klient = zweite.test_client()
    assert klient.get('/login').status_code == 200

    # Und der Herkunftsnachweis steht wieder auf eigenen Fuessen.
    assert m._eigene_instanz_id(), (
        'Die Installation hat nach dem Neustart keine lesbare Kennung')

    # Zweiter Teil, und der eigentliche Waechter: dieselbe kaputte Form
    # direkt in der laufenden Datei. Der Restore-Pfad raeumt sie schon beim
    # Festschreiben der Kennung weg - geprueft werden soll aber init_db()
    # selbst. Es laeuft bei JEDEM Start, auch wenn die Datei auf anderem Weg
    # in diesen Zustand geraten ist (Kopie ins Volume, abgebrochener
    # Restore, Wiederherstellung aus einem Dateisystem-Snapshot).
    con = m._open_db(m.DB_PATH)
    try:
        con.execute('DROP TABLE IF EXISTS app_meta')
        con.execute('CREATE TABLE app_meta (key TEXT PRIMARY KEY)')
        con.execute("INSERT INTO app_meta (key) VALUES ('instance_id')")
        con.commit()
    finally:
        con.close()

    dritte = create_app()      # darf nicht mit 'no such column: value' sterben
    assert dritte.test_client().get('/login').status_code == 200
    assert m._eigene_instanz_id(), (
        'Nach dem Neuaufbau von app_meta fehlt die Kennung - damit waere '
        'jeder weitere Restore blockiert')


# ── Dritte Nachpruefung: die Pruefung zaehlte auf, was verboten ist ──────────
#
# Drei Bauarten, die alle drei nur eine einzige Bestaetigung im ganz
# gewoehnlichen Altsicherungs-Dialog brauchten:
#   A  ein INDEX namens `app_meta` (Typ 'index', also nicht 'view'/'trigger')
#   B  eine TABELLE namens `idx_users_person` (init_db() stirbt beim CREATE
#      INDEX, create_app() danach fuer immer)
#   C  eine app_meta mit CHECK bzw. FOREIGN KEY - unter PRAGMA table_info
#      exakt die Sollform, trotzdem nimmt sie kein INSERT an
#
# Alle drei muessen am Tor scheitern, VOR dem Dateitausch. Und danach muss
# die Installation unveraendert arbeitsfaehig sein.


def _instanz_ist_heil(app, auth_client, csrf, kennung_vorher):
    """Gemeinsame Nachbedingung der drei Angriffsfaelle.

    Prueft genau das, was bei den bisherigen Bauarten kaputt ging: die eigene
    Kennung ist weiter lesbar und unveraendert, die Anwendung startet noch,
    und der Rueckweg - die eigene Schutzkopie - laesst sich OHNE Bestaetigung
    einspielen.
    """
    from app import create_app

    assert m._eigene_instanz_id() == kennung_vorher, (
        'Die eigene Instanzkennung ist weg oder rotiert - damit waere kein '
        'einziger Restore mehr moeglich, auch der Rueckweg nicht')

    zweite = create_app()
    assert zweite.test_client().get('/login').status_code == 200, (
        'Die Anwendung startet nach dem Angriffsversuch nicht mehr')

    # Eine echte Schutzkopie dieser Installation - eine Dateikopie der
    # laufenden Datenbank, mit allen Indizes, sqlite_sequence und den
    # impliziten sqlite_autoindex_*. Genau die Datei, die eine zu strenge
    # Positivliste als erstes faelschlich abweisen wuerde.
    with app.app_context():
        schutzkopie = m.create_backup('pre-restore', schutz=True)
    zurueck = _hochladen(auth_client, csrf, schutzkopie)
    assert zurueck.status_code == 200, (
        'Die eigene Schutzkopie laesst sich nicht mehr einspielen: '
        + zurueck.get_data(as_text=True))


def test_restore_lehnt_index_namens_app_meta_ab(
        app, auth_client, csrf, sicherung_bauen, sauberes_backupverzeichnis):
    """Angriff A: ein INDEX, der den Namen der Tabelle app_meta belegt.

    Die Datei ist in jeder Hinsicht eine gueltige Altsicherung - alle
    Pflichttabellen, ein Benutzer 'admin', saubere Integritaet, keine Views,
    keine Trigger. Nur traegt sie keine app_meta-Tabelle, sondern einen
    gleichnamigen Index. Die alte Pruefung sah nur die Typen 'view' und
    'trigger' und liess ihn durch.

    Danach lieferte `PRAGMA table_info(app_meta)` eine leere Liste,
    `DROP TABLE IF EXISTS app_meta` war ein stilles Nichts und das folgende
    CREATE TABLE scheiterte mit 'there is already an index named app_meta'.
    Die Installation hatte ab da DAUERHAFT keine eigene Kennung mehr - und
    weil srv.restore_kein_eigener_nachweis nicht uebersteuerbar ist, liess
    sich keine Datei mehr einspielen, auch die Schutzkopie nicht.
    """
    kennung_vorher = m._eigene_instanz_id()
    assert kennung_vorher

    boese = sicherung_bauen('index_app_meta.db', instanz_id=None, zusatz_sql=(
        'CREATE INDEX app_meta ON users(username)',
    ))

    for bestaetigt in (False, True):
        res = _hochladen(auth_client, csrf, boese, bestaetigt=bestaetigt)
        assert res.status_code == 400, (
            f'Ein Index namens app_meta ging durch (bestaetigt={bestaetigt})')
        assert res.get_json()['grund'] == 'srv.restore_objects'
        assert 'app_meta' in res.get_json()['error']

    _instanz_ist_heil(app, auth_client, csrf, kennung_vorher)


def test_restore_lehnt_tabelle_namens_idx_users_person_ab(
        app, auth_client, csrf, sicherung_bauen, sauberes_backupverzeichnis):
    """Angriff B: eine TABELLE, die den Namen eines Index von init_db() belegt.

    `CREATE UNIQUE INDEX IF NOT EXISTS idx_users_person` ist nur dann ein
    stilles Nichts, wenn dort schon ein INDEX liegt. Bei einer Tabelle wirft
    es 'there is already a table named idx_users_person' - mitten in
    init_db(), und init_db() laeuft in create_app(). Mit
    `restart: unless-stopped` eine endlose Neustartschleife.

    Die Datei traegt die ECHTE Kennung dieser Installation: sie braucht
    nicht einmal eine Bestaetigung, sie sieht aus wie die eigene Sicherung
    von gestern.
    """
    kennung_vorher = m._eigene_instanz_id()
    assert kennung_vorher

    boese = sicherung_bauen('tabelle_idx_users_person.db', zusatz_sql=(
        'CREATE TABLE idx_users_person (person_id INTEGER)',
    ))

    res = _hochladen(auth_client, csrf, boese)
    assert res.status_code == 400, (
        'Eine Tabelle namens idx_users_person wurde uebernommen - die '
        'Anwendung startet danach nie wieder')
    assert res.get_json()['grund'] == 'srv.restore_objects'
    assert 'idx_users_person' in res.get_json()['error']

    _instanz_ist_heil(app, auth_client, csrf, kennung_vorher)


@pytest.mark.parametrize('app_meta_schema, kennung_in_datei, bezeichnung', [
    ("CREATE TABLE app_meta (key TEXT PRIMARY KEY,"
     " value TEXT CHECK (value = 'nur_dies'))", 'nur_dies', 'check'),
    ("CREATE TABLE app_meta (key TEXT PRIMARY KEY REFERENCES users(username),"
     " value TEXT)", 'eigen', 'fremdschluessel'),
])
def test_restore_lehnt_app_meta_mit_unsichtbarer_bedingung_ab(
        app, auth_client, csrf, sicherung_bauen, sauberes_backupverzeichnis,
        app_meta_schema, kennung_in_datei, bezeichnung):
    """Angriff C: app_meta, die der Formpruefung nichts zu beanstanden gibt.

    `PRAGMA table_info` liefert je Spalte nur (cid, name, type, notnull,
    default, pk). CHECK-Bedingungen, FOREIGN KEY, generierte Spalten - nichts
    davon taucht dort auf. Beide Varianten ergeben exakt _APP_META_SOLLFORM,
    es wurde also nichts neu aufgebaut; das spaetere INSERT der Kennung warf
    IntegrityError, wurde verschluckt, und die Installation hatte fuer immer
    keine eigene Kennung. Leiser als Variante A, weil die Tabelle korrekt
    aussieht.

    (_open_db setzt PRAGMA foreign_keys = ON - die FOREIGN-KEY-Variante
    greift also wirklich.)
    """
    kennung_vorher = m._eigene_instanz_id()
    assert kennung_vorher

    # Die Fremdschluessel-Variante traegt die ECHTE Kennung dieser
    # Installation: sie sieht aus wie die eigene Sicherung von gestern und
    # braeuchte nicht einmal eine Bestaetigung.
    wert = kennung_vorher if kennung_in_datei == 'eigen' else kennung_in_datei
    boese = sicherung_bauen(f'app_meta_{bezeichnung}.db', app_meta_sql=(
        app_meta_schema,
        "INSERT INTO app_meta (key, value) VALUES ('instance_id', '%s')" % wert,
    ))

    # Vorbedingung: unter PRAGMA table_info sieht diese Tabelle exakt richtig
    # aus - sonst prueft der Test nicht den Weg, um den es geht.
    probe = _sqlite3.connect(str(boese))
    try:
        form = [(r[1], str(r[2] or '').upper(), r[5])
                for r in probe.execute('PRAGMA table_info(app_meta)')]
    finally:
        probe.close()
    assert form == m._APP_META_SOLLFORM, (
        f'Die praeparierte app_meta faellt schon der Formpruefung auf: {form}')

    for bestaetigt in (False, True):
        res = _hochladen(auth_client, csrf, boese, bestaetigt=bestaetigt)
        assert res.status_code == 400, (
            f'app_meta mit {bezeichnung} ging durch (bestaetigt={bestaetigt})')
        assert res.get_json()['grund'] == 'srv.restore_app_meta_fremd'

    _instanz_ist_heil(app, auth_client, csrf, kennung_vorher)


def test_app_meta_neuaufbau_gewinnt_den_namen_gegen_index_und_trigger(tmp_path):
    """Der zweite Riegel hinter Angriff A, als Einzelteil.

    `DROP TABLE IF EXISTS app_meta` ist bei einem INDEX dieses Namens ein
    stilles Nichts - es wirft nicht, es tut nur nichts. Die Schleife brach
    danach mit `break` ab ("hat nicht geworfen, also hat es geklappt"), der
    Index blieb stehen, und das folgende CREATE TABLE scheiterte mit
    'there is already an index named app_meta'. Ein Drop, der nichts getan
    hat, ist kein Erfolg.
    """
    for zusatz in ('CREATE INDEX app_meta ON traeger(x)',
                   'CREATE VIEW app_meta AS SELECT x AS key, x AS value FROM traeger',
                   'CREATE TRIGGER app_meta AFTER INSERT ON traeger BEGIN'
                   ' UPDATE traeger SET x=1; END'):
        pfad = tmp_path / f'namenskollision_{abs(hash(zusatz))}.db'
        con = _sqlite3.connect(str(pfad))
        con.execute('CREATE TABLE traeger (x)')
        con.execute(zusatz)
        con.commit()
        try:
            kennung = m._ensure_instanz_id(con)   # darf nicht werfen
            assert kennung, f'Keine Kennung - blockiert durch: {zusatz}'
            assert m._lies_instanz_id(con) == kennung, (
                'Die Kennung ist nicht lesbar - die Installation koennte ab '
                f'jetzt keine Sicherung mehr einspielen. Blockiert durch: {zusatz}')
        finally:
            con.close()


def test_app_meta_mit_unsichtbarer_bedingung_wird_neu_aufgebaut(tmp_path):
    """Der zweite Riegel hinter Angriff C, als Einzelteil.

    Selbst wenn eine solche Tabelle auf anderem Weg in die laufende Datei
    geraet (Kopie ins Volume, Dateisystem-Snapshot), darf die Installation
    nicht ohne Kennung dastehen. Die Formpruefung sieht die Bedingung nicht -
    der Schreib-/Lese-Versuch schon.
    """
    for schema in (
        "CREATE TABLE app_meta (key TEXT PRIMARY KEY,"
        " value TEXT CHECK (value = 'nur_dies'))",
        "CREATE TABLE app_meta (key TEXT PRIMARY KEY,"
        " value TEXT GENERATED ALWAYS AS (upper(key)) VIRTUAL)",
    ):
        pfad = tmp_path / f'bedingung_{abs(hash(schema))}.db'
        con = _sqlite3.connect(str(pfad))
        con.execute('PRAGMA foreign_keys = ON')
        con.execute(schema)
        try:
            kennung = m._ensure_instanz_id(con)
            assert kennung, f'Keine Kennung trotz Neuaufbau - Schema: {schema}'
            assert m._lies_instanz_id(con) == kennung, (
                'Die Kennung ist nach dem Neuaufbau nicht lesbar - genau der '
                'Zustand, in dem kein Restore mehr moeglich ist')
        finally:
            con.close()


def test_app_meta_neuaufbau_erhaelt_den_abgeschlossenen_einrichtungsassistenten(
        tmp_path):
    """Der Neuaufbau warf `profiles_setup_done` bisher mit weg.

    Folge: nach jedem bestaetigten Restore stand der Einrichtungsassistent
    wieder auf "noch offen" (done: true -> done: false), obwohl der Admin ihn
    laengst durchlaufen hatte. Uebernommen wird ausschliesslich der Wert '1' -
    genau der, den api_setup_profiles_complete() schreibt.
    """
    pfad = tmp_path / 'assistent.db'
    con = _sqlite3.connect(str(pfad))
    # Kaputte Form -> Neuaufbau, aber mit einem gueltigen Flag darin.
    con.execute('CREATE TABLE app_meta (key, value)')
    con.execute("INSERT INTO app_meta (key, value) VALUES ('profiles_setup_done','1')")
    con.execute("INSERT INTO app_meta (key, value) VALUES ('irgendwas','beliebig')")
    con.commit()
    try:
        m._app_meta_sicherstellen(con)
        werte = dict(con.execute('SELECT key, value FROM app_meta'))
        assert werte.get('profiles_setup_done') == '1', (
            'Der abgeschlossene Assistent wurde verworfen - er stellt sich '
            'nach jedem Restore erneut in den Weg')
        assert 'irgendwas' not in werte, (
            'Es wurde mehr uebernommen als die Positivliste erlaubt - der '
            'Inhalt stammt aus der eingespielten Datei')
    finally:
        con.close()


@pytest.mark.parametrize('wert', ['0', '', 'ja', '1 ', 'x' * 200])
def test_app_meta_neuaufbau_uebernimmt_nur_den_woertlichen_wert(tmp_path, wert):
    """Alles ausser dem woertlichen '1' wird verworfen.

    Der Inhalt von app_meta ist im Moment des Neuaufbaus angreifergewaehlt.
    Uebernommen werden darf deshalb nur, was diese Anwendung selbst schreibt.
    """
    pfad = tmp_path / f'assistent_{abs(hash(wert))}.db'
    con = _sqlite3.connect(str(pfad))
    con.execute('CREATE TABLE app_meta (key, value)')
    con.execute("INSERT INTO app_meta (key, value) VALUES ('profiles_setup_done',?)",
                (wert,))
    con.commit()
    try:
        m._app_meta_sicherstellen(con)
        werte = dict(con.execute('SELECT key, value FROM app_meta'))
        assert 'profiles_setup_done' not in werte, (
            f'Der Fremdwert {wert!r} wurde uebernommen')
    finally:
        con.close()


def test_ensure_instanz_id_gibt_none_zurueck_wenn_app_meta_nicht_anlegbar_ist(
        tmp_path):
    """Der Zweig `except sqlite3.Error` in _ensure_instanz_id.

    Er war in der Suite toter Code: jeder Test, der "init_db darf nicht
    auseinanderfliegen" behauptete, wurde in Wahrheit vom Neuaufbau
    aufgefangen, es kam nie ein sqlite3.Error bis hierher. Entfernte man den
    except-Block, blieben alle Tests gruen.

    Der realistische Fall, in dem es ihn wirklich braucht: das Volume ist
    schreibgeschuetzt eingehaengt (oder die Dateirechte passen nicht). Dann
    scheitert das CREATE TABLE des Neuaufbaus. init_db() laeuft in
    create_app() - fliegt der Fehler durch, startet die Anwendung nicht mehr.
    """
    pfad = tmp_path / 'nur_lesen.db'
    con = _sqlite3.connect(str(pfad))
    con.execute('CREATE TABLE platzhalter (x)')
    con.commit()
    con.close()

    nur_lesend = _sqlite3.connect(f'file:{pfad}?mode=ro', uri=True)
    try:
        # Vorbedingung des Tests: hier MUSS wirklich ein sqlite3.Error
        # entstehen, sonst prueft der Test den Zweig nicht.
        with pytest.raises(_sqlite3.Error):
            nur_lesend.execute(m._APP_META_SCHEMA)

        kennung = m._ensure_instanz_id(nur_lesend)
        assert kennung is None, (
            'Erwartet wird ein sauberes None - jeder Restore wird dann '
            'abgelehnt, aber die Anwendung startet')
    finally:
        nur_lesend.close()


def test_create_app_startet_mit_einer_tabelle_namens_idx_users_person(app):
    """Angriff B, als Waechter ueber init_db() selbst.

    `CREATE UNIQUE INDEX IF NOT EXISTS idx_users_person` war die einzige
    Index-Anweisung in init_db(), die NICHT in try/except stand - die sechs
    darunter schon. Liegt unter dem Namen eine Tabelle, wirft sie 'there is
    already a table named idx_users_person'. init_db() laeuft in
    create_app(): die Anwendung startete danach nie wieder, mit
    `restart: unless-stopped` in einer Dauerschleife.

    Das Tor (Positivliste) haelt solche Dateien jetzt draussen. Dieser Test
    bewacht den zweiten Riegel: auch wenn die Datei auf anderem Weg in den
    Zustand geraet (Kopie ins Volume, Snapshot, abgebrochener Restore), darf
    init_db() den Start nicht verhindern.
    """
    from app import create_app

    con = m._open_db(m.DB_PATH)
    try:
        con.execute('DROP INDEX IF EXISTS idx_users_person')
        con.execute('CREATE TABLE idx_users_person (person_id INTEGER)')
        con.commit()
    finally:
        con.close()
    try:
        zweite = create_app()      # darf nicht mit 'already a table' sterben
        assert zweite.test_client().get('/login').status_code == 200
        assert m._eigene_instanz_id(), (
            'Nach dem Start fehlt die Kennung - jeder Restore waere blockiert')
    finally:
        con = m._open_db(m.DB_PATH)
        try:
            con.execute('DROP TABLE IF EXISTS idx_users_person')
            con.commit()
        finally:
            con.close()
        with app.app_context():
            m.init_db()


def test_fehlertext_verspricht_keinen_neustart_als_heilmittel():
    """Befund aus der Nachpruefung: der Text log den Admin an.

    'Bitte die Anwendung neu starten und das Server-Protokoll pruefen' - beim
    Neustart laeuft dieselbe init_db() in denselben Fehler. Ein Hinweis, der
    eine Erholung verspricht, die er nicht einloesen kann, kostet den
    Betreiber genau die Zeit, die er nicht hat.
    """
    for sprache in ('de', 'en'):
        text = translate(sprache, 'srv.restore_kein_eigener_nachweis').lower()
        assert 'neu starten' not in text and 'restart the application' not in text, (
            f'[{sprache}] Der Text verspricht weiterhin einen Neustart als '
            f'Heilmittel: {text}')
        assert 'protokoll' in text or 'log' in text, (
            f'[{sprache}] Ohne Verweis auf das Protokoll bleibt der Admin '
            'ganz ohne Anhaltspunkt')


def test_echte_dateikopie_der_laufenden_datenbank_restauriert_ohne_bestaetigung(
        app, auth_client, csrf, sauberes_backupverzeichnis, caplog):
    """Die Gegenprobe zur verschaerften Positivliste.

    Eine echte Sicherung ist keine nachgebaute Testdatei, sondern eine
    DATEIKOPIE der laufenden Datenbank: mit allen sieben idx_*-Indizes, mit
    sqlite_sequence und mit den impliziten sqlite_autoindex_*. Genau diese
    Objekte wuerde eine zu eng gefasste Positivliste als erstes abweisen -
    und der Betreiber stuende ohne jeden Rueckweg da.

    Sie muss OHNE jede Bestaetigung durchgehen.
    """
    with app.app_context():
        kopie = m.create_backup('gegenprobe')

    con = _sqlite3.connect(str(kopie))
    try:
        objekte = {(t_, n) for t_, n in con.execute(
            'SELECT type, name FROM sqlite_master')}
    finally:
        con.close()
    # Testannahme absichern: die Kopie enthaelt wirklich das, worum es geht.
    assert ('table', 'sqlite_sequence') in objekte
    assert ('index', 'idx_users_person') in objekte
    assert any(t_ == 'index' and n.startswith('sqlite_autoindex_')
               for t_, n in objekte)

    with caplog.at_level('WARNING', logger=m.log.name):
        res = _hochladen(auth_client, csrf, kopie)   # bewusst ohne Bestaetigung
    assert res.status_code == 200, (
        'Eine echte Sicherung dieser Installation wurde abgelehnt: '
        + res.get_data(as_text=True))
    assert res.get_json() == {'success': True}
    # Die Kehrseite von _startschritt(): der Rahmen darf nur bei einer
    # feindlichen Datei anspringen. Meldet er bei der eigenen Sicherung einen
    # ausgefallenen Schritt, laeuft die Anwendung im Normalbetrieb still mit
    # eingeschraenkten Garantien - und niemand merkt es.
    ausgefallen = [r.getMessage() for r in caplog.records
                   if 'Start-Schritt' in r.getMessage()
                   or 'Performance-Index nicht angelegt' in r.getMessage()]
    assert ausgefallen == [], (
        f'Bei einer echten Sicherung ist ein Start-Schritt ausgefallen: {ausgefallen}')


def test_erlaubte_objekte_bleiben_mit_init_db_im_gleichlauf(tmp_path):
    """Der Waechter ueber der Positivliste.

    _EIGENE_TABELLEN/_EIGENE_INDIZES sind von Hand gepflegt. Wer init_db()
    um eine Tabelle oder einen Index erweitert und diese Listen vergisst,
    baut eine Anwendung, die ihre EIGENEN Sicherungen ablehnt - und das faellt
    erst beim naechsten Restore auf, also im schlechtesten Moment.

    Deshalb wird hier eine frische Datenbank wirklich gebaut (create_app() in
    einem eigenen Prozess, damit DATABASE_PATH sauber greift) und ihr
    sqlite_master gegen die Listen gehalten. Umgekehrt genauso: ein Eintrag,
    den init_db() gar nicht mehr anlegt, waere ein unnoetig offenes Tor.
    """
    import json
    import subprocess
    import sys

    ziel = tmp_path / 'frisch' / 'k.db'
    ziel.parent.mkdir(parents=True, exist_ok=True)
    umgebung = dict(os.environ)
    umgebung.update(DATABASE_PATH=str(ziel), ADMIN_PASSWORD='egal',
                    DISABLE_SCHEDULER='1', DISABLE_RATE_LIMIT='1')
    umgebung.pop('DB_ENCRYPTION_KEY', None)
    quelle = (
        'import json, os, sqlite3\n'
        'from app import create_app\n'
        'create_app()\n'
        "con = sqlite3.connect(os.environ['DATABASE_PATH'])\n"
        "print(json.dumps(sorted(tuple(r) for r in"
        " con.execute('SELECT type, name FROM sqlite_master'))))\n"
    )
    lauf = subprocess.run([sys.executable, '-c', quelle], env=umgebung,
                          cwd=str(Path(__file__).resolve().parents[1]),
                          capture_output=True, text=True)
    assert lauf.returncode == 0, lauf.stderr
    objekte = [tuple(e) for e in json.loads(lauf.stdout.strip().splitlines()[-1])]
    assert objekte, 'Die frische Datenbank ist leer - Testannahme kaputt'

    tabellen = {n for typ, n in objekte if typ == 'table'} - m._SQLITE_EIGENE_TABELLEN
    indizes = {n for typ, n in objekte
               if typ == 'index' and not n.startswith('sqlite_autoindex_')}

    assert tabellen == set(m._EIGENE_TABELLEN), (
        'init_db() und _EIGENE_TABELLEN laufen auseinander. Nur in init_db(): '
        f'{sorted(tabellen - set(m._EIGENE_TABELLEN))}; nur in der Liste: '
        f'{sorted(set(m._EIGENE_TABELLEN) - tabellen)}')
    assert indizes == set(m._EIGENE_INDIZES), (
        'init_db() und _EIGENE_INDIZES laufen auseinander. Nur in init_db(): '
        f'{sorted(indizes - set(m._EIGENE_INDIZES))}; nur in der Liste: '
        f'{sorted(set(m._EIGENE_INDIZES) - indizes)}')

    # Und jedes Objekt der frischen Datenbank muss die Pruefung passieren -
    # sonst lehnt die Anwendung die Sicherung einer frischen Installation ab.
    for typ, name in objekte:
        assert m._objekt_ist_eigenes(typ, name), (
            f'Eigenes Objekt {typ} {name!r} wird als fremd abgewiesen')


def test_schutzkopie_bleibt_nach_bestaetigtem_altrestore_einspielbar(
        app, auth_client, csrf, sicherung_bauen, sauberes_backupverzeichnis):
    """Dritter Befund: die Kennung rotierte beim Restore.

    Eine bestaetigt eingespielte Altsicherung traegt keine Kennung, init_db()
    praegte danach eine frische - und ab diesem Moment war JEDE Sicherung
    dieser Installation 'srv.restore_fremde_instanz'. Auch die Schutzkopie
    'pre-restore', die derselbe Aufruf Sekunden vorher angelegt hatte. Fuer
    diesen Grund gibt es bewusst keine Uebersteuerung: der Betreiber haette
    die falsche Datei eingespielt und keinen Rueckweg mehr.
    """
    conn = m._open_db(m.DB_PATH)
    try:
        conn.execute("DELETE FROM persons WHERE name='VorDemRestore'")
        conn.execute("INSERT INTO persons (name) VALUES ('VorDemRestore')")
        conn.commit()
    finally:
        conn.close()
    kennung_vorher = m._eigene_instanz_id()
    assert kennung_vorher

    alt = sicherung_bauen('alte_sicherung.db', instanz_id=None)
    con = _sqlite3.connect(str(alt))
    con.execute("INSERT INTO persons (name) VALUES ('Altbestand')")
    con.commit()
    con.close()

    mit = _hochladen(auth_client, csrf, alt, bestaetigt=True)
    assert mit.status_code == 200, mit.get_data(as_text=True)

    assert m._eigene_instanz_id() == kennung_vorher, (
        'Die Instanzkennung ist beim Restore rotiert - damit sind alle '
        'bestehenden Sicherungen dieser Installation unerreichbar')

    schutzkopien = _pfade_der_schutzkopien()
    assert schutzkopien, 'Vor dem Tausch wurde keine Schutzkopie angelegt'

    # Der eigentliche Rueckweg: OHNE Bestaetigung, denn die Schutzkopie ist
    # eine echte Sicherung DIESER Installation.
    zurueck = _hochladen(auth_client, csrf, schutzkopien[-1])
    assert zurueck.status_code == 200, (
        'Die eigene Schutzkopie laesst sich nicht mehr einspielen: '
        + zurueck.get_data(as_text=True))

    namen = _personennamen_der_laufenden_instanz()
    assert 'VorDemRestore' in namen, 'Der Stand vor dem Restore ist nicht zurueck'
    assert 'Altbestand' not in namen


def _personennamen_der_laufenden_instanz():
    conn = m._open_db(m.DB_PATH)
    try:
        return {r[0] for r in conn.execute('SELECT name FROM persons')}
    finally:
        conn.close()


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

        # Der frueher hier stehende `assert all(p.exists() ...)` war wertlos:
        # die Zeile darueber hat gerade festgestellt, dass JEDER dieser
        # Aufrufe mit 400 endete - dann kann er auch nichts verdraengt haben.
        # Was wirklich zu beweisen ist: gewoehnliche Sicherungen rotieren
        # getrennt von den Schutzkopien. Ohne diese Trennung schiebt schon der
        # normale Betrieb den Rueckweg aus dem Topf.
        for i in range(m._MAX_SCHUTZ_BACKUPS + 2):
            res = auth_client.post('/api/backup/create', json={'label': f'alltag{i}'},
                                   headers={'X-CSRF-Token': csrf})
            assert res.status_code == 200, res.get_data(as_text=True)

        assert all(p.exists() for p in echte), (
            'Gewoehnliche Sicherungen haben die Schutzkopien verdraengt - '
            'nach einem misslungenen Restore gaebe es keinen Rueckweg mehr')
        gewoehnliche = [f for f in backup_dir.glob('kassenstuerzle_*.db')
                        if not m._ist_schutzkopie(f.name)]
        assert gewoehnliche, 'Testannahme kaputt: keine gewoehnliche Sicherung angelegt'
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


# ── Vierte Nachpruefung: die Regel "init_db() darf den Start nicht verhindern"
#
# Runde drei hatte genau die EINE Anweisung abgesichert, die sie gefunden hatte
# (CREATE UNIQUE INDEX idx_users_person). Runde vier fand zwei weitere, beide
# nach einem bestaetigten Altrestore erreichbar:
#   * check_password_hash() auf einem password_hash = NULL / Zahl / 'x$y$z'
#   * das UPDATE in _ensure_admin() an einer users-Tabelle mit
#     CHECK (role <> 'admin') bzw. einer generierten Spalte
# Beides liess create_app() fuer immer scheitern. Die Tests hier bewachen die
# Einzelfaelle UND - entscheidend - die allgemeine Regel: ein Fehler, den sich
# niemand ausgedacht hat, darf den Start genauso wenig verhindern.


@pytest.fixture()
def datenbank_zuruecksetzen(app, sauberes_backupverzeichnis, tmp_path):
    """Stellt die Datenbank der Testsession nach dem Test Byte fuer Byte wieder her.

    Diese Tests spielen WIRKLICH eine feindlich geformte users-Tabelle ein -
    danach gibt es z.B. keinen Admin mehr. init_db() allein repariert das
    nicht (genau das ist ja der Punkt), also wird der vorherige Stand ueber
    die SQLite-Backup-Schnittstelle zurueckgeschrieben.
    """
    kopie = tmp_path / 'vorher.db'
    quelle = _sqlite3.connect(m.DB_PATH)
    ziel = _sqlite3.connect(str(kopie))
    try:
        quelle.backup(ziel)
    finally:
        ziel.close()
        quelle.close()
    yield
    quelle = _sqlite3.connect(str(kopie))
    ziel = _sqlite3.connect(m.DB_PATH)
    try:
        quelle.backup(ziel)
    finally:
        ziel.close()
        quelle.close()
    with app.app_context():
        m.init_db()


def _users_umbauen(pfad, users_sql, spalten, werte_sql):
    """Ersetzt die users-Tabelle einer gebauten Sicherung durch `users_sql`.

    `spalten`/`werte_sql` bestimmen, was aus der alten Tabelle uebernommen
    wird - so bleibt z.B. der echte Passwort-Hash erhalten, waehrend die
    Rolle auf 'member' gesetzt wird.
    """
    con = _sqlite3.connect(str(pfad))
    try:
        con.execute('ALTER TABLE users RENAME TO users_alt')
        con.execute(users_sql)
        con.execute(f'INSERT INTO users ({spalten}) SELECT {werte_sql} FROM users_alt')
        con.execute('DROP TABLE users_alt')
        con.commit()
    finally:
        con.close()


def _startschritt_warnungen(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelname == 'WARNING' and 'Start-Schritt' in r.getMessage()]


_USERS_FEINDLICH = """CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        role TEXT NOT NULL DEFAULT 'member' {role_zusatz},
        is_active INTEGER {is_active_art},
        must_change_password INTEGER NOT NULL DEFAULT 0,
        created_at TEXT, last_login TEXT,
        session_epoch INTEGER NOT NULL DEFAULT 0)"""


@pytest.mark.parametrize('bezeichnung, users_sql, riegel_aus, person_dazu', [
    # Der Fall aus dem Bericht: kommt ohne jede Hilfe durch das Tor. Keine
    # Liste der Restore-Pruefung erkennt ihn - nur der Rahmen in init_db().
    ('check_gegen_admin',
     _USERS_FEINDLICH.format(role_zusatz="CHECK (role <> 'admin')",
                             is_active_art='NOT NULL DEFAULT 1'),
     False, False),
    # Generierte Spalten: die Restore-Pruefung weist sie inzwischen ab (siehe
    # test_restore_lehnt_generierte_spalte_ab). Hier wird dieser Riegel
    # abgeschaltet, damit der Test den Rahmen in init_db() bewacht - so, als
    # waere die Datei auf anderem Weg ins Volume gelangt.
    ('generiertes_is_active',
     _USERS_FEINDLICH.format(role_zusatz='',
                             is_active_art='GENERATED ALWAYS AS (0) VIRTUAL'),
     True, False),
    # Dasselbe fuer _autolink_single_person: genau ein Benutzer, genau eine
    # Person, person_id generiert -> 'cannot UPDATE generated column'.
    ('generierte_person_id',
     _USERS_FEINDLICH.replace(
         'person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL',
         'person_id INTEGER GENERATED ALWAYS AS (NULL) VIRTUAL').format(
             role_zusatz='', is_active_art='NOT NULL DEFAULT 1'),
     True, True),
])
def test_create_app_startet_nach_restore_mit_users_die_ensure_admin_sprengt(
        app, auth_client, csrf, sicherung_bauen, datenbank_zuruecksetzen,
        monkeypatch, caplog, bezeichnung, users_sql, riegel_aus, person_dazu):
    """Vierte Nachpruefung, Fundstelle 2: _ensure_admin/_autolink_single_person.

    Eine "Altsicherung" ohne Instanzkennung, deren einziger Admin die Rolle
    'member' traegt, und eine users-Tabelle, die das Befoerdern verbietet.
    Der Admin bestaetigt den gewohnten Altsicherungs-Dialog - danach warf das
    UPDATE in _ensure_admin() IntegrityError bzw. OperationalError, mitten in
    init_db(). Der Restore endete mit 500 bei schon getauschter Datei, und
    create_app() scheiterte ab da bei JEDEM Start.
    """
    from app import create_app

    if riegel_aus:
        monkeypatch.setattr(m, '_generierte_spalten', lambda con, tabellen: [])
    kennung_vorher = m._eigene_instanz_id()
    assert kennung_vorher

    datei = sicherung_bauen(f'{bezeichnung}.db', instanz_id=None,
                            benutzer=(('admin', 'test-password-123', 'member'),))
    spalten = ('id, username, password_hash, role'
               + ('' if 'is_active INTEGER GENERATED' in users_sql else ', is_active'))
    werte = spalten.replace('role', "'member'")
    _users_umbauen(datei, users_sql, spalten, werte)
    if person_dazu:
        con = _sqlite3.connect(str(datei))
        con.execute("INSERT INTO persons (name) VALUES ('Einzige Person')")
        con.commit()
        con.close()

    with caplog.at_level('WARNING', logger=m.log.name):
        antwort = _hochladen(auth_client, csrf, datei, bestaetigt=True)
    assert antwort.status_code == 200, antwort.get_data(as_text=True)

    warnungen = _startschritt_warnungen(caplog)
    erwarteter_schritt = ('_autolink_single_person' if person_dazu
                          else '_ensure_admin')
    assert any(erwarteter_schritt in w for w in warnungen), (
        f'Keine Warnung, die den Schritt {erwarteter_schritt} benennt: {warnungen}')

    zweite = create_app()      # darf nicht an der users-Tabelle sterben
    assert zweite.test_client().get('/login').status_code == 200
    assert m._eigene_instanz_id() == kennung_vorher, (
        'Die Instanzkennung ist nach dem Restore weg oder rotiert')


@pytest.mark.parametrize('wert', [None, 42, 'x$y$z', 'pbkdf2:sha256:99999999999999999999$s$h'],
                         ids=['null', 'zahl', 'text_ohne_verfahren', 'ueberlauf'])
def test_create_app_startet_nach_restore_mit_unbrauchbarem_passwort_hash(
        app, auth_client, csrf, sicherung_bauen, datenbank_zuruecksetzen,
        monkeypatch, wert):
    """Vierte Nachpruefung, Fundstelle 1: check_password_hash in init_db().

    werkzeug ruft darin `pwhash.split('$', 2)` auf. NULL oder eine Zahl
    werfen AttributeError, ein Text wie 'x$y$z' ValueError ('Invalid hash
    method'), eine riesige Iterationszahl OverflowError - frueher alles in
    create_app().

    Seit der fuenften Nachpruefung prueft init_db() gar keinen Hash mehr
    (siehe test_init_db_ruft_keine_passwort_kdf_auf), und die Restore-Pruefung
    weist alle vier Werte ab (test_restore_lehnt_unbrauchbaren_passwort_hash_ab,
    test_restore_lehnt_hash_mit_unplausiblen_kosten_ab). Beide Riegel werden
    hier abgeschaltet - so, als waere die Datei auf anderem Weg ins Volume
    gelangt. Dann muss create_app() trotzdem starten, und der Login fuer das
    betroffene Konto muss "falsches Passwort" melden statt 500.
    """
    from app import create_app

    monkeypatch.setattr(m, '_hat_ungueltige_passwort_hashes', lambda con: False)
    monkeypatch.setattr(m, '_hat_unplausible_passwort_hashes', lambda con: False)
    kennung_vorher = m._eigene_instanz_id()
    assert kennung_vorher

    datei = sicherung_bauen('kaputter_hash.db', instanz_id=None)
    _users_umbauen(datei, _USERS_FEINDLICH.replace(
        'password_hash TEXT NOT NULL', 'password_hash').format(
            role_zusatz='', is_active_art='NOT NULL DEFAULT 1'),
        'id, username, password_hash, role, is_active',
        'id, username, password_hash, role, is_active')
    con = _sqlite3.connect(str(datei))
    con.execute("UPDATE users SET password_hash=? WHERE username='admin'", (wert,))
    con.commit()
    con.close()

    antwort = _hochladen(auth_client, csrf, datei, bestaetigt=True)
    assert antwort.status_code == 200, antwort.get_data(as_text=True)

    zweite = create_app()      # darf nicht an check_password_hash sterben
    besucher = zweite.test_client()
    assert besucher.get('/login').status_code == 200
    m._login_attempts.clear()
    m._ip_attempts.clear()
    anmeldung = besucher.post('/login', data={'username': 'admin', 'password': 'admin'})
    m._login_attempts.clear()
    m._ip_attempts.clear()
    assert anmeldung.status_code == 200, 'Login mit kaputtem Hash endet nicht als falsches Passwort'
    assert m._eigene_instanz_id() == kennung_vorher


@pytest.mark.parametrize('ziel', [
    '_ensure_instanz_id', '_ensure_admin', '_autolink_single_person',
])
def test_unvorhergesehener_fehler_in_einem_startschritt_verhindert_den_start_nicht(
        app, monkeypatch, caplog, ziel):
    """Der eigentliche Waechter ueber die STRUKTUR der Behebung.

    Die beiden Tests darueber wuerden auch dann gruen, wenn jemand nur die
    zwei bekannten Aufrufe einzeln in try/except setzt - genau das Muster,
    das vier Pruefrunden hervorgebracht hat. Dieser hier wirft einen Fehler,
    den sich niemand ausgedacht hat, und verlangt dreierlei:
      1. create_app() startet trotzdem,
      2. die Warnung benennt den ausgefallenen Schritt,
      3. die UEBRIGEN Schritte laufen weiter (kein grosses try um alles, das
         nach dem ersten Fehler still aufhoert).
    """
    from app import create_app

    kennung_vorher = m._eigene_instanz_id()
    assert kennung_vorher

    gelaufen = []

    def mitschreiben(name, echt):
        def _huelle(*args, **kwargs):
            gelaufen.append(name)
            return echt(*args, **kwargs)
        return _huelle

    def unvorhergesehen(*args, **kwargs):
        raise RuntimeError('irgendwas Unvorhergesehenes')

    beobachtet = ('_ensure_instanz_id', '_ensure_admin', '_autolink_single_person')
    for name in beobachtet:
        if name != ziel:
            monkeypatch.setattr(m, name, mitschreiben(name, getattr(m, name)))
    # Frueher gab es hier auch den Fall 'check_password_hash' (die
    # Standardpasswort-Pruefung). Den Schritt gibt es nicht mehr - siehe
    # test_init_db_ruft_keine_passwort_kdf_auf.
    monkeypatch.setattr(m, ziel, unvorhergesehen)

    with caplog.at_level('WARNING', logger=m.log.name):
        zweite = create_app()
    assert zweite.test_client().get('/login').status_code == 200

    warnungen = _startschritt_warnungen(caplog)
    assert any('irgendwas Unvorhergesehenes' in w for w in warnungen), (
        f'Der Fehler wurde nicht als ausgefallener Start-Schritt gemeldet: {warnungen}')
    assert any(ziel in w for w in warnungen), (
        f'Die Warnung benennt den Schritt {ziel} nicht: {warnungen}')

    uebrige = [n for n in beobachtet if n != ziel]
    assert all(n in gelaufen for n in uebrige), (
        f'Nach dem Ausfall von {ziel} liefen nicht mehr alle uebrigen Schritte: '
        f'gelaufen={gelaufen}, erwartet={uebrige}')
    assert m._eigene_instanz_id() == kennung_vorher


@pytest.mark.parametrize('wert', [None, 42, ''], ids=['null', 'zahl', 'leer'])
def test_restore_lehnt_unbrauchbaren_passwort_hash_ab(
        auth_client, csrf, sicherung_bauen, kein_dateitausch, wert):
    """Zweiter Riegel am Tor: password_hash ist in jeder Fassung von init_db()
    `TEXT NOT NULL` und stammt immer aus generate_password_hash(). Eine Zeile
    ohne nicht leeren Text kann keine echte Sicherung sein - und ist durch
    keine Bestaetigung zu retten."""
    datei = sicherung_bauen('hash.db', instanz_id=None)
    _users_umbauen(datei, _USERS_FEINDLICH.replace(
        'password_hash TEXT NOT NULL', 'password_hash').format(
            role_zusatz='', is_active_art='NOT NULL DEFAULT 1'),
        'id, username, password_hash, role, is_active',
        'id, username, password_hash, role, is_active')
    con = _sqlite3.connect(str(datei))
    con.execute("INSERT INTO users (username, password_hash) VALUES ('zweiter', ?)",
                (wert,))
    con.commit()
    con.close()

    for bestaetigt in (False, True):
        res = _hochladen(auth_client, csrf, datei, bestaetigt=bestaetigt)
        assert res.status_code == 400, res.get_data(as_text=True)
        assert res.get_json()['grund'] == 'srv.restore_password_hash'
        assert res.get_json()['error'] == translate('de', 'srv.restore_password_hash')
    assert kein_dateitausch == []


def test_restore_lehnt_generierte_spalte_ab(auth_client, csrf, sicherung_bauen,
                                           kein_dateitausch):
    """Zweiter Riegel am Tor: init_db() legt nie eine generierte Spalte an."""
    datei = sicherung_bauen('generiert.db', instanz_id=None)
    _users_umbauen(datei, _USERS_FEINDLICH.format(
        role_zusatz='', is_active_art='GENERATED ALWAYS AS (0) VIRTUAL'),
        'id, username, password_hash, role', 'id, username, password_hash, role')

    for bestaetigt in (False, True):
        res = _hochladen(auth_client, csrf, datei, bestaetigt=bestaetigt)
        assert res.status_code == 400, res.get_data(as_text=True)
        assert res.get_json()['grund'] == 'srv.restore_generated_column'
        assert 'users.is_active' in res.get_json()['error']
    assert kein_dateitausch == []


def test_neue_ablehnungsgruende_sind_uebersetzt():
    for schluessel in ('srv.restore_password_hash', 'srv.restore_generated_column'):
        de = translate('de', schluessel, spalten='x')
        en = translate('en', schluessel, spalten='x')
        assert de != schluessel and en != schluessel and de != en


@pytest.mark.parametrize('typ, name', [
    ('table', 'users '), ('table', ' app_meta'), ('index', 'idx_users_person '),
])
def test_positivliste_haelt_namen_mit_randleerzeichen_fuer_fremd(typ, name):
    """Nit aus der vierten Nachpruefung: _objekt_ist_eigenes() normalisierte
    mit .strip().lower(), der app_meta-Wortlautvergleich nur mit .lower().
    `"users "` ist in SQLite eine ANDERE Tabelle als `users` - und lief mit
    .strip() als eigenes Objekt durch die Positivliste."""
    assert not m._objekt_ist_eigenes(typ, name)
    assert m._objekt_ist_eigenes(typ, name.strip())


# ── Fuenfte Nachpruefung: Rechenaufwand statt Ausnahme ───────────────────────
#
# Ein werkzeug-Hash traegt seine Kostenparameter selbst. 'pbkdf2:sha256:
# 2147483647$...' ist ein nicht leerer Text, kam also durch jede bisherige
# Pruefung - und liess check_password_hash() in init_db() ueber zwei Minuten
# rechnen. gunicorn (timeout=120) toetete den Worker mitten in create_app(),
# fuer immer. Kein try/except haelt das auf, weil nichts geworfen wird.
#
# Keiner dieser Tests rechnet den boesen Hash wirklich durch: eine Regression
# soll sofort rot werden, nicht die Suite 150 Sekunden lang anhalten.

_HEX64 = 'ab' * 32
_TEURE_HASHES = {
    'pbkdf2_maximal': f'pbkdf2:sha256:2147483647$salzsalzsalzsalz${_HEX64}',
    'pbkdf2_ueberlauf': f'pbkdf2:sha256:99999999999999999999$salz${_HEX64}',
    'pbkdf2_knapp_ueber_grenze': f'pbkdf2:sha256:2000001$salz${_HEX64}',
    'pbkdf2_fremdes_verfahren': f'pbkdf2:md5:1000$salz${_HEX64}',
    'scrypt_speicher': f'scrypt:1073741824:8:1$salzsalzsalzsalz${_HEX64}',
    'scrypt_r': f'scrypt:32768:100000:1$salzsalzsalzsalz${_HEX64}',
    'scrypt_p': f'scrypt:32768:8:100000$salzsalzsalzsalz${_HEX64}',
    'scrypt_n_keine_zweierpotenz': f'scrypt:30000:8:1$salz${_HEX64}',
    'text_ohne_verfahren': 'x$y$z',
    'nicht_ascii': f'scrypt:32768:8:1$salz$ä{_HEX64}',
    'ueberlang': f'scrypt:32768:8:1${"s" * 10_000}${_HEX64}',
}


@pytest.mark.parametrize('boeser_hash', list(_TEURE_HASHES.values()), ids=list(_TEURE_HASHES))
def test_restore_lehnt_hash_mit_unplausiblen_kosten_ab(
        auth_client, csrf, sicherung_bauen, kein_dateitausch, boeser_hash):
    """Dritter Riegel am Tor: nur Hashes in der Form und mit dem Aufwand, den
    generate_password_hash() erzeugt. Auch mit Bestaetigung nicht zu retten -
    und die Ablehnung selbst darf den Hash natuerlich nicht durchrechnen."""
    import time
    datei = sicherung_bauen('kosten.db', instanz_id=None)
    con = _sqlite3.connect(str(datei))
    con.execute("UPDATE users SET password_hash=? WHERE username='admin'", (boeser_hash,))
    con.commit()
    con.close()

    for bestaetigt in (False, True):
        start = time.monotonic()
        res = _hochladen(auth_client, csrf, datei, bestaetigt=bestaetigt)
        dauer = time.monotonic() - start
        assert res.status_code == 400, res.get_data(as_text=True)
        assert res.get_json()['grund'] == 'srv.restore_password_hash_kosten'
        assert res.get_json()['error'] == translate('de', 'srv.restore_password_hash_kosten')
        assert dauer < 2, f'Die Ablehnung brauchte {dauer:.1f} s'
    assert kein_dateitausch == []


def test_restore_lehnt_nicht_lesbaren_hash_ab(auth_client, csrf, sicherung_bauen,
                                              kein_dateitausch):
    """Text, der kein gueltiges UTF-8 ist, besteht die typeof-Pruefung, laesst
    sich in Python aber nicht einmal lesen - also ebenfalls unplausibel."""
    datei = sicherung_bauen('utf8.db', instanz_id=None)
    con = _sqlite3.connect(str(datei))
    con.execute("UPDATE users SET password_hash = CAST(x'ff24ff24ff' AS TEXT) "
                "WHERE username='admin'")
    con.commit()
    con.close()
    res = _hochladen(auth_client, csrf, datei, bestaetigt=True)
    assert res.status_code == 400
    assert res.get_json()['grund'] == 'srv.restore_password_hash_kosten'
    assert kein_dateitausch == []


def test_echte_sicherung_besteht_die_kostenpruefung(sicherung_bauen):
    """Gegenprobe: eine echte Sicherung - auch eine aus aelteren
    werkzeug-Fassungen mit pbkdf2-Hashes - wird NICHT abgelehnt."""
    from werkzeug.security import generate_password_hash
    datei = sicherung_bauen('echt.db', benutzer=(
        ('admin', 'test-password-123', 'admin'), ('zweiter', 'geheim-123', 'member')))
    con = _sqlite3.connect(str(datei))
    try:
        assert not m._hat_unplausible_passwort_hashes(con)
        for verfahren in ('pbkdf2:sha256:260000', 'pbkdf2:sha256:600000', 'pbkdf2', 'scrypt'):
            con.execute("UPDATE users SET password_hash=? WHERE username='zweiter'",
                        (generate_password_hash('geheim-123', verfahren),))
            assert not m._hat_unplausible_passwort_hashes(con), verfahren
    finally:
        con.close()


def test_neuer_ablehnungsgrund_kosten_ist_uebersetzt():
    de = translate('de', 'srv.restore_password_hash_kosten')
    en = translate('en', 'srv.restore_password_hash_kosten')
    assert de != 'srv.restore_password_hash_kosten'
    assert en != 'srv.restore_password_hash_kosten'
    assert de != en


def test_init_db_ruft_keine_passwort_kdf_auf(app, tmp_path, monkeypatch):
    """init_db() laeuft in JEDEM create_app() und darf keinen gespeicherten
    Hash pruefen - nach einem Restore stammen dessen Kostenparameter aus der
    hochgeladenen Datei. Frueher warnte ein Schritt, falls das Admin-Passwort
    noch 'admin' lautete, per check_password_hash().

    Die Wegwerf-Datenbank enthaelt genau den Hash aus dem Bericht. Der Spion
    rechnet nichts, er notiert nur - eine Regression wird sofort rot statt
    150 Sekunden zu rechnen."""
    import werkzeug.security

    db_pfad = str(tmp_path / 'wegwerf.db')
    monkeypatch.setattr(m, 'DB_PATH', db_pfad)
    with app.app_context():
        m.init_db()
    con = m._open_db(db_pfad)
    con.execute("UPDATE users SET password_hash=? WHERE username='admin'",
                (_TEURE_HASHES['pbkdf2_maximal'],))
    con.commit()
    con.close()

    aufrufe = []

    def verboten(pwhash, password):
        aufrufe.append(pwhash)
        return False

    # Beide Wege: init_db() importierte die Funktion frueher lokal aus
    # werkzeug.security, der Modulname in main.py ist der zweite.
    monkeypatch.setattr(werkzeug.security, 'check_password_hash', verboten)
    monkeypatch.setattr(m, 'check_password_hash', verboten)
    with app.app_context():
        m.init_db()
    assert aufrufe == [], f'init_db() hat check_password_hash aufgerufen: {aufrufe}'


def test_zwei_schutzkopien_derselben_sekunde_ueberschreiben_sich_nicht(
        app, sauberes_backupverzeichnis, monkeypatch):
    """Fuenfte Nachpruefung: der Zeitstempel war minutengenau. Zwei Restores
    in derselben Minute schrieben 'pre-restore' unter demselben Namen - die
    zweite Schutzkopie ueberschrieb die erste und damit den Rueckweg aus dem
    ersten Restore.

    Die Uhr wird angehalten, beide Aufrufe fallen also sogar in dieselbe
    SEKUNDE - der schaerfere Fall. Zwischen den Aufrufen aendert sich der
    Datenbestand, damit sich beide Kopien am Inhalt unterscheiden lassen."""
    import datetime as _dt

    class _FesteZeit(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 23, 12, 0, 0)

    def personen(pfad):
        con = _sqlite3.connect(str(pfad))
        try:
            return {r[0] for r in con.execute('SELECT name FROM persons')}
        finally:
            con.close()

    conn = m._open_db(m.DB_PATH)
    try:
        with monkeypatch.context() as mp:
            mp.setattr(_dt, 'datetime', _FesteZeit)
            erste = m.create_backup('pre-restore', schutz=True)
            conn.execute("INSERT INTO persons (name) VALUES ('VorDemRestore')")
            conn.commit()
            zweite = m.create_backup('pre-restore', schutz=True)
    finally:
        conn.execute("DELETE FROM persons WHERE name='VorDemRestore'")
        conn.commit()
        conn.close()

    assert erste != zweite, f'Beide Schutzkopien heissen {erste.name}'
    assert erste.exists() and zweite.exists()
    assert 'VorDemRestore' not in personen(erste), (
        'Die erste Schutzkopie wurde vom zweiten Aufruf ueberschrieben')
    assert 'VorDemRestore' in personen(zweite)
    assert m._ist_schutzkopie(erste.name) and m._ist_schutzkopie(zweite.name)
    assert (erste.stat().st_mode & 0o777) == 0o600
    assert (zweite.stat().st_mode & 0o777) == 0o600
