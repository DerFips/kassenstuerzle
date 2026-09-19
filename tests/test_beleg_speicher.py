"""Befund I5: unbegrenzter Beleg-Upload, verwaiste Belegdateien.

`api_receipt_upload` war der einzige Upload-Endpunkt ohne Rate-Limit, das
Beleg-Verzeichnis hatte keine Obergrenze, und geloeschte Ausgaben liessen ihre
Belegdateien fuer immer liegen. Jedes eingeloggte Mitglied - nicht nur ein
Admin - konnte den Endpunkt so lange in einer Schleife aufrufen, bis der
Datentraeger voll war; danach scheitern SQLite-Schreibzugriffe und Sicherungen.
"""
import io
import os

import pytest

import app as app_paket
from app.routes import main as m


def _pdf_bytes(kennung=b'x'):
    """Ein minimales PDF.

    Der Upload prueft bei .pdf nur den Datei-Header - das kommt ohne Pillow
    aus (in der Testumgebung nicht installiert) und uebt trotzdem genau
    denselben Pfad im Endpunkt aus. `kennung` sorgt fuer unterschiedliche
    Inhalte und damit fuer unterschiedliche MD5-Dateinamen.
    """
    return b'%PDF-1.4\n% ' + kennung + b'\n'


@pytest.fixture()
def belegverzeichnis(tmp_path, monkeypatch):
    """Leeres Wegwerf-Belegverzeichnis statt des echten."""
    verzeichnis = tmp_path / 'receipts'
    verzeichnis.mkdir()
    monkeypatch.setattr(m, 'RECEIPTS_DIR', str(verzeichnis))
    return verzeichnis


def _speichern(client, csrf, **payload):
    body = {'v': 2, 'year': 2026, 'month': 9, 'rows': [], 'deleted_ids': []}
    body.update(payload)
    return client.post('/api/expenses/save', json=body, headers={'X-CSRF-Token': csrf})


# ── Teil 1: Rate-Limit ───────────────────────────────────────────────────────

def test_beleg_upload_steht_in_der_rate_limit_tabelle(app):
    """Ohne Eintrag in RATE_LIMITS bekommt der Endpunkt gar kein Limit - das
    war der Ausgangspunkt des Befunds: der einzige Upload-Endpunkt, der
    beliebig oft pro Minute aufgerufen werden konnte.

    Die frueher hier stehende Pruefung 'gleicher Wert wie beim Nachbarn' war
    kein Waechter: setzt man BEIDE auf '10000 per minute', bleibt sie gruen.
    Geprueft wird deshalb der Wert selbst - in beide Richtungen.
    """
    limit = app_paket.RATE_LIMITS.get('main.api_receipt_upload')
    assert limit, 'api_receipt_upload fehlt in der Rate-Limit-Tabelle'

    menge, _, zeitraum = limit.partition(' per ')
    assert zeitraum.strip() == 'minute', f'Unerwarteter Zeitraum: {limit!r}'
    menge = int(menge)
    # Obergrenze: ein absurd hohes Limit ist keins. Bei 20 MB je Upload sind
    # 30/Minute schon 600 MB/Minute - mehr darf hier nicht stehen.
    assert menge <= 60, (
        f'{limit!r} ist kein Limit mehr - bei 20 MB je Beleg waeren das '
        f'{menge * 20} MB pro Minute')
    # Hier stand frueher `assert menge >= 30` mit einer UX-Begruendung (der
    # Zaehler ist bis zur Behebung von M1 haushaltsweit, ein Stapel
    # Wochenbelege soll durchlaufen). Das war an dieser Stelle falsch: es
    # machte das VERSCHAERFEN des Limits zu einem Testfehler und haette
    # jemanden, der auf einen Missbrauch reagiert, gegen die Suite arbeiten
    # lassen. Eine Sicherheitsaussage darf nur in die strenge Richtung
    # wirken. Geblieben ist die Untergrenze, die keine Geschmacksfrage ist:
    # ein Limit von 0 wuerde den Endpunkt komplett abschalten.
    assert menge >= 1, f'{limit!r} schaltet den Endpunkt ganz ab'

    # Und der Endpunkt muss es auch wirklich geben, sonst laeuft der Eintrag
    # in create_app() stillschweigend ins Leere.
    assert 'main.api_receipt_upload' in app.view_functions


