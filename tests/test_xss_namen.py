"""Regressionstests zu K1 (Sicherheitsprüfung 18.09.2026):

Gespeichertes XSS über Personen- und Kategorienamen. Jeder Test hier deckt
genau eine der vier Senken bzw. die serverseitige Farbprüfung ab und wird rot,
sobald die zugehörige Behebung zurückgenommen wird.
"""
import re

import pytest

from app.routes import main as _main

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
    # "Wer" einer neuen Zeile kommt serverseitig aus der Sitzung (Task 3), und
    # ein Konto ohne Person darf keine neuen Zeilen anlegen (Befund I2) - der
    # Test-Admin wird deshalb der Person zugeordnet, deren Name geprueft wird.
    # Zurueckgesetzt ueber die db-Fixture (Personen weg -> ON DELETE SET NULL).
    con = _main._open_db(_main.DB_PATH)
    try:
        con.execute("UPDATE users SET person_id=? WHERE username='admin'", (person_id,))
        con.commit()
    finally:
        con.close()
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


# ── Struktureller Waechter fuer die JS-Senken ────────────────────────────────
# Die Senken in month/overview/settings laufen erst im Browser: die Daten
# kommen per JSON, der Payload taucht im ausgelieferten HTML also ohnehin auf,
# egal ob escHtml davor steht. Ein Vergleich "Payload nicht im HTML" kann hier
# nichts pruefen, und die frueheren Tests suchten stattdessen nach woertlichen
# Codestellen ('${escHtml(pn)}') - die ging beim ersten Umbenennen verloren,
# ohne rot zu werden.
#
# Stattdessen wird das ausgelieferte Skript strukturell geprueft: JEDE
# Einsetzung ${...} in einem Template-Literal, das HTML baut, muss entweder
# durch eine bekannte Maskierungsfunktion laufen oder in der unten
# ausgeschriebenen Liste unbedenklicher Ausdruecke stehen. Damit wird nicht
# nur die Ruecknahme eines einzelnen Fixes rot, sondern auch jede NEU
# hinzugefuegte ungeschuetzte Senke - und die Liste zwingt zu einer bewussten
# Entscheidung statt zu einem stillen Durchwinken.

_SICHERE_HUELLEN = ('escHtml', 'safeColor', 'encodeURIComponent', 'fmt', 'fmtEur', 't')

# Schluesselwoerter und Globale, die keine Daten transportieren.
_KEINE_DATEN = {'true', 'false', 'null', 'undefined', 'new', 'this',
                'Math', 'Object', 'String', 'Number', 'JSON', 'Array',
                'document', 'window'}

# Ausdruecke, die bewusst roh eingesetzt werden duerfen. Jeder Eintrag ist eine
# Entscheidung, keine Bequemlichkeit - wer hier etwas hinzufuegt, muss belegen
# koennen, dass an dieser Stelle kein frei waehlbarer Text ankommt.
_UNBEDENKLICHE_KETTEN = {
    # Im Skript selbst zusammengebaute HTML-Bruchstuecke. Ihr Inhalt stammt
    # aus denselben Literalen und wird von diesem Test mitgeprueft.
    'body', 'cells', 'rows', 'thead', 'legend', 'footCells', 'subCells',
    'label', 'inputs', 'buildSplitPanelHtml',
    # Reine Meldungstexte aus t(), von den Aufrufern uebersetzt.
    'msg',
    # In JS berechnet, nie aus Benutzereingaben: eine CSS-Klasse.
    'rowClass',
    # 'url' stand hier frueher mit der Begruendung "ueber encodeURIComponent
    # gebaut" - und genau das pruefte niemand nach. Die Zeile
    # `const url = ``/api/receipt/${encodeURIComponent(filename)}``;` baut
    # kein Markup ('<' kommt darin nicht vor), der Scanner sah sie also gar
    # nicht an; `${url}` daneben war per Liste freigestellt. Man konnte
    # encodeURIComponent ersatzlos streichen, ohne dass ein einziger Test rot
    # wurde. Statt des Eintrags loest _literal_variablen() solche Variablen
    # jetzt auf und prueft die Einsetzungen IHRES Literals mit.
    # Zahlen und Flags aus dem Server-JSON; sie steuern nur Ternaries und
    # erreichen das Dokument nie als Text.
    'isCash', 'isSplitCat', 'settled', 'i',
    'r.savings', 'w.end_balance', 'w.cash_expenses', 'txn.is_income',
}


