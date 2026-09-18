"""Regressionstests zu K3 (Sicherheitsprüfung 18.09.2026):

Gespeichertes XSS über ein präpariertes Kontoauszugs-PDF. `counterpart` und
`description` kommen roh aus der PDF-Textebene (`extract_bank_statement`,
app/routes/main.py) und wurden in `import_pdf.html` ungeschützt per
`row.innerHTML` sowohl als Attributwert (`title="..."`) als auch als
Elementinhalt eingesetzt - eine Mail mit "Kontoauszug.pdf" genügte, kein
Konto war nötig.

Der Sink ist client-seitiges JavaScript (`row.innerHTML` im Browser); ein
Python-Test kann ihn nicht ausführen. Diese Datei prüft daher zwei
unabhängige Dinge:

1. Parser-Ebene (main.py, `extract_bank_statement`): Markup aus dem PDF
   landet unverändert (nur gekürzt) im JSON, das an den Browser geht -
   der Server ist bewusst NICHT die Maskierungsschicht.
2. Template-Struktur (import_pdf.html): die verwundbaren Interpolationen
   sind entweder verschwunden oder durch den gemeinsamen `escHtml`-Helfer
   (app/templates/base.html, siehe K1) abgesichert.

Wichtig: Test 2 ist eine Zeichenketten-/Strukturprüfung des ausgelieferten
Templates, KEIN Beweis dafür, dass im Browser tatsächlich nichts mehr
ausgeführt wird - dafür müsste man das Skript in einer echten JS-Umgebung
laufen lassen. Zusammen mit Test 1 (der zeigt, dass der Payload den Server
unverändert verlässt) grenzt es den Fall aber eng ein: wenn escHtml() an
jeder Stelle steht, an der roher Server-Text in innerHTML eingesetzt wird,
UND escHtml() nachweislich maskiert (siehe test_xss_namen.py, Senke 1-4),
bleibt kein bekannter Pfad für den Payload mehr offen.
"""
import re
import sys
import types

import pytest

MARKUP_PAYLOAD = '<img src=x onerror=alert(1)>'

TEMPLATE_PFAD = 'app/templates/import_pdf.html'


# ── Hilfsfunktionen für die Parser-Tests ─────────────────────────────────────

