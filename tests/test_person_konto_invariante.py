"""Strikte 1:1-Kopplung Person<->Konto und gesperrtes "Wer" - die Pfade, die
die Invariante nach Task 1-5 noch umgingen (finale Branch-Review).

C1  /api/persons/save: Mitglied darf keine Person loeschen/anlegen
C2  /api/import/pdf/commit: "Wer" immer die eigene Person
C3  Einstellungen: "Konto anlegen" fuer Person ohne Konto, Liste der Konten
    ohne Person
I2  Konto ohne Person legt keine Buchung mit person_id=NULL an
I3  POST /api/users/<id> und /api/setup/profiles/self haengen kein bereits
    verknuepftes Konto um
"""
import pytest
from werkzeug.security import generate_password_hash


# ── Hilfen ───────────────────────────────────────────────────────────────────

def _admin_id(db):
    return db.execute("SELECT id FROM users WHERE username='admin'").fetchone()['id']


def _admin_verknuepfen(db, person_id):
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (person_id,))
    db.commit()


def _person(db, name):
    cur = db.execute('INSERT INTO persons (name) VALUES (?)', (name,))
    db.commit()
    return cur.lastrowid


def _konto(db, username, person_id, is_active=1, role='member'):
    cur = db.execute(
        'INSERT INTO users (username, password_hash, role, is_active, person_id)'
        ' VALUES (?,?,?,?,?)',
        (username, generate_password_hash('x'), role, is_active, person_id))
    db.commit()
    return cur.lastrowid


@pytest.fixture()
def als_member(db):
    """Der angemeldete Test-Admin wird fuer die Dauer des Tests zum Mitglied."""
    db.execute("UPDATE users SET role='member' WHERE username='admin'")
    db.commit()
    yield
    db.execute("UPDATE users SET role='admin' WHERE username='admin'")
    db.commit()


@pytest.fixture()
def aufraeumen(db):
    """Sammelt in Tests angelegte Benutzernamen und loescht sie danach."""
    namen = []
    yield namen
    admin = _admin_id(db)
    for n in namen:
        db.execute('DELETE FROM users WHERE username=? AND id<>?', (n, admin))
    db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
    db.commit()


def _post(client, csrf, url, body):
    return client.post(url, json=body, headers={'X-CSRF-Token': csrf})


# ── C1: /api/persons/save ────────────────────────────────────────────────────

def test_c1_member_kann_person_mit_deaktiviertem_konto_nicht_loeschen(
        auth_client, csrf, db, als_member, aufraeumen):
    pid = _person(db, 'Deaktivierte')
    _konto(db, 'deaktiviert', pid, is_active=0)
    aufraeumen.append('deaktiviert')

    res = _post(auth_client, csrf, '/api/persons/save',
                {'v': 2, 'rows': [], 'deleted_ids': [pid]})
    assert res.status_code == 403
    assert res.get_json()['success'] is False
    assert res.get_json()['error']
    assert db.execute('SELECT 1 FROM persons WHERE id=?', (pid,)).fetchone() is not None, \
        'Mitglied hat eine Person geloescht'
    assert db.execute("SELECT person_id FROM users WHERE username='deaktiviert'"
                      ).fetchone()['person_id'] == pid, 'Konto wurde verwaist'


def test_c1_member_kann_keine_person_ohne_konto_anlegen(auth_client, csrf, db, als_member):
    res = _post(auth_client, csrf, '/api/persons/save',
                {'v': 2, 'rows': [{'client_key': 'g', 'name': 'Geist'}], 'deleted_ids': []})
    assert res.status_code == 403
    assert db.execute("SELECT 1 FROM persons WHERE name='Geist'").fetchone() is None, \
        'Mitglied hat eine Person ohne Konto angelegt'


def test_c1_member_darf_bestehende_personen_weiterhin_bearbeiten(
        auth_client, csrf, db, als_member):
    """Genau das, was die Einstellungsseite (save-persons-btn) und die
    Profilseite (Geldboerse) fuer ein Mitglied senden - muss weiter gehen."""
    pid = _person(db, 'Anna')
    res = _post(auth_client, csrf, '/api/persons/save', {
        'v': 2, 'deleted_ids': [],
        'rows': [{'id': pid, 'name': 'Anna B.',
                  'wallet_start_date': '2026-02', 'wallet_start_amount': 42}]})
    assert res.status_code == 200
    row = db.execute('SELECT * FROM persons WHERE id=?', (pid,)).fetchone()
    assert row['name'] == 'Anna B.'
    assert row['wallet_start_date'] == '2026-02'
    assert row['wallet_start_amount'] == 42