# ── Teil 2: Obergrenze fuer das Beleg-Verzeichnis ────────────────────────────

def _hochladen(client, csrf, daten):
    return client.post('/api/receipt/upload',
                       data={'file': (io.BytesIO(daten), 'bon.pdf')},
                       headers={'X-CSRF-Token': csrf},
                       content_type='multipart/form-data')


def test_upload_bei_vollem_belegverzeichnis_wird_abgelehnt(auth_client, csrf,
                                                           belegverzeichnis, monkeypatch):
    """Ohne Obergrenze kann jedes eingeloggte Mitglied den Endpunkt in einer
    Schleife aufrufen, bis der Datentraeger voll ist. Das Rate-Limit alleine
    verzoegert das nur, begrenzt aber nichts.

    Die Gegenproben (normaler Upload, inhaltsgleiche Wiederholung) stehen
    bewusst im selben Test: einzeln wuerden sie auch ohne die Obergrenze
    gruen sein und damit nichts bewachen.
    """
    monkeypatch.setattr(m, 'RECEIPTS_MAX_TOTAL_MB', 1)

    # 1) Unterhalb der Grenze laeuft alles wie bisher.
    erst = _hochladen(auth_client, csrf, _pdf_bytes(b'gruen'))
    assert erst.status_code == 200
    assert erst.get_json()['success'] is True
    assert len(os.listdir(belegverzeichnis)) == 1

    # 2) Verzeichnis ueber die Grenze bringen - der naechste NEUE Inhalt muss
    #    abgelehnt werden, mit uebersetzter Meldung und ohne etwas zu schreiben.
    (belegverzeichnis / 'ballast.bin').write_bytes(b'\0' * 1024 * 1024)
    vorher = sorted(os.listdir(belegverzeichnis))

    voll = _hochladen(auth_client, csrf, _pdf_bytes(b'rot'))
    assert voll.status_code == 507, 'voller Belegspeicher muss den Upload ablehnen'
    body = voll.get_json()
    assert body['success'] is False
    assert 'Belegspeicher' in body['error']
    assert sorted(os.listdir(belegverzeichnis)) == vorher, 'trotz Ablehnung geschrieben'

    # 3) Ein bereits vorhandener, inhaltsgleicher Beleg belegt keinen
    #    zusaetzlichen Platz und muss auch jetzt noch durchgehen - sonst
    #    scheitert das Anhaengen desselben Bons an eine zweite Ausgabe.
    nochmal = _hochladen(auth_client, csrf, _pdf_bytes(b'gruen'))
    assert nochmal.status_code == 200
    assert nochmal.get_json()['filename'] == erst.get_json()['filename']


# ── Teil 3: verwaiste Belege aufraeumen ──────────────────────────────────────

@pytest.fixture()
def person(db):
    cur = db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    db.commit()
    return cur.lastrowid


def test_beleg_wird_geloescht_wenn_die_letzte_ausgabe_verschwindet(auth_client, csrf, db,
                                                                   person, belegverzeichnis):
    beleg = belegverzeichnis / 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf'
    beleg.write_bytes(_pdf_bytes(b'rot'))

    neu = _speichern(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Bon',
         'person_id': person, 'amount': 12.5,
         'receipt_filename': beleg.name}]).get_json()['id_map']['a']
    assert beleg.exists()

    _speichern(auth_client, csrf, deleted_ids=[neu])
    assert not beleg.exists(), (
        'Nach dem Loeschen der letzten Ausgabe muss die Belegdatei weg sein - '
        'sonst waechst das Verzeichnis monoton, bis die Platte voll ist'
    )