def _literale_mit_position(text):
    """Wie _template_literale_mit_einsetzungen, zusaetzlich mit der Position.

    Liefert (position_des_oeffnenden_backticks, inhalt, ausdruecke). Die
    Position braucht _literal_variablen(), um ein Literal seiner Variablen
    zuzuordnen.
    """
    ergebnis = []

    def literal(i):
        beginn = i - 1          # das oeffnende Backtick
        inhalt, ausdruecke = [], []
        while i < len(text):
            c = text[i]
            if c == '\\':
                i += 2
                continue
            if c == '`':
                ergebnis.append((beginn, ''.join(inhalt), ausdruecke))
                return i + 1
            if text[i:i + 2] == '${':
                i, ausdruck = einsetzung(i + 2)
                ausdruecke.append(ausdruck)
                continue
            inhalt.append(c)
            i += 1
        ergebnis.append((beginn, ''.join(inhalt), ausdruecke))
        return i

    def einsetzung(i):
        start, tiefe = i, 1
        while i < len(text):
            c = text[i]
            if c == '\\':
                i += 2
                continue
            if c in '"\'':
                q = c
                i += 1
                while i < len(text) and text[i] != q:
                    i += 2 if text[i] == '\\' else 1
                i += 1
                continue
            if c == '`':
                i = literal(i + 1)
                continue
            if c == '{':
                tiefe += 1
            elif c == '}':
                tiefe -= 1
                if tiefe == 0:
                    return i + 1, text[start:i]
            i += 1
        return i, text[start:i]

    i = 0
    while i < len(text):
        i = literal(i + 1) if text[i] == '`' else i + 1
    return ergebnis


def _template_literale_mit_einsetzungen(text):
    """Zerlegt JS-Quelltext in Template-Literale samt ihren ${...}-Ausdruecken.

    Bewusst ein echter Scanner statt eines regulaeren Ausdrucks: in
    import_pdf.html stecken Literale INNERHALB einer Einsetzung, und eine
    Paarung ueber `[^`]*` wuerde dort die falschen Backticks zusammenlegen
    und die Haelfte der Senken uebersehen.
    """
    return [(inhalt, ausdruecke) for _, inhalt, ausdruecke in _literale_mit_position(text)]


# Zuweisungen der Form `const url = ` unmittelbar vor einem Template-Literal.
_LITERAL_ZUWEISUNG = re.compile(
    r'\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*$')


def _literal_variablen(text):
    """Ordnet Variablennamen die Einsetzungen ihres Template-Literals zu.

    Hintergrund: der Scanner unten sieht sich nur Literale an, die '<'
    enthalten - alles andere baut kein Markup. Ein Literal, das erst ueber
    eine Variable in eine HTML-Senke wandert, fiel damit durch:

        const url = `/api/receipt/${filename}`;     // kein '<', ungeprueft
        el.innerHTML = `<img src="${url}">`;        // 'url' stand auf der Liste

    Beide Haelften einzeln harmlos, zusammen die Senke. Aufgeloest wird
    deshalb ueber den Namen: wo `url` in einer HTML-Senke steht, werden die
    Einsetzungen SEINES Literals geprueft.
    """
    zuordnung = {}
    for beginn, _inhalt, ausdruecke in _literale_mit_position(text):
        treffer = _LITERAL_ZUWEISUNG.search(text[max(0, beginn - 80):beginn])
        if treffer:
            zuordnung.setdefault(treffer.group(1), []).extend(ausdruecke)
    return zuordnung


