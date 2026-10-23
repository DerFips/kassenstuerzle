"""Nicht erkannte Zeilen beim PDF-Import sichtbar machen.

Frueher verwarf `extract_bank_statement` jede Buchung mit unbekanntem Typ, ohne
Betrag oder mit ungueltigem Datum still - der Nutzer sah nur "N Buchungen
gefunden". Jetzt landet alles Verworfene in `unrecognized`, damit es sich
ansehen, von Hand uebernehmen oder als TXT sichern laesst.

Die Erkennung selbst bleibt bewusst unveraendert (siehe Regressionstest unten).
"""
import io
import re

from app.translations import TRANSLATIONS

# Fixture und Hilfsfunktion des Parser-Tests wiederverwenden (der Text-Layer
# wird ueber ein Fake-Modul vorgegeben, pdfplumber ist hier nicht installiert).
from test_xss_pdf_import import _extract, fake_pdfplumber  # noqa: F401

KEIN_FEHLER_TEXT = (
    "02.01.2025Kartenzahlung onl Amazon / Berlin 2025-01-01 -12,99\n"
    "03.01.2025Basislastschrift -45,10\n"
    "Stadtwerke\n"
    "Strom Januar\n"
    "09.01.2025Überweisung -1.234,56\n"
    "Vermieter\n"
    "13.01.2025Gutschrift\n"
    "Erstattung 15,00\n"
)


def test_bisher_erkannte_buchungen_bleiben_identisch(fake_pdfplumber):
    """Regressionsanker: die Erkennung wird durch diese Aenderung nicht angefasst."""
    res = _extract(fake_pdfplumber, KEIN_FEHLER_TEXT)
    erkannt = [(t['date'], t['amount'], t['is_income'], t['counterpart'])
               for t in res['transactions']]
    assert erkannt == [
        ('01.01.2025', 12.99, False, 'Amazon'),
        ('03.01.2025', 45.10, False, 'Stadtwerke'),
        ('09.01.2025', 1234.56, False, 'Vermieter'),
        ('13.01.2025', 15.0, True, 'Erstattung'),
    ]
    assert res['unrecognized'] == []
    assert res['unrecognized_total'] == 0


def test_unbekannter_typ_landet_in_nicht_erkannt(fake_pdfplumber):
    text = (
        "02.01.2025Kartenzahlung onl Amazon / Berlin 2025-01-01 -12,99\n"
        "04.01.2025Dauerüberweisung -100,00\n"
        "Max Mustermann\n"
        "Miete Januar\n"
    )
    res = _extract(fake_pdfplumber, text)
    assert len(res['transactions']) == 1
    assert res['unrecognized_total'] == 1
    e = res['unrecognized'][0]
    assert e['reason'] == 'unknown_type'
    assert e['date_iso'] == '2025-01-04'
    assert e['amount_guess'] == -100.0
    assert e['likely_noise'] is False
    assert e['lines'] == ['04.01.2025Dauerüberweisung -100,00', 'Max Mustermann', 'Miete Januar']


def test_zeile_mit_zwei_datumsangaben_wird_gemeldet(fake_pdfplumber):
    res = _extract(fake_pdfplumber, "10.01.2025 10.01.2025 Basislastschrift -9,99\n")
    assert res['transactions'] == []
    e = res['unrecognized'][0]
    assert (e['reason'], e['date_iso'], e['amount_guess']) == ('unknown_type', '2025-01-10', -9.99)


def test_buchung_ohne_betrag_wird_gemeldet(fake_pdfplumber):
    res = _extract(fake_pdfplumber, "05.01.2025Überweisung\nOhne Betrag hier\n")
    assert res['transactions'] == []
    e = res['unrecognized'][0]
    assert e['reason'] == 'no_amount'
    assert e['amount_guess'] is None
    assert e['likely_noise'] is False, 'bekannter Typ ohne Betrag ist eine echte Buchung'


def test_ungueltiges_datum_wird_gemeldet(fake_pdfplumber):
    res = _extract(fake_pdfplumber, "31.02.2025Überweisung -5,00\nIrgendwer\n")
    assert res['transactions'] == []
    e = res['unrecognized'][0]
    assert (e['reason'], e['date_iso'], e['amount_guess']) == ('bad_date', '', -5.0)


def test_betragsvorschlag_nimmt_den_letzten_betrag_und_versteht_nachgestelltes_minus(fake_pdfplumber):
    text = (
        "11.01.2025Foo 50,00-\n"
        "12.01.2025Bar Test 3,50 EUR -3,50\n"
        "13.01.2025Baz 1.234,56\n"
        "14.01.2025Qux 1234,56\n"
    )
    res = _extract(fake_pdfplumber, text)
    vorschlaege = [e['amount_guess'] for e in res['unrecognized']]
    assert vorschlaege == [-50.0, -3.5, 1234.56, 1234.56]


