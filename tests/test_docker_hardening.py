"""Regressionstests für die Infrastruktur-Befunde K4 und I2 (Sicherheitsprüfung
2026-09-18): fehlende .dockerignore bzw. Container, der als root läuft.

Diese Tests lesen nur Textdateien im Projekt - sie starten keinen Docker-Build
und berühren weder data/ noch data_/.
"""
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_dockerignore_exists_and_excludes_secrets_and_data():
    """Ohne .dockerignore backt `COPY . .` (Dockerfile) .env, data/ und data_/
    dauerhaft in eine Image-Schicht (Befund K4). Eine bloße Existenzpruefung
    waere kein Regressionswaechter, deshalb werden die kritischen Eintraege
    einzeln verlangt."""
    dockerignore = PROJECT_ROOT / '.dockerignore'
    assert dockerignore.is_file(), '.dockerignore fehlt'

    lines = {line.strip() for line in dockerignore.read_text(encoding='utf-8').splitlines()}

    required = {'.env', '.env.*', 'data/', 'data_/', '.git/'}
    missing = required - lines
    assert not missing, f'.dockerignore fehlen kritische Eintraege: {missing}'

    # Mindestens ein Muster muss Datenbankdateien/Sidecars abdecken.
    db_patterns = {l for l in lines if l.startswith('*.db')}
    assert db_patterns, '.dockerignore deckt keine *.db*-Dateien ab'


def test_dockerfile_does_not_run_as_root():
    """Befund I2: das Dockerfile hatte keine USER-Anweisung, der Container
    lief als root. Prueft, dass NACH den apt-get/pip-Install-Schritten ein
    nicht-root-Benutzer gesetzt wird und aktiv bleibt (kein spaeteres
    `USER root`)."""
    dockerfile = PROJECT_ROOT / 'Dockerfile'
    content = dockerfile.read_text(encoding='utf-8')
    lines = content.splitlines()

    user_lines = [l.strip() for l in lines if l.strip().startswith('USER ')]
    assert user_lines, 'Dockerfile setzt keinen USER - Container laeuft als root'
    assert all(l != 'USER root' for l in user_lines), (
        'Dockerfile setzt USER root - Container laeuft weiterhin als root')

    # Die USER-Anweisung muss NACH der Paketinstallation kommen (Layer-
    # Reihenfolge), sonst schlaegt ein `apt-get`/`pip install` als
    # unprivilegierter Benutzer fehl bzw. die Absicherung greift nicht.
    install_idx = max(
        i for i, l in enumerate(lines)
        if 'apt-get install' in l or 'pip install' in l
    )
    user_idx = min(i for i, l in enumerate(lines) if l.strip().startswith('USER '))
    assert user_idx > install_idx, (
        'USER-Anweisung steht vor der Paketinstallation')