def _ohne_stringliterale(code):
    return re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`", ' ', code)


def _ohne_sichere_aufrufe(code):
    """Entfernt Aufrufe der Maskierungsfunktionen samt ihrer Argumente."""
    muster = re.compile(r'\b(?:' + '|'.join(_SICHERE_HUELLEN) + r')\s*\(')
    while True:
        treffer = muster.search(code)
        if not treffer:
            return code
        auf = code.index('(', treffer.end() - 1)
        tiefe, ende = 0, None
        for j in range(auf, len(code)):
            if code[j] == '(':
                tiefe += 1
            elif code[j] == ')':
                tiefe -= 1
                if tiefe == 0:
                    ende = j
                    break
        if ende is None:
            return code
        code = code[:treffer.start()] + ' ' + code[ende + 1:]


def _ungeschuetzte_einsetzungen(html):
    """Liefert (Ausdruck, Kette) fuer jede ungeschuetzte HTML-Einsetzung."""
    variablen = _literal_variablen(html)
    funde = []

    def pruefe(ausdruck, aufgeloest):
        rest = _ohne_sichere_aufrufe(_ohne_stringliterale(ausdruck))
        rest = re.sub(r'/[^/\n]*/[gimsuy]*', ' ', rest)   # Regex-Literale
        for kette in re.findall(
                r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*', rest):
            if kette.split('.')[0] in _KEINE_DATEN:
                continue
            if kette in variablen and kette not in aufgeloest:
                # Aus einem Template-Literal gebaut: nicht die Variable
                # bewerten, sondern das, was in sie hineinlaeuft.
                for innen in variablen[kette]:
                    pruefe(innen, aufgeloest | {kette})
                continue
            if kette in _UNBEDENKLICHE_KETTEN:
                continue
            funde.append((ausdruck.strip(), kette))

    for inhalt, ausdruecke in _template_literale_mit_einsetzungen(html):
        if '<' not in inhalt:
            continue          # baut kein Markup - hier gibt es keine Senke
        for ausdruck in ausdruecke:
            pruefe(ausdruck, frozenset())
    return funde


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
    Jinja maskiert dort $ { } nicht, `${...}` war also ausfuehrbarer Code.

    Die Personen-Auswahl der wiederkehrenden Ausgaben (REC_PERSONS), auf die
    sich dieser Test urspruenglich mitbezog, gibt es seitdem gar nicht mehr:
    wiederkehrende Vorlagen sind jetzt streng persoenlich und leben auf der
    eigenen Profilseite ohne Personen-Feld (immer die eigene Person) - dieser
    Teil der Angriffsflaeche ist damit nicht behoben, sondern entfernt. Der
    Kategorie-Payload-Nachweis wandert deshalb mit dem Feature nach
    test_profil_baut_kategorie_optionen_per_dom unten."""
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

    # Die Personendaten muessen weiterhin ankommen - jetzt als JSON
    # (PERSONS_FOR_SPLITS, fuer die Kategorie-Split-Anteile).
    assert LITERAL_PAYLOAD_PERSON in html


def test_einstellungen_baut_optionen_per_dom(auth_client, csrf, db):
    html = _text(auth_client, '/settings')
    assert 'personsOpts' not in html
    assert 'catsOpts' not in html


def test_profil_baut_kategorie_optionen_per_dom(auth_client, csrf, db):
    """Nachfolger von REC_CATS auf settings.html: die Kategorie-Auswahl der
    (jetzt eigenen) wiederkehrenden Ausgaben lebt auf /profile und baut ihre
    <option>-Liste per new Option(), nicht per Template-Literal."""
    pid = _person(auth_client, csrf, 'Normalo')
    auth_client.application  # nur zur Klarheit, dass wir dieselbe App nutzen
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (pid,))
    db.commit()
    try:
        _kategorie(auth_client, csrf, LITERAL_PAYLOAD_KATEGORIE)

        html = _text(auth_client, '/profile')

        literale = _template_literale(html)
        for lit in literale:
            assert LITERAL_PAYLOAD_KATEGORIE not in lit, \
                f'Kategoriename steht in einem Template-Literal: {lit[:120]!r}'

        assert LITERAL_PAYLOAD_KATEGORIE in html, \
            'Kategoriename kommt gar nicht mehr an (PROFILE_CATS als JSON)'
        assert 'new Option(' in html
    finally:
        db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
        db.commit()


# ── Senke 3: innerHTML in month.html ────────────────────────────────────────

def test_monatsseite_js_ohne_ungeschuetzte_interpolation(auth_client, csrf, db):
    """Jede HTML-bauende Einsetzung im Skript der Monatsseite muss maskiert sein.

    Die Vorgaengerfassung suchte nach woertlichen Codestellen ('${r.name}',
    'buildPersonOpts', ...). Sie blieb gruen, wenn man '${escHtml(pn)}' durch
    '${ pn }' ersetzte - also bei der woertlichen Wiedereinfuehrung genau des
    Befunds, den sie bewachen sollte. Geprueft wird deshalb die Struktur des
    ausgelieferten Skripts, nicht sein Wortlaut.
    """
    # Mit echten Daten rendern, damit auch die datenabhaengigen Bloecke
    # (Abrechnung, Uebersicht) im Dokument stehen.
    pid = _person(auth_client, csrf, LITERAL_PAYLOAD_PERSON)
    _person(auth_client, csrf, 'Bob')
    cid = _kategorie(auth_client, csrf, LITERAL_PAYLOAD_KATEGORIE)
    _ausgabe(auth_client, csrf, pid, cid)

    html = _text(auth_client, f'/month/{JAHR}/{MONAT}')
    literale = [inhalt for inhalt, _ in _template_literale_mit_einsetzungen(html)
                if '<' in inhalt]
    assert len(literale) > 10, 'Skriptblock fehlt - der Test prueft nichts'

    funde = _ungeschuetzte_einsetzungen(html)
    assert not funde, (
        'Ungeschuetzte Einsetzungen im HTML der Monatsseite: '
        + '; '.join(f'{kette!r} in ${{{ausdruck}}}' for ausdruck, kette in funde))


def test_einstellungsseite_js_ohne_ungeschuetzte_interpolation(auth_client, csrf, db):
    """Dieselbe Pruefung fuer settings.html - dort baute buildSplitPanelHtml()
    den Personennamen roh in innerHTML, waehrend die servergerenderte
    Zwillingsstelle im Markup maskiert war. Der Fehler feuerte bei jedem Klick
    auf 'Kategorie hinzufuegen'."""
    _person(auth_client, csrf, MARKUP_PAYLOAD)
    _kategorie(auth_client, csrf, LITERAL_PAYLOAD_KATEGORIE)

    html = _text(auth_client, '/settings')
    assert 'buildSplitPanelHtml' in html, 'Split-Panel-Code fehlt - Test prueft nichts'

    funde = _ungeschuetzte_einsetzungen(html)
    assert not funde, (
        'Ungeschuetzte Einsetzungen im HTML der Einstellungsseite: '
        + '; '.join(f'{kette!r} in ${{{ausdruck}}}' for ausdruck, kette in funde))


def test_escape_helfer_maskiert_alle_gefaehrlichen_zeichen(auth_client):
    """Der gemeinsame Helfer selbst ist die Grundlage aller anderen Tests hier.

    Die Vorgaengerfassung pruefte nur, DASS 'window.escHtml = esc;' im
    Dokument steht. Faellt aus esc() eine einzelne Ersetzung heraus - etwa die
    des Anfuehrungszeichens, mit der jeder Attribut-Ausbruch steht und faellt -
    blieb sie gruen. Geprueft wird deshalb der Rumpf der Funktion.
    """
    html = _text(auth_client, f'/month/{JAHR}/{MONAT}')
    assert 'window.escHtml = esc;' in html, 'esc() wird nicht exportiert'

    rumpf = re.search(r'function esc\s*\([^)]*\)\s*\{(.*?)\n  \}', html, re.S)
    assert rumpf, 'esc() nicht im ausgelieferten Dokument gefunden'
    koerper = rumpf.group(1)
    for zeichen, ersatz in (('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;'),
                            ('"', '&quot;'), ("'", '&#39;')):
        assert ersatz in koerper, (
            f'esc() maskiert {zeichen!r} nicht mehr (Ersatz {ersatz!r} fehlt) - '
            'damit ist jede darauf aufbauende Senke wieder offen')
    # '&' muss zuerst ersetzt werden, sonst maskiert der Helfer seine eigene
    # Ausgabe nach ('&lt;' wuerde zu '&amp;lt;').
    assert koerper.index('&amp;') < koerper.index('&lt;'), \
        'esc() ersetzt & nicht zuerst - die Maskierung frisst sich selbst'


# ── Senke 4: Chart-Legende in overview.html ─────────────────────────────────

def test_uebersicht_legende_ohne_innerhtml(auth_client, csrf, db):
    """Die Legende der Kategorie-Auswertung baut Name und Farbe ueber den DOM.

    Vorher suchte dieser Test nach drei woertlichen Codestellen
    ('legendEl.innerHTML = entries.map', ...). Jede Umbenennung haette ihn
    entwertet. Geprueft wird jetzt zweierlei: das ausgelieferte Skript
    enthaelt keine ungeschuetzte HTML-Einsetzung, und die Legende entsteht
    nachweislich ueber createElement/textContent statt ueber innerHTML.
    """
    # Der Chart-Block steht in `{% if persons_list %}` - ohne Person wird das
    # Skript gar nicht ausgeliefert und der Test wuerde nichts pruefen.
    pid = _person(auth_client, csrf, MARKUP_PAYLOAD)
    cid = _kategorie(auth_client, csrf, MARKUP_PAYLOAD + ' Kat', color=FARB_PAYLOAD)
    _ausgabe(auth_client, csrf, pid, cid)

    html = _text(auth_client, '/overview')
    assert 'categoryChart' in html, 'Chart-Skript fehlt - Test prueft nichts'

    funde = _ungeschuetzte_einsetzungen(html)
    assert not funde, (
        'Ungeschuetzte Einsetzungen im HTML der Uebersicht: '
        + '; '.join(f'{kette!r} in ${{{ausdruck}}}' for ausdruck, kette in funde))

    # Der Legendenaufbau selbst: Text als Textknoten, Farbe ueber safeColor.
    legende = re.search(r"legendEl\.textContent = '';(.*?)\n  \}", html, re.S)
    assert legende, 'Legendenaufbau nicht gefunden - Test prueft nichts'
    block = legende.group(1)
    assert 'innerHTML' not in block, 'Die Legende baut wieder per innerHTML'
    assert 'createTextNode' in block or 'textContent' in block, \
        'Der Kategoriename landet nicht mehr als Text im DOM'
    assert 'safeColor(' in block, 'Die Kategoriefarbe laeuft nicht mehr durch safeColor'


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