class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePDF:
    def __init__(self, text):
        self.pages = [_FakePage(text)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def fake_pdfplumber(monkeypatch):
    """`extract_bank_statement` importiert `pdfplumber` lokal und parst nur
    den Text-Layer weiter (`page.extract_text()`). In der Test-Umgebung ist
    `pdfplumber` gar nicht installiert (Bibliothek fehlt -> `srv.pdf_lib_missing`),
    daher wird hier ein minimales Fake-Modul unter `sys.modules['pdfplumber']`
    hinterlegt, das exakt den von uns vorgegebenen Text zurückgibt. So kann
    die eigentliche Text-Parsing-Logik (das, was K3 betrifft) unabhängig von
    einer echten PDF-Binärdatei getestet werden."""
    erzeugtes_modul = {}

    def _mit_text(full_text):
        mod = types.ModuleType('pdfplumber')
        mod.open = lambda pdf_io: _FakePDF(full_text)
        monkeypatch.setitem(sys.modules, 'pdfplumber', mod)
        erzeugtes_modul['mod'] = mod
        return mod

    return _mit_text


def _extract(fake_pdfplumber, full_text):
    from app.routes.main import extract_bank_statement
    fake_pdfplumber(full_text)
    return extract_bank_statement(bytearray(full_text.encode('utf-8')))


# ── Parser-Ebene: Server maskiert bewusst nicht ─────────────────────────────

def test_kontoauszug_counterpart_traegt_markup_unveraendert_weiter(fake_pdfplumber):
    """Eine Überweisungszeile, deren Verwendungszweck aus reinem Markup
    besteht: `extract_bank_statement` liefert den Payload byte-genau (nur auf
    80 Zeichen gekuerzt) zurueck - keine Entity-Maskierung, kein Strippen von
    Tags. Der Server ist nicht die Verteidigungslinie; das Template muss es
    sein (siehe Template-Tests unten)."""
    full_text = f"01.09.2026Ueberweisung -50,00\n{MARKUP_PAYLOAD}\nSonstiges\n"
    result = _extract(fake_pdfplumber, full_text)

    assert result.get('transactions'), 'keine Transaktion erkannt - Test prueft nichts'
    txn = result['transactions'][0]
    assert txn['counterpart'] == MARKUP_PAYLOAD, \
        f'Server hat den Payload veraendert: {txn["counterpart"]!r}'
    assert txn['is_income'] is False
    assert txn['amount'] == 50.0


def test_kontoauszug_description_traegt_markup_unveraendert_weiter(fake_pdfplumber):
    """Gleiche Senke, andere Spalte: bei einer Kartenzahlung landet Markup in
    `description`, ebenfalls unmaskiert."""
    full_text = (
        "02.09.2026Kartenzahlung -12,34\n"
        f"Supermarkt / {MARKUP_PAYLOAD}\n"
        "2026-09-02T10:00:00\n"
    )
    result = _extract(fake_pdfplumber, full_text)

    assert result.get('transactions'), 'keine Transaktion erkannt - Test prueft nichts'
    txn = result['transactions'][0]
    assert MARKUP_PAYLOAD in txn['description'], \
        f'Payload nicht mehr in description enthalten: {txn["description"]!r}'


def test_kontoauszug_kuerzt_aber_maskiert_nicht(fake_pdfplumber):
    """Dokumentiert die einzige serverseitige Behandlung: Kuerzung auf
    80/150 Zeichen, keine Maskierung. Ein Regressionstest, der rot wird,
    sollte jemand faelschlich versuchen, die Behebung serverseitig statt im
    Template nachzuruesten (was den JSON-Vertrag fuer commit/Anzeige aendern
    wuerde)."""
    langer_payload = '<img src=x onerror=alert(1)>' + 'A' * 100
    full_text = f"03.09.2026Ueberweisung -1,00\n{langer_payload}\n"
    result = _extract(fake_pdfplumber, full_text)

    txn = result['transactions'][0]
    assert txn['counterpart'] == langer_payload[:80]
    assert '&lt;' not in txn['counterpart'], 'Server maskiert bereits - Testannahme falsch'


# ── Template-Struktur: escHtml an jeder Senke ───────────────────────────────

def _template_html():
    with open(TEMPLATE_PFAD, encoding='utf-8') as fh:
        return fh.read()


def test_zeile_counterpart_nicht_mehr_unmaskiert_interpoliert():
    """Die urspruengliche Senke aus dem Bericht (K3, import_pdf.html:115-121):
    `title="${txn.counterpart||txn.description||''}"` und der Zelleninhalt
    direkt danach - beides ohne jede Maskierung."""
    html = _template_html()
    assert 'title="${txn.counterpart' not in html, \
        'counterpart/description landen weiterhin unmaskiert im title-Attribut'
    assert '>${txn.counterpart||txn.description' not in html, \
        'counterpart/description landen weiterhin unmaskiert im Zelleninhalt'


def test_zeile_baut_werte_nur_noch_ueber_eschtml():
    """Alle vier ehemals rohen Interpolationen (date, type, counterpart/
    description als Attribut UND als Inhalt) muessen jetzt durch escHtml
    laufen."""
    html = _template_html()
    assert 'escHtml(txn.date)' in html
    assert "escHtml(txn.type" in html
    # counterpart/description werden ueber eine gemeinsame Variable escaped -
    # es muss mindestens zwei escHtml(...)-Aufrufe geben, die diese Variable
    # (oder direkt txn.counterpart/txn.description) einsetzen.
    zeilen_block = re.search(r'row\.innerHTML = `.*?`;', html, re.S)
    assert zeilen_block, 'row.innerHTML-Block nicht gefunden - Test prueft nichts'
    block = zeilen_block.group(0)
    assert block.count('escHtml(') >= 4, \
        f'weniger escHtml()-Aufrufe im Zeilen-Template als erwartet: {block!r}'
    assert '${txn.counterpart' not in block, \
        'txn.counterpart wird weiterhin ungeschuetzt in das Template-Literal eingesetzt'
    assert '${txn.description' not in block, \
        'txn.description wird weiterhin ungeschuetzt in das Template-Literal eingesetzt'


def test_fehlermeldung_beim_parsen_wird_maskiert():
    """Zweite im Bericht genannte Stelle: der Fehlerpfad um Zeile 104
    (`${d.error}` direkt in innerHTML)."""
    html = _template_html()
    assert '${d.error}' not in html, \
        'd.error wird weiterhin ungeschuetzt in innerHTML eingesetzt'
    assert 'escHtml(d.error)' in html


def test_seite_nutzt_gemeinsamen_escape_helfer():
    """Es soll KEINE zweite Escaping-Implementierung entstehen - die Seite
    muss den in base.html (K1) exportierten Helfer wiederverwenden."""
    html = _template_html()
    assert 'function esc(' not in html, \
        'import_pdf.html definiert einen eigenen Escape-Helfer statt escHtml zu nutzen'
    assert html.count('escHtml(') >= 5


def test_ausgelieferte_seite_enthaelt_eschtml_helfer(auth_client):
    """Gegenprobe end-to-end: wenn /import/pdf ausgeliefert wird, ist
    `window.escHtml` (aus base.html) tatsaechlich Teil des Dokuments, auf das
    sich import_pdf.html verlaesst."""
    res = auth_client.get('/import/pdf')
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'window.escHtml = esc;' in html
    assert 'escHtml(txn.date)' in html