def test_kopfzeile_ohne_betrag_ist_als_rauschen_markiert(fake_pdfplumber):
    text = (
        "01.01.2025 - 31.01.2025\n"
        "Kontoauszug Nr. 1\n"
        "02.01.2025Dauerüberweisung -1,00\n"
    )
    res = _extract(fake_pdfplumber, text)
    kopf, buchung = res['unrecognized']
    assert kopf['amount_guess'] is None and kopf['likely_noise'] is True
    assert buchung['likely_noise'] is False


def test_markup_in_nicht_erkannten_zeilen_bleibt_unveraendert(fake_pdfplumber):
    """Wie bei den erkannten Buchungen (K3): der Server maskiert nicht, die
    Seite gibt den Text nur ueber textContent aus."""
    payload = '<img src=x onerror=alert(1)>'
    res = _extract(fake_pdfplumber, f"04.01.2025Dauerüberweisung -1,00\n{payload}\n")
    assert payload in res['unrecognized'][0]['lines']


def test_menge_zeilen_und_laenge_sind_begrenzt(fake_pdfplumber):
    bloecke = ''.join(f"{(i % 28) + 1:02d}.01.2025Dauerüberweisung -1,00\n" for i in range(350))
    res = _extract(fake_pdfplumber, bloecke)
    assert res['unrecognized_total'] == 350
    assert len(res['unrecognized']) == 300

    viele = "04.01.2025Dauerüberweisung -1,00\n" + ''.join(f"Zeile {i}\n" for i in range(40))
    langer = "05.01.2025Dauerüberweisung -1,00\n" + 'x' * 500 + "\n"
    e1 = _extract(fake_pdfplumber, viele)['unrecognized'][0]
    e2 = _extract(fake_pdfplumber, langer)['unrecognized'][0]
    assert len(e1['lines']) == 12
    assert max(len(z) for z in e2['lines']) == 200


def test_parse_endpunkt_liefert_nicht_erkannte_zeilen(auth_client, csrf, fake_pdfplumber):
    fake_pdfplumber("04.01.2025Dauerüberweisung -100,00\nMax\n")
    res = auth_client.post(
        '/api/import/pdf/parse', headers={'X-CSRF-Token': csrf},
        data={'file': (io.BytesIO(b'%PDF-1.4 fake'), 'auszug.pdf')},
        content_type='multipart/form-data')
    body = res.get_json()
    assert body['success'] is True
    assert body['unrecognized_total'] == 1
    assert body['unrecognized'][0]['amount_guess'] == -100.0


# ── Oberflaeche ──────────────────────────────────────────────────────────────

NEUE_SCHLUESSEL = [
    'pdf.unrecognized_title', 'pdf.unrecognized_hint', 'pdf.unrecognized_warning',
    'pdf.unrecognized_more', 'pdf.no_amount_group', 'pdf.take_over_btn',
    'pdf.taken_over', 'pdf.export_txt_btn', 'pdf.manual_invalid', 'pdf.manual_type',
    'pdf.reason_unknown_type', 'pdf.reason_no_amount', 'pdf.reason_bad_date',
    'pdf.txt_title', 'pdf.txt_amount_guess', 'pdf.txt_reason',
]


def test_neue_texte_in_beiden_sprachen():
    for sprache in ('de', 'en'):
        fehlend = [k for k in NEUE_SCHLUESSEL if not TRANSLATIONS[sprache].get(k)]
        assert not fehlend, f'{sprache}: {fehlend}'


def test_seite_enthaelt_bereich_fuer_nicht_erkannte_zeilen(auth_client):
    html = auth_client.get('/import/pdf').get_data(as_text=True)
    for ident in ('pdf-unrecognized-card', 'pdf-unrecognized-list', 'pdf-export-txt-btn'):
        assert f'id="{ident}"' in html, ident
    assert 'function renderUnrecognized(' in html
    assert 'function exportUnrecognizedTxt(' in html
    assert 'URL.createObjectURL' in html, 'TXT-Export fehlt'


def test_nicht_erkannte_zeilen_werden_nur_als_text_ausgegeben(auth_client):
    """Rohtext aus dem PDF darf nie ueber innerHTML auf die Seite kommen."""
    html = auth_client.get('/import/pdf').get_data(as_text=True)
    start = html.index('function renderUnrecognized(')
    ende = html.index('function exportUnrecognizedTxt(')
    block = html[start:ende]
    assert 'textContent' in block
    assert 'innerHTML' not in block
    assert 'insertAdjacentHTML' not in block