def test_beleg_bleibt_wenn_eine_andere_ausgabe_ihn_noch_nutzt(auth_client, csrf, db,
                                                              person, belegverzeichnis):
    """Der Dateiname ist der MD5 des Inhalts - zwei Ausgaben duerfen voellig
    legitim denselben Beleg referenzieren (z.B. eine aufgeteilte Rechnung).
    Ein Aufraeumen, das nur auf die geloeschte Zeile schaut, wuerde der zweiten
    Ausgabe den Beleg unter den Fuessen wegziehen."""
    beleg = belegverzeichnis / 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.pdf'
    beleg.write_bytes(_pdf_bytes(b'gruen'))

    ids = _speichern(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Bon Teil 1',
         'person_id': person, 'amount': 10.0, 'receipt_filename': beleg.name},
        {'client_key': 'b', 'day': 1, 'description': 'Bon Teil 2',
         'person_id': person, 'amount': 5.0, 'receipt_filename': beleg.name},
    ]).get_json()['id_map']

    _speichern(auth_client, csrf, deleted_ids=[ids['a']])
    assert beleg.exists(), (
        'Der Beleg wird noch von einer zweiten Ausgabe referenziert und darf '
        'nicht geloescht werden'
    )

    _speichern(auth_client, csrf, deleted_ids=[ids['b']])
    assert not beleg.exists(), 'jetzt ist die letzte Referenz weg'


def test_ausgetauschter_beleg_wird_aufgeraeumt(auth_client, csrf, db, person,
                                               belegverzeichnis):
    alt = belegverzeichnis / 'cccccccccccccccccccccccccccccccc.pdf'
    neu_datei = belegverzeichnis / 'dddddddddddddddddddddddddddddddd.pdf'
    alt.write_bytes(_pdf_bytes(b'rot'))
    neu_datei.write_bytes(_pdf_bytes(b'blau'))

    rid = _speichern(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Bon',
         'person_id': person, 'amount': 20.0,
         'receipt_filename': alt.name}]).get_json()['id_map']['a']

    _speichern(auth_client, csrf, rows=[
        {'id': rid, 'day': 1, 'description': 'Bon',
         'person_id': person, 'amount': 20.0, 'receipt_filename': neu_datei.name}])

    assert not alt.exists(), (
        'Der ersetzte Beleg hat keine Referenz mehr und muss geloescht werden'
    )
    assert neu_datei.exists(), 'der neue Beleg muss erhalten bleiben'


def test_geleerter_betrag_raeumt_den_beleg_ebenfalls_auf(auth_client, csrf, db, person,
                                                         belegverzeichnis):
    """Ein auf 0 gesetzter Betrag loescht die Zeile - derselbe Weg in die
    Verwaisung, nur ueber einen anderen Zweig."""
    beleg = belegverzeichnis / 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.pdf'
    beleg.write_bytes(_pdf_bytes(b'gruen'))

    rid = _speichern(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Bon',
         'person_id': person, 'amount': 7.0,
         'receipt_filename': beleg.name}]).get_json()['id_map']['a']

    _speichern(auth_client, csrf, rows=[
        {'id': rid, 'day': 1, 'description': 'Bon',
         'person_id': person, 'amount': 0}])

    assert not beleg.exists()


def test_abgelehnter_belegname_loescht_die_datei_nicht(auth_client, csrf, db, person,
                                                       belegverzeichnis):
    """Nacharbeit zu K1: still verwerfen ist dokumentiert, still LOESCHEN nicht.

    Bei einem ungueltigen Belegnamen wird der Wert verworfen (rfn = None).
    Der alte Name landete trotzdem in den Aufraeum-Kandidaten, und nach dem
    UPDATE zeigte keine Zeile mehr auf ihn - _verwaiste_belege_loeschen()
    entfernte die Datei also von der Platte. Ein einziger Autosave aus einem
    alten Tab, ein Copy-and-Paste-Fehler in der Oberflaeche, und der Beleg
    ist unwiederbringlich weg, ohne dass irgendwo etwas davon steht.
    """
    beleg = belegverzeichnis / 'ffffffffffffffffffffffffffffffff.pdf'
    beleg.write_bytes(_pdf_bytes(b'gelb'))

    rid = _speichern(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Bon',
         'person_id': person, 'amount': 30.0,
         'receipt_filename': beleg.name}]).get_json()['id_map']['a']
    assert beleg.exists()

    antwort = _speichern(auth_client, csrf, rows=[
        {'id': rid, 'day': 1, 'description': 'Bon', 'person_id': person,
         'amount': 30.0, 'receipt_filename': 'a.jpg" onerror="alert(1)" x="'}])
    assert antwort.status_code == 200

    # Der ungueltige Wert erreicht die Datenbank nach wie vor nicht ...
    zeile = db.execute('SELECT receipt_filename FROM expenses WHERE id=?',
                       (rid,)).fetchone()
    assert zeile['receipt_filename'] is None

    # ... aber die Datei ist noch da.
    assert beleg.exists(), (
        'Ein abgelehnter Belegname hat die Belegdatei geloescht - das stille '
        'Verwerfen des Werts darf keine Daten vernichten')


