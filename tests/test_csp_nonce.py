"""CSP-Nonce-Infrastruktur und Abbau der Inline-Ereignisattribute (Befund I6).

Was diese Tests NICHT koennen: JavaScript ausfuehren. Ob ein umgestellter
Handler im Browser noch feuert, kann hier niemand pruefen - dafuer gibt es
den Browser-Durchgang im Abschlussschritt. Geprueft wird ausschliesslich,
was am gerenderten HTML und am Header ablesbar ist.
"""
import pathlib
import re

import pytest

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'templates'

# Ereignisattribute im Markup: on...= ausserhalb von Kommentaren.
_ON_ATTR = re.compile(r'\bon[a-z]+\s*=\s*["\']')

# Genau die Bloecke, die eine Nonce tragen muessen. <script src=...> auf CDNs
# bleibt aussen vor, die deckt die Host-Quelle in script-src ab.
_NONCE_ERWARTET = {
    'base.html': 4,
    'month.html': 1,
    'settings.html': 2,
    'import_pdf.html': 1,
    'overview.html': 2,   # inkl. des JSON-Datenblocks
    'scan_receipt.html': 1,
    'offline.html': 1,
}


def _seiten_nonces(html):
    return re.findall(r'<script[^>]*\snonce="([^"]+)"', html)


# ── 1. Die gerenderte Seite traegt Nonces an den Inline-Skripten ──────────────

@pytest.mark.parametrize('pfad', ['/overview', '/settings', '/month/2026/9'])
def test_inline_skripte_tragen_nonce(auth_client, pfad):
    html = auth_client.get(pfad).get_data(as_text=True)
    nonces = _seiten_nonces(html)
    assert nonces, f'{pfad}: kein einziges <script> mit nonce-Attribut'
    # Alle Bloecke einer Antwort muessen denselben Wert tragen, sonst passt
    # spaeter nur ein Teil davon zum Header.
    assert len(set(nonces)) == 1, f'{pfad}: uneinheitliche Nonces {set(nonces)}'
    assert nonces[0] != '', f'{pfad}: leeres nonce-Attribut'
    assert len(nonces[0]) >= 16, f'{pfad}: Nonce zu kurz: {nonces[0]!r}'


def test_kein_inline_skript_ohne_nonce(auth_client):
    """Ein Block ohne Nonce faellt im Abschlussschritt stumm aus."""
    html = auth_client.get('/settings').get_data(as_text=True)
    ohne_src = [tag for tag in re.findall(r'<script[^>]*>', html)
                if ' src=' not in tag]
    assert ohne_src, 'Testannahme kaputt: /settings hat keine Inline-Skripte'
    for tag in ohne_src:
        assert 'nonce="' in tag, f'Inline-Skript ohne Nonce: {tag}'


# ── 2. Die Nonce ist pro Request frisch ──────────────────────────────────────

def test_nonce_unterscheidet_sich_zwischen_requests(auth_client):
    """Ein fester Wert waere wertlos: wer ihn kennt, umgeht die CSP."""
    a = _seiten_nonces(auth_client.get('/overview').get_data(as_text=True))
    b = _seiten_nonces(auth_client.get('/overview').get_data(as_text=True))
    assert a and b
    assert a[0] != b[0], f'Nonce ist konstant: {a[0]!r}'


def test_nonce_auch_ueber_verschiedene_seiten_frisch(auth_client):
    a = _seiten_nonces(auth_client.get('/settings').get_data(as_text=True))
    b = _seiten_nonces(auth_client.get('/month/2026/9').get_data(as_text=True))
    assert a and b and a[0] != b[0]


# ── 3. Die umgestellten Templates haben keine Ereignisattribute mehr ─────────

