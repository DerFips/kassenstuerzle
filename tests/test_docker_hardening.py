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


def test_dockerfile_pinnt_uid_und_gid_auf_1000():
    """Blocker 2: `useradd -r` ohne -u vergibt eine BELIEBIGE System-uid.

    Zur Laufzeit verdeckt das Bind-Mount ./data:/app/data das im Image
    gechownte /app/data - massgeblich ist dann allein, wem ./data auf dem Host
    gehoert. Ohne feste uid/gid laesst sich das Host-Verzeichnis gar nicht
    verlaesslich vorbereiten, und der Start scheitert auf einem Linux-Host in
    _open_db am `PRAGMA journal_mode=WAL` (kein Schreibrecht fuer -wal/-shm).
    """
    content = (PROJECT_ROOT / 'Dockerfile').read_text(encoding='utf-8')

    assert 'groupadd -r -g 1000' in content, (
        'Dockerfile pinnt die gid nicht auf 1000')
    assert 'useradd -r -u 1000' in content, (
        'Dockerfile pinnt die uid nicht auf 1000')

    user_lines = [l.strip() for l in content.splitlines()
                  if l.strip().startswith('USER ')]
    assert user_lines[-1] == 'USER 1000:1000', (
        'Die USER-Anweisung nennt nicht die gepinnte uid:gid, sondern '
        f'{user_lines[-1]!r} - eine Namensangabe verschleiert, welche uid der '
        'Container tatsaechlich benutzt')


def test_compose_setzt_gepinnte_uid_und_dokumentiert_den_chown():
    """Blocker 2, Gegenstueck im Compose-File.

    `user:` muss dieselbe uid:gid erzwingen wie das Dockerfile, und der
    einmalige chown fuer bestehende Installationen muss dokumentiert sein -
    ohne ihn laeuft eine Bestandsinstallation nach dem Update in die
    Neustartschleife.
    """
    compose = (PROJECT_ROOT / 'docker-compose.yml').read_text(encoding='utf-8')

    user_zeilen = [l.strip() for l in compose.splitlines()
                   if l.strip().startswith('user:')]
    assert user_zeilen == ['user: "1000:1000"'], (
        f'docker-compose.yml setzt kein passendes `user:` - gefunden: {user_zeilen}')

    assert 'chown -R 1000:1000 ./data' in compose, (
        'Der einmalige chown fuer bestehende ./data-Verzeichnisse ist nicht '
        'dokumentiert')