def test_gueltiger_namenswechsel_raeumt_weiterhin_auf(auth_client, csrf, db, person,
                                                      belegverzeichnis):
    """Gegenprobe zum Test darueber: die Ausnahme gilt NUR fuer abgelehnte
    Namen. Wer den Beleg regulaer entfernt, bekommt die Datei weiterhin
    aufgeraeumt - sonst waere aus der Behebung ein Leck geworden."""
    beleg = belegverzeichnis / '99999999999999999999999999999999.pdf'
    beleg.write_bytes(_pdf_bytes(b'lila'))

    rid = _speichern(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Bon',
         'person_id': person, 'amount': 30.0,
         'receipt_filename': beleg.name}]).get_json()['id_map']['a']

    _speichern(auth_client, csrf, rows=[
        {'id': rid, 'day': 1, 'description': 'Bon', 'person_id': person,
         'amount': 30.0, 'receipt_filename': ''}])

    assert not beleg.exists()


# ── Teil 4: Belegnamen serverseitig pruefen (Befund K1) ─────────────────────

# Genau die Form, die api_receipt_upload erzeugt: MD5 des Inhalts + Endung.
_AUSBRUCH = 'a.jpg" onerror="alert(1)" x="'


@pytest.mark.parametrize('boeser_name', [
    _AUSBRUCH,
    '<img src=x onerror=alert(1)>.png',
    '../../../etc/passwd',
    'ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ.jpg',   # 32 Zeichen, aber kein Hex
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.svg',   # Hex, aber verbotene Endung
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg',    # nur 31 Hexstellen
])
def test_ausgedachter_belegname_erreicht_die_datenbank_nicht(auth_client, csrf, db,
                                                             person, boeser_name):
    """`rfn = e.get('receipt_filename') or None` nahm jeden Text entgegen.

    Ein Mitglied konnte damit beliebiges Markup in der Datenbank ablegen; es
    kam in `const EXISTING` woertlich in die Monatsseite zurueck. Ein Beleg
    dieser Anwendung hat immer die Form <MD5>.<erlaubte Endung> - alles andere
    kann kein Beleg sein und darf nicht gespeichert werden.
    """
    rid = _speichern(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Bon',
         'person_id': person, 'amount': 9.0,
         'receipt_filename': boeser_name}]).get_json()['id_map']['a']

    gespeichert = db.execute('SELECT receipt_filename FROM expenses WHERE id=?',
                             (rid,)).fetchone()['receipt_filename']
    assert gespeichert is None, (
        f'Ausgedachter Belegname gespeichert: {gespeichert!r}')

    # Und er taucht folglich auch nicht in der ausgelieferten Seite auf.
    html = auth_client.get('/month/2026/9').get_data(as_text=True)
    assert boeser_name not in html, (
        'Der ausgedachte Belegname kommt woertlich in der Seite zurueck')


def test_echter_belegname_bleibt_erhalten(auth_client, csrf, db, person,
                                          belegverzeichnis):
    """Gegenprobe: eine Pruefung, die alles verwirft, waere keine Behebung.

    Der Name stammt hier aus dem Upload-Endpunkt selbst - genau die Form, die
    im Betrieb entsteht."""
    hoch = auth_client.post(
        '/api/receipt/upload',
        data={'file': (io.BytesIO(_pdf_bytes(b'echt')), 'bon.pdf')},
        headers={'X-CSRF-Token': csrf},
        content_type='multipart/form-data')
    assert hoch.status_code == 200, hoch.get_data(as_text=True)
    name = hoch.get_json()['filename']

    rid = _speichern(auth_client, csrf, rows=[
        {'client_key': 'a', 'day': 1, 'description': 'Bon',
         'person_id': person, 'amount': 9.0,
         'receipt_filename': name}]).get_json()['id_map']['a']

    gespeichert = db.execute('SELECT receipt_filename FROM expenses WHERE id=?',
                             (rid,)).fetchone()['receipt_filename']
    assert gespeichert == name, (
        'Ein echter, vom Upload erzeugter Belegname wurde verworfen - damit '
        'liesse sich kein einziger Beleg mehr anhaengen')
