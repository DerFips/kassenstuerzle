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
    """
    praepariert = sicherung_bauen('vor_update_sicherung.db', app_meta_sql=(
        'CREATE TABLE app_meta (key, value)',
        "INSERT INTO app_meta (key, value) VALUES ('instance_id', '')",
    ))

    # Schritt 1: der gewohnte Dialog - erst Ablehnung, dann eine Bestaetigung.
    ohne = _hochladen(auth_client, csrf, praepariert)
    assert ohne.status_code == 400
    assert ohne.get_json()['grund'] == 'srv.restore_keine_instanz_id', (
        'Die Datei muss wie jede Altsicherung aussehen - sonst prueft dieser '
        'Test nicht den Weg, den ein Angreifer wirklich geht')

    mit = _hochladen(auth_client, csrf, praepariert, bestaetigt=True)
    assert mit.status_code == 200, mit.get_data(as_text=True)

    # Schritt 2: die Installation kennt ihre eigene Herkunft weiterhin.
    eigene = m._eigene_instanz_id()
    assert eigene, (
        'Nach einer einzigen Bestaetigung kann die Installation ihre eigene '
        'Kennung nicht mehr lesen - der Herkunftsnachweis ist damit erledigt')

    # Schritt 3: und deshalb prallt die naechste fremde Datei weiter ab -
    # ohne Flag, ohne Rueckfrage, mit dem Grund, der nicht uebersteuerbar ist.
    fremd = sicherung_bauen('uebernahme.db', instanz_id='ff' * 16,
                            benutzer=(('admin', 'passwort-des-angreifers', 'admin'),))
    res = _hochladen(auth_client, csrf, fremd)
    assert res.status_code == 400, (
        'Nach der einen Bestaetigung wurde eine voellig fremde Datenbank ohne '
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
    assert antwort.status_code in (200, 400), (
        f'Unerwarteter Status {antwort.status_code} - ein 500 bedeutet: Datei '
        'schon getauscht, Migration gescheitert')

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