def test_c1_auch_admin_loescht_keine_person_mit_deaktiviertem_konto(
        auth_client, csrf, db, aufraeumen):
    """Die Loeschsperre gilt fuer JEDES verknuepfte Konto, nicht nur aktive -
    sonst verwaist ein deaktiviertes Konto. Loeschen nur ueber das Konto."""
    pid = _person(db, 'Pausiert')
    _konto(db, 'pausiert', pid, is_active=0)
    aufraeumen.append('pausiert')
    res = _post(auth_client, csrf, '/api/persons/save',
                {'v': 2, 'rows': [], 'deleted_ids': [pid]})
    assert res.status_code == 200
    assert res.get_json()['blocked'] == ['Pausiert']
    assert db.execute('SELECT 1 FROM persons WHERE id=?', (pid,)).fetchone() is not None


# ── C2: PDF-Import ───────────────────────────────────────────────────────────

_TXN = {'include': True, 'amount': 12.5, 'day': 3, 'month': 9, 'year': 2026,
        'counterpart': 'PDF-Laden', 'is_income': False}
_TXN_EIN = {'include': True, 'amount': 99.0, 'day': 4, 'month': 9, 'year': 2026,
            'counterpart': 'PDF-Gehalt', 'is_income': True}


def test_c2_pdf_commit_nimmt_immer_die_eigene_person(auth_client, csrf, db, aufraeumen):
    anna = _person(db, 'Anna')
    ben = _person(db, 'Ben')
    _admin_verknuepfen(db, anna)
    res = _post(auth_client, csrf, '/api/import/pdf/commit',
                {'personid': ben, 'person_id': ben, 'transactions': [_TXN, _TXN_EIN]})
    assert res.status_code == 200, res.get_data(as_text=True)
    aus = db.execute("SELECT person_id FROM expenses WHERE description='PDF-Laden'").fetchone()
    ein = db.execute("SELECT person_id FROM income WHERE description='PDF-Gehalt'").fetchone()
    assert aus['person_id'] == anna, 'Client-gesendetes person_id wurde uebernommen'
    assert ein['person_id'] == anna, 'Client-gesendetes person_id wurde uebernommen'


def test_c2_i2_pdf_commit_ohne_eigene_person_wird_abgewiesen(auth_client, csrf, db):
    ben = _person(db, 'Ben')
    db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
    db.commit()
    res = _post(auth_client, csrf, '/api/import/pdf/commit',
                {'personid': ben, 'transactions': [_TXN]})
    assert res.status_code == 400
    assert res.get_json()['success'] is False
    assert db.execute("SELECT COUNT(*) c FROM expenses").fetchone()['c'] == 0


def test_c2_importseite_bietet_keine_personenauswahl_mehr(auth_client, db, aufraeumen):
    _admin_verknuepfen(db, _person(db, 'Anna Import'))
    html = auth_client.get('/import/pdf').get_data(as_text=True)
    assert 'id="pdf-person"' not in html, 'Personen-Auswahl im PDF-Import ist noch da'
    assert 'Anna Import' in html, 'Eigene Person wird nicht angezeigt'


# ── C3: Einstellungen - beide Richtungen ─────────────────────────────────────

def test_c3_konto_anlegen_fuer_nicht_existierende_person_meldet_404(auth_client, csrf, db):
    """Der "Konto anlegen"-Button sendet eine konkrete person_id. Gibt es die
    Person nicht (mehr), darf die Meldung nicht "Benutzername vergeben" sein."""
    res = _post(auth_client, csrf, '/api/users',
                {'username': 'niemand', 'role': 'member', 'person_id': 987654})
    assert res.status_code == 404
    assert db.execute("SELECT 1 FROM users WHERE username='niemand'").fetchone() is None


def test_c3_konto_anlegen_fuer_bestandsperson_verknuepft_genau_diese(
        auth_client, csrf, db, aufraeumen):
    pid = _person(db, 'Bestand')
    aufraeumen.append('bestand')
    res = _post(auth_client, csrf, '/api/users',
                {'username': 'bestand', 'role': 'member', 'person_id': pid})
    assert res.status_code == 200
    assert db.execute("SELECT person_id FROM users WHERE username='bestand'"
                      ).fetchone()['person_id'] == pid
    assert db.execute("SELECT COUNT(*) c FROM persons").fetchone()['c'] == 1


def test_c3_einstellungen_haben_konto_anlegen_und_liste_ohne_person(auth_client):
    html = auth_client.get('/settings').get_data(as_text=True)
    # Richtung 1: Person ohne Konto -> Button, der POST /api/users mit ihrer
    # person_id sendet.
    assert 'function kontoAnlegenEinfuegen' in html
    assert "person_id: personId" in html
    # Richtung 2: Konto ohne Person -> eigene Liste, standardmaessig versteckt
    # (progressive disclosure) und nur bei Bedarf eingeblendet.
    assert '<div id="orphan-accounts" class="mt-2 mb-2" hidden>' in html
    assert 'function kontenOhnePersonZeigen' in html
    assert 'wrap.hidden = konten.length === 0' in html


def test_c3_konten_ohne_person_nur_fuer_admin_sichtbar(auth_client, db, als_member):
    html = auth_client.get('/settings').get_data(as_text=True)
    assert 'orphan-accounts' not in html
    assert 'kontoAnlegenEinfuegen' not in html


