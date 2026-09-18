"""Regressionstests zu K1 (Sicherheitsprüfung 18.09.2026):

Gespeichertes XSS über Personen- und Kategorienamen. Jeder Test hier deckt
genau eine der vier Senken bzw. die serverseitige Farbprüfung ab und wird rot,
sobald die zugehörige Behebung zurückgenommen wird.
"""
import re

import pytest

# Klassisches Markup-Payload: wirkt in HTML-Senken (|safe, innerHTML).
MARKUP_PAYLOAD = '<img src=x onerror=alert(1)>'
MARKUP_MASKIERT = '&lt;img src=x onerror=alert(1)&gt;'

# Payload ohne < > " - genau die Zeichen, die Jinja maskiert. In einem
# JS-Template-Literal ist ${...} trotzdem eine lebende Substitution.
LITERAL_PAYLOAD_PERSON = 'Anna${fetch(1)}'
LITERAL_PAYLOAD_KATEGORIE = 'Miete${fetch(2)}'

# Bricht aus style="background:..." aus, sobald die Farbe ungeprüft bleibt.
FARB_PAYLOAD = 'red" onmouseover="alert(1)'

JAHR, MONAT = 2026, 9


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def _person(client, csrf, name):
    res = client.post('/api/persons/save',
                      json={'v': 2, 'rows': [{'client_key': 'k', 'name': name}],
                            'deleted_ids': []},
                      headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()['id_map']['k']


def _kategorie(client, csrf, name, color='#ff8800', is_split=True):
    res = client.post('/api/categories/save',
                      json={'v': 2, 'rows': [{'client_key': 'k', 'name': name,
                                              'color': color, 'is_split': is_split}],
                            'deleted_ids': []},
                      headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()['id_map']['k']


def _ausgabe(client, csrf, person_id, category_id, betrag=100.0):
    res = client.post('/api/expenses/save',
                      json={'v': 2, 'year': JAHR, 'month': MONAT, 'deleted_ids': [],
                            'rows': [{'client_key': 'e', 'day': 3, 'description': 'Test',
                                      'person_id': person_id, 'category_id': category_id,
                                      'amount': betrag}]},
                      headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200, res.get_data(as_text=True)


def _text(client, pfad):
    res = client.get(pfad)
    assert res.status_code == 200
    return res.get_data(as_text=True)


def _template_literale(html):
    """Alle Backtick-Literale des ausgelieferten Dokuments."""
    return re.findall(r'`[^`]*`', html, re.S)


# ── Senke 1: |safe auf dem übersetzten Abrechnungssatz ───────────────────────

def test_abrechnung_maskiert_personennamen(auth_client, csrf, db):
    """month.html: der Satz '<X> schuldet <Y>' lief durch `| safe`, obwohl
    translate() die Platzhalter ungeschützt einsetzt."""
    boeser = _person(auth_client, csrf, MARKUP_PAYLOAD)
    _person(auth_client, csrf, 'Bob')
    cid = _kategorie(auth_client, csrf, 'Miete')
    _ausgabe(auth_client, csrf, boeser, cid)

    html = _text(auth_client, f'/month/{JAHR}/{MONAT}')

    # Vorbedingung: die Abrechnung wurde wirklich gerendert.
    assert 'schuldet' in html, 'Abrechnungsblock fehlt - Test prueft nichts'
    assert MARKUP_PAYLOAD not in html, 'Personenname landet unmaskiert im HTML'
    assert MARKUP_MASKIERT in html, 'Name wird gar nicht mehr angezeigt'


def test_monatsseite_zeigt_harmlosen_namen_weiter_an(auth_client, csrf, db):
    """Gegenprobe: ein normaler Name erscheint unveraendert."""
    pid = _person(auth_client, csrf, 'Änna Müller')
    _person(auth_client, csrf, 'Bob')
    cid = _kategorie(auth_client, csrf, 'Miete')
    _ausgabe(auth_client, csrf, pid, cid)

    html = _text(auth_client, f'/month/{JAHR}/{MONAT}')
    assert 'Änna Müller' in html


# ── Senke 2: Namen in einem JS-Template-Literal (settings.html) ──────────────

def test_einstellungen_namen_nicht_in_template_literal(auth_client, csrf, db):
    """settings.html baute die <option>-Liste in einem Backtick-Literal.
    Jinja maskiert dort $ { } nicht, `${...}` war also ausfuehrbarer Code."""
    _person(auth_client, csrf, LITERAL_PAYLOAD_PERSON)
    _kategorie(auth_client, csrf, LITERAL_PAYLOAD_KATEGORIE)

    html = _text(auth_client, '/settings')

    literale = _template_literale(html)
    assert literale, 'keine Template-Literale gefunden - Test prueft nichts'
    for lit in literale:
        assert LITERAL_PAYLOAD_PERSON not in lit, \
            f'Personenname steht in einem Template-Literal: {lit[:120]!r}'
        assert LITERAL_PAYLOAD_KATEGORIE not in lit, \
            f'Kategoriename steht in einem Template-Literal: {lit[:120]!r}'

    # Die Daten muessen weiterhin ankommen - jetzt als JSON.
    assert LITERAL_PAYLOAD_PERSON in html
    assert LITERAL_PAYLOAD_KATEGORIE in html
    assert 'REC_PERSONS' in html and 'REC_CATS' in html


def test_einstellungen_baut_optionen_per_dom(auth_client, csrf, db):
    html = _text(auth_client, '/settings')
    assert 'personsOpts' not in html
    assert 'catsOpts' not in html
    assert 'new Option(' in html


# ── Senke 3: innerHTML in month.html ────────────────────────────────────────

@pytest.mark.parametrize('verbotenes_muster', [
    # buildPersonOpts / buildCatOpts
    '${x.name}',
    'buildPersonOpts',
    'buildCatOpts',
    # renderSavings / renderWallet
    '${r.name}',
    '${w.name}',
    # renderSettlement (JS-Zwilling von Senke 1)
    "'<strong>' + txn.from",
    "'<strong>' + txn.to",
    # renderOverview: Personen-, Kategoriename und Farbe
    '<th class="text-end">${pn}</th>',
    '${cn}${isSplitCat',
    'style="background:${col}"',
])
def test_monatsseite_js_ohne_ungeschuetzte_interpolation(auth_client, verbotenes_muster):
    """Diese Senken laufen erst im Browser (die Daten kommen als JSON), lassen
    sich also nur am ausgelieferten Skript pruefen."""
    html = _text(auth_client, f'/month/{JAHR}/{MONAT}')
    assert verbotenes_muster not in html, \
        f'ungeschuetzte Interpolation weiterhin vorhanden: {verbotenes_muster}'


def test_monatsseite_nutzt_gemeinsamen_escape_helfer(auth_client):
    html = _text(auth_client, f'/month/{JAHR}/{MONAT}')
    assert 'window.escHtml = esc;' in html, 'esc() wird nicht exportiert'
    assert 'escHtml(' in html
    assert 'safeColor(' in html


# ── Senke 4: Chart-Legende in overview.html ─────────────────────────────────

@pytest.mark.parametrize('verbotenes_muster', [
    'legendEl.innerHTML = entries.map',
    'style="background:${colorMap',
    '${cn} <span class="text-muted">',
])
def test_uebersicht_legende_ohne_innerhtml(auth_client, csrf, db, verbotenes_muster):
    # Der Chart-Block steht in `{% if persons_list %}` - ohne Person wird das
    # Skript gar nicht ausgeliefert und der Test wuerde nichts pruefen.
    pid = _person(auth_client, csrf, 'Alice')
    cid = _kategorie(auth_client, csrf, 'Miete')
    _ausgabe(auth_client, csrf, pid, cid)

    html = _text(auth_client, '/overview')
    assert 'categoryChart' in html, 'Chart-Skript fehlt - Test prueft nichts'
    assert verbotenes_muster not in html, \
        f'Legende baut weiterhin per innerHTML: {verbotenes_muster}'


# ── Farbprüfung serverseitig ────────────────────────────────────────────────

def test_kategoriefarbe_mit_attributausbruch_wird_verworfen(auth_client, csrf, db):
    cid = _kategorie(auth_client, csrf, 'Boese', color=FARB_PAYLOAD)
    row = db.execute('SELECT color FROM categories WHERE id=?', (cid,)).fetchone()
    assert row['color'] == '#6c757d', f"Farbe ungeprueft gespeichert: {row['color']!r}"


def test_gueltige_kategoriefarben_bleiben_erhalten(auth_client, csrf, db):
    for farbe in ('#ff8800', '#FFF', 'aabbcc', '#12345678'):
        cid = _kategorie(auth_client, csrf, f'Kat {farbe}', color=farbe)
        row = db.execute('SELECT color FROM categories WHERE id=?', (cid,)).fetchone()
        assert row['color'] == farbe


def test_monatsseite_ohne_ereignisattribut_aus_farbe(auth_client, csrf, db):
    """Die ungeprüfte Farbe stand maskiert, aber vollstaendig im style-Attribut
    der Monatsuebersicht - Wurzel des Attribut-Ausbruchs."""
    pid = _person(auth_client, csrf, 'Alice')
    _person(auth_client, csrf, 'Bob')
    cid = _kategorie(auth_client, csrf, 'Boese', color=FARB_PAYLOAD)
    _ausgabe(auth_client, csrf, pid, cid)

    html = _text(auth_client, f'/month/{JAHR}/{MONAT}')
    assert 'background:' in html, 'Farbbadge fehlt - Test prueft nichts'
    assert 'onmouseover' not in html