def _markup_ohne_kommentare(name):
    """Jinja- und HTML-Kommentare raus: dort stehen on...=-Erwaehnungen als
    Prosa (z.B. die XSS-Erklaerung in base.html), die kein Handler sind."""
    text = (TEMPLATES / name).read_text(encoding='utf-8')
    text = re.sub(r'\{#.*?#\}', '', text, flags=re.S)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'^\s*//.*$', '', text, flags=re.M)
    return text


@pytest.mark.parametrize('name', ['base.html', 'offline.html', 'settings.html',
                                  'month.html'])
def test_umgestellte_templates_ohne_inline_handler(name):
    treffer = _ON_ATTR.findall(_markup_ohne_kommentare(name))
    assert not treffer, f'{name}: Inline-Handler uebrig: {treffer}'


def test_konkrete_entfernte_handler_sind_weg():
    """Namentlich die Handler aus diesem Schritt - damit ein Rueckfall auf
    genau diese Stellen nicht durch eine andere Zaehlung verdeckt wird."""
    settings = _markup_ohne_kommentare('settings.html')
    base = _markup_ohne_kommentare('base.html')
    offline = _markup_ohne_kommentare('offline.html')
    for schnipsel in ('onchange="toggleCatSplitPanel(this)"',
                      'onclick="enablePush()"',
                      'onclick="createBackup()"',
                      'onclick="restoreBackup()"',
                      "onclick=\"deleteBackup('"):
        assert schnipsel not in settings, f'settings.html: {schnipsel} noch da'
    assert 'onclick="location.reload()"' not in base
    assert 'onclick="location.reload()"' not in offline


def test_ersatzverdrahtung_vorhanden():
    """Handler entfernt UND nicht ersetzt waere eine stille Regression."""
    settings = (TEMPLATES / 'settings.html').read_text(encoding='utf-8')
    for id_ in ('createBackupBtn', 'restoreBackupBtn', 'enablePushBtn'):
        assert f"getElementById('{id_}')" in settings and 'addEventListener' in settings
    assert 'js-backup-delete' in settings
    assert "getElementById('categories-list').addEventListener('change'" in settings
    base = (TEMPLATES / 'base.html').read_text(encoding='utf-8')
    assert 'js-activity-reload' in base
    offline = (TEMPLATES / 'offline.html').read_text(encoding='utf-8')
    assert "getElementById('offline-retry-btn')" in offline


# ── 3b. month.html: die 34 Handler aus Schritt 2 ─────────────────────────────

# Genau das Muster aus der Aufgabenstellung. Strenger als _ON_ATTR: es trifft
# auch eine Erwaehnung im Kommentar. Wer einen Handler "nur im Kommentar"
# stehen laesst, faellt hier auf - und beim Lesen des Diffs ist nicht mehr
# unterscheidbar, ob er wirklich weg ist.
_ON_ATTR_ROH = re.compile(r' on[a-z]*=')


def test_month_html_hat_kein_einziges_ereignisattribut_mehr():
    text = (TEMPLATES / 'month.html').read_text(encoding='utf-8')
    treffer = _ON_ATTR_ROH.findall(text)
    assert not treffer, f'month.html: {len(treffer)} Ereignisattribute uebrig'