# ── I2: Konto ohne Person legt keine NULL-Buchung an ─────────────────────────

@pytest.mark.parametrize('url', ['/api/expenses/save', '/api/income/save'])
def test_i2_neue_zeile_ohne_eigene_person_wird_abgewiesen(auth_client, csrf, db, url):
    db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
    db.commit()
    res = _post(auth_client, csrf, url, {
        'v': 2, 'year': 2026, 'month': 9, 'deleted_ids': [],
        'rows': [{'client_key': 'n', 'day': 1, 'description': 'Ohne Person', 'amount': 5}]})
    assert res.status_code == 400
    body = res.get_json()
    assert body['success'] is False
    assert 'Administrator' in body['error']
    tabelle = 'expenses' if 'expenses' in url else 'income'
    assert db.execute(f'SELECT COUNT(*) c FROM {tabelle}').fetchone()['c'] == 0, \
        'Zeile mit person_id=NULL wurde angelegt'


@pytest.mark.parametrize('tabelle,url', [('expenses', '/api/expenses/save'),
                                         ('income', '/api/income/save')])
def test_i2_bestehende_zeilen_bleiben_ohne_eigene_person_bearbeitbar(
        auth_client, csrf, db, tabelle, url):
    """Alle duerfen alle Buchungen bearbeiten - nur das ANLEGEN braucht die
    eigene Person."""
    anna = _person(db, 'Anna')
    cur = db.execute(f"INSERT INTO {tabelle} (year,month,day,description,person_id,amount)"
                     " VALUES (2026,9,1,'Alt',?,5)", (anna,))
    db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
    db.commit()
    res = _post(auth_client, csrf, url, {
        'v': 2, 'year': 2026, 'month': 9, 'deleted_ids': [],
        'rows': [{'id': cur.lastrowid, 'day': 2, 'description': 'Neu', 'amount': 6}]})
    assert res.status_code == 200
    row = db.execute(f'SELECT description, person_id FROM {tabelle} WHERE id=?',
                     (cur.lastrowid,)).fetchone()
    assert row['description'] == 'Neu'
    assert row['person_id'] == anna


# ── I3: kein Umhaengen/Loesen eines verknuepften Kontos ──────────────────────

def test_i3_update_kann_verknuepftes_konto_nicht_loesen(auth_client, csrf, db, aufraeumen):
    pid = _person(db, 'Verknuepft')
    uid = _konto(db, 'verknuepft', pid)
    aufraeumen.append('verknuepft')
    res = _post(auth_client, csrf, f'/api/users/{uid}', {'person_id': None})
    assert res.status_code == 200
    assert db.execute('SELECT person_id FROM users WHERE id=?', (uid,)
                      ).fetchone()['person_id'] == pid, 'Konto wurde von der Person geloest'


def test_i3_update_kann_verknuepftes_konto_nicht_umhaengen(auth_client, csrf, db, aufraeumen):
    alt = _person(db, 'Alt')
    frei = _person(db, 'Frei')
    uid = _konto(db, 'umhaengen', alt)
    aufraeumen.append('umhaengen')
    _post(auth_client, csrf, f'/api/users/{uid}', {'person_id': frei, 'role': 'member'})
    assert db.execute('SELECT person_id FROM users WHERE id=?', (uid,)
                      ).fetchone()['person_id'] == alt


@pytest.mark.parametrize('ziel', ['andere', None])
def test_i3_selbstzuordnung_haengt_verknuepftes_konto_nicht_um(
        auth_client, csrf, db, aufraeumen, ziel):
    eigene = _person(db, 'Ich')
    andere = _person(db, 'Andere')
    _admin_verknuepfen(db, eigene)
    res = _post(auth_client, csrf, '/api/setup/profiles/self',
                {'person_id': andere if ziel == 'andere' else None})
    assert res.status_code == 400
    assert res.get_json()['success'] is False
    assert db.execute("SELECT person_id FROM users WHERE username='admin'"
                      ).fetchone()['person_id'] == eigene


def test_i3_selbstzuordnung_mit_derselben_person_bleibt_harmlos(
        auth_client, csrf, db, aufraeumen):
    eigene = _person(db, 'Ich')
    _admin_verknuepfen(db, eigene)
    res = _post(auth_client, csrf, '/api/setup/profiles/self', {'person_id': eigene})
    assert res.status_code == 200


def test_i3_selbstzuordnung_auf_person_mit_konto_wird_abgewiesen(
        auth_client, csrf, db, aufraeumen):
    belegt = _person(db, 'Belegt')
    _konto(db, 'belegtkonto', belegt)
    aufraeumen.append('belegtkonto')
    db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
    db.commit()
    res = _post(auth_client, csrf, '/api/setup/profiles/self', {'person_id': belegt})
    assert res.status_code == 409
    assert db.execute("SELECT person_id FROM users WHERE username='admin'"
                      ).fetchone()['person_id'] is None
