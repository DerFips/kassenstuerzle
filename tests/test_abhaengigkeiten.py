"""Regressionstests für Befund I10 (Sicherheitsprüfung 2026-09-18):
transitive Abhängigkeiten ungepinnt, Pillow veraltet.

Ehrlichkeitshinweis zur Aussagekraft: eine Prüfung auf eine Versionszeichen-
kette ist schwach. Sie beweist nicht, dass die Version sicher ist, und sie
altert (irgendwann ist auch die hier verlangte Untergrenze alt). Sie hält
genau zwei Dinge fest: dass niemand still auf die verwundbare Pillow-Reihe
zurückfällt, und dass die gepinnten transitiven Versionen mit dem
übereinstimmen, was hier tatsächlich installiert ist — erfundene Nummern
fliegen damit auf. Der eigentliche Schutz ist requirements.lock; dass dessen
Mechanismus dokumentiert und im Build verdrahtet ist, prüft der letzte Block.

Diese Tests lesen nur Textdateien im Projekt und die Metadaten des
Entwicklungs-venv - kein Netzzugriff, kein Zugriff auf data/ oder data_/.
"""
import pathlib
import re

import pytest

try:                                    # Py>=3.8
    from importlib.metadata import PackageNotFoundError, version as _paketversion
except ImportError:                     # pragma: no cover
    from importlib_metadata import PackageNotFoundError, version as _paketversion

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
REQUIREMENTS = PROJECT_ROOT / 'requirements.txt'


def _pins():
    """requirements.txt -> {paketname: version} für alle ==-Pins."""
    pins = {}
    for zeile in REQUIREMENTS.read_text(encoding='utf-8').splitlines():
        blank = zeile.split('#', 1)[0].strip()
        if not blank:
            continue
        pins[blank.split('==')[0].strip()] = blank.split('==', 1)[1].strip() \
            if '==' in blank else None
    return pins


def _als_zahlen(v):
    return tuple(int(t) for t in re.findall(r'\d+', v)[:3])


def test_jede_zeile_ist_hart_gepinnt():
    """Kein `>=`, kein `~=`, kein nackter Paketname: sonst löst der Build die
    Version wieder frei auf und genau das war I10."""
    ungepinnt = [name for name, ver in _pins().items() if ver is None]
    assert not ungepinnt, f'Nicht hart gepinnte Einträge: {ungepinnt}'


def test_pillow_nicht_mehr_auf_der_alten_reihe():
    """Pillow==10.0.1 (Sept. 2023) verarbeitet in main.py:2776-2783 direkt
    hochgeladene, also angreiferkontrollierte Bild-Bytes. Seither sind mehrere
    Speicherfehler in den Decodern behoben worden."""
    pins = _pins()
    assert 'Pillow' in pins, 'Pillow ist aus requirements.txt verschwunden'

    ver = _als_zahlen(pins['Pillow'])
    assert ver != (10, 0, 1), 'Pillow steht wieder auf der verwundbaren 10.0.1'
    assert ver >= (10, 4), f'Pillow=={pins["Pillow"]} ist aelter als die Untergrenze 10.4'


def test_kern_der_transitiven_abhaengigkeiten_ist_gepinnt():
    """Die Pakete, die bei jedem Build neu aufgelöst wurden und im Prüfbericht
    namentlich stehen, müssen jetzt festgeschrieben sein."""
    pins = _pins()
    verlangt = {'Werkzeug', 'Jinja2', 'MarkupSafe', 'itsdangerous', 'click',
                'blinker', 'limits', 'typing_extensions', 'pytz', 'tzlocal'}
    fehlend = verlangt - set(pins)
    assert not fehlend, (
        f'Transitive Abhaengigkeiten weiterhin ungepinnt: {sorted(fehlend)}')


def test_gepinnte_versionen_entsprechen_der_installierten_umgebung():
    """Wächter gegen erfundene Versionsnummern.

    Die transitiven Pins wurden ohne Netzzugang am Entwicklungs-venv
    abgelesen. Jeder Pin, dessen Paket hier installiert ist, MUSS deshalb
    exakt der installierten Version entsprechen. Pakete, die hier gar nicht
    installiert sind (Pillow, pdfplumber, gunicorn, ...), sind nicht prüfbar
    und werden übersprungen - dafür gibt es requirements.lock.
    """
    abweichungen = []
    geprueft = 0
    for name, gepinnt in _pins().items():
        try:
            installiert = _paketversion(name)
        except PackageNotFoundError:
            continue
        geprueft += 1
        if installiert != gepinnt:
            abweichungen.append(f'{name}: gepinnt {gepinnt}, installiert {installiert}')

    assert geprueft >= 10, (
        f'Nur {geprueft} Pins gegen die Umgebung pruefbar - zu wenig, um etwas zu zeigen')
    assert not abweichungen, (
        'Gepinnte Version weicht von der tatsaechlich installierten ab '
        f'(also nicht abgelesen, sondern geraten?): {abweichungen}')


@pytest.mark.parametrize('nadel', ['requirements.lock', 'compile'])
def test_requirements_dokumentiert_den_lock_mechanismus(nadel):
    """Ohne Netzzugang konnte die Lock-Datei nicht erzeugt werden. Dann muss
    wenigstens schwarz auf weiss dastehen, was dafuer laufen muss."""
    text = REQUIREMENTS.read_text(encoding='utf-8')
    assert nadel in text, f'requirements.txt erwaehnt "{nadel}" nicht'


def test_make_ziel_fuer_requirements_lock_existiert():
    makefile = PROJECT_ROOT / 'Makefile'
    assert makefile.is_file(), 'Makefile mit dem Lock-Ziel fehlt'
    text = makefile.read_text(encoding='utf-8')
    assert re.search(r'^requirements\.lock\s*:', text, re.M), (
        'Makefile hat kein Ziel "requirements.lock"')
    assert 'pip compile' in text or 'pip-compile' in text, (
        'Das Lock-Ziel ruft weder uv pip compile noch pip-compile auf')


def test_dockerfile_bevorzugt_die_lock_datei():
    """Ein Lock-Ziel nuetzt nichts, wenn der Image-Bau es ignoriert."""
    text = (PROJECT_ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'requirements.lock' in text, (
        'Dockerfile zieht requirements.lock nicht heran - der Build bleibt '
        'unreproduzierbar, auch wenn die Lock-Datei existiert')