def test_month_html_entfernte_handler_sind_namentlich_weg():
    """Namentlich, damit ein Rueckfall auf eine einzelne Stelle nicht durch
    eine andere Zaehlung verdeckt wird. Die Zeilenvorlagen (addRow /
    addIncomeRow) stehen hier zuerst: sie sind der heikle Teil, weil ihre
    Elemente erst zur Laufzeit entstehen."""
    text = (TEMPLATES / 'month.html').read_text(encoding='utf-8')
    for schnipsel in (
            # dynamisch gebaute Ausgaben-Zeile
            'onchange="scheduleAutoSaveExpense()"',
            'onchange="updatePaidForStyle(this); scheduleAutoSaveExpense()"',
            'onchange="updateRowColor(this); scheduleAutoSaveExpense()"',
            'onchange="updateFooter(); scheduleAutoSaveExpense()"',
            'onfocus="formulaFocus(this)"',
            'onblur="formulaBlur(this); updateFooter()"',
            'oninput="updateFooter()"',
            'onkeydown="handleAmountTab(event, this)"',
            'onclick="receiptClick(this)"',
            'onclick="removeExpenseRow(this)"',
            # dynamisch gebaute Einnahmen-Zeile
            'onchange="scheduleAutoSaveIncome()"',
            'oninput="updateIncomeFooter()"',
            'onclick="removeIncomeRow(this)"',
            # servergerendertes Markup
            'onclick="scanReceipt()"',
            'onclick="applyReceiptScan()"',
            "onclick=\"clearTable('expenseBody', true)\"",
            "onclick=\"clearTable('incomeBody', false)\"",
            'onclick="insertRecurring()"',
            'onclick="addRow()"',
            'onclick="addIncomeRow()"',
            'onclick="saveExpenses()"',
            'onclick="saveIncome()"',
            'onchange="applyExpenseFilter()"',
            'onchange="applyIncomeFilter()"',
            'onclick="resetExpenseFilter()"',
            'onclick="resetIncomeFilter()"',
            'onchange="toggleSettle(this.checked)"',
            # zur Laufzeit erzeugte Knoepfe ausserhalb der Tabellen
            'onclick="deleteReceipt(this)"',
            "onclick=\"this.closest('.toast').remove()\"",
    ):
        assert schnipsel not in text, f'month.html: {schnipsel} noch da'


def test_month_html_ersatzverdrahtung_vorhanden():
    """Attribut entfernt UND nicht ersetzt waere eine stille Regression, die
    der reine Abwesenheitstest durchwinkt."""
    text = (TEMPLATES / 'month.html').read_text(encoding='utf-8')

    # Delegation an den beiden stabilen <tbody>, je Ereignisart einmal.
    assert "function verdrahteAusgabenTabelle()" in text
    assert "function verdrahteEinnahmenTabelle()" in text
    for ereignis in ("'change'", "'input'", "'keydown'", "'click'"):
        assert f"body.addEventListener({ereignis}" in text, (
            f'kein delegierter Listener fuer {ereignis}')

    # focus/blur steigen nicht auf: pro Element, beim Anlegen der Zeile.
    assert "function verdrahteBetragsfeld(input)" in text
    assert "input.addEventListener('focus'" in text
    assert "input.addEventListener('blur'" in text
    # ... und in BEIDEN Zeilenvorlagen aufgerufen (Ausgaben und Einnahmen).
    assert text.count('verdrahteBetragsfeld(amtInput);') == 2

    # Servergerenderte Schalter.
    assert "function verdrahteStatischeBedienelemente()" in text
    for id_ in ('receiptScanBtn', 'receiptApplyBtn', 'clearExpensesBtn',
                'recurBtn', 'addExpenseRowBtn', 'saveBtn', 'filterPerson',
                'filterCat', 'resetExpenseFilterBtn', 'clearIncomeBtn',
                'addIncomeRowBtn', 'saveIncomeBtn', 'incFilterPerson',
                'resetIncomeFilterBtn'):
        assert f"an('{id_}'," in text, f'{id_} nicht verdrahtet'
    assert "toggleSettle(abrechnung.checked)" in text

    # Die Haken, an denen die Delegation die Knoepfe wiedererkennt. Ohne sie
    # trifft closest() den falschen Knopf: die Zeilen-Mülltonne und der
    # Löschen-Knopf der Belegvorschau teilen sich btn-outline-danger.
    assert 'js-row-remove' in text
    assert 'js-receipt-delete' in text

    # Verdrahtet wird VOR dem Erzeugen der Zeilen und vor dem focusout-
    # Autosave, damit die Reihenfolge der Listener am <tbody> der alten
    # Reihenfolge (Attribut-Handler zuerst) entspricht.
    init = text[text.index("document.addEventListener('DOMContentLoaded'"):]
    assert init.index('verdrahteAusgabenTabelle();') < init.index('EXISTING.forEach')
    assert init.index('verdrahteEinnahmenTabelle();') < init.index("'focusout'")


def test_gerenderte_monatsseite_ohne_ereignisattribute(auth_client):
    """Das Template ist das eine, die ausgelieferte Seite das andere: hier
    haengt auch drin, was base.html beisteuert."""
    html = auth_client.get('/month/2026/9').get_data(as_text=True)
    treffer = _ON_ATTR.findall(html)
    assert not treffer, f'/month/2026/9: Ereignisattribute im HTML: {treffer}'


# ── 4. Der Header ist scharf geschaltet ─────────────────────────────────────

def test_script_src_nutzt_nonce_statt_unsafe_inline(auth_client):
    """Der eigentliche Zweck von Befund I6.

    'unsafe-inline' erlaubte jedes eingeschleuste Skript und entwertete damit
    genau den Schutz, der hinter den XSS-Befunden K1 und K3 greifen soll.
    """
    csp = auth_client.get('/overview').headers['Content-Security-Policy']
    script_src = [d for d in csp.split(';') if d.strip().startswith('script-src')][0]
    assert 'nonce-' in script_src, f'Keine Nonce in script-src: {script_src}'
    assert "'unsafe-inline'" not in script_src, (
        f"script-src erlaubt weiterhin beliebige Inline-Skripte: {script_src}")


def test_nonce_im_header_passt_zu_der_im_html(auth_client):
    """Header und Markup muessen denselben Wert tragen.

    Weichen sie ab, blockt der Browser die eigenen Skriptbloecke der App -
    ein Fehler, der in den Python-Tests sonst voellig unsichtbar waere, weil
    dort nie JavaScript laeuft.
    """
    antwort = auth_client.get('/overview')
    csp = antwort.headers['Content-Security-Policy']
    script_src = [d for d in csp.split(';') if d.strip().startswith('script-src')][0]
    nonce_aus_header = script_src.split("'nonce-")[1].split("'")[0]

    html = antwort.get_data(as_text=True)
    assert f'nonce="{nonce_aus_header}"' in html, (
        'Die Nonce im Header taucht so nicht im HTML auf - der Browser wuerde '
        'die eigenen Skriptbloecke blockieren.')


def test_style_src_darf_unsafe_inline_behalten(auth_client):
    """Abgrenzung, damit niemand aus Eifer zu viel abschaltet.

    Die Templates nutzen an vielen Stellen style="..."-Attribute. Die
    unterliegen style-src, nicht script-src, und sind ein deutlich
    geringeres Risiko - Befund I6 betrifft sie ausdruecklich nicht.
    """
    csp = auth_client.get('/overview').headers['Content-Security-Policy']
    style_src = [d for d in csp.split(';') if d.strip().startswith('style-src')][0]
    assert "'unsafe-inline'" in style_src


def test_json_antwort_bekommt_gueltigen_header(auth_client):
    """Auch Antworten ohne Template muessen einen vollstaendigen Header haben.

    Die Nonce entsteht in before_request, nicht erst beim Abruf aus dem
    Template. Waere sie an das Rendern gekoppelt, stuende hier 'nonce-' ohne
    Wert - ein Header, den Browser als kaputte Quellenliste behandeln.
    """
    antwort = auth_client.get('/api/activity?scope=2026-09')
    csp = antwort.headers['Content-Security-Policy']
    script_src = [d for d in csp.split(';') if d.strip().startswith('script-src')][0]
    assert "'nonce-'" not in script_src, f'Leere Nonce im Header: {script_src}'
    nonce = script_src.split("'nonce-")[1].split("'")[0]
    assert len(nonce) >= 16, f'Nonce zu kurz oder leer: {nonce!r}'
