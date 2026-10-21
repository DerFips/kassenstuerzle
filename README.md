# Kassenstürzle

Haushaltskasse für mehrere Personen: Ausgaben und Einnahmen pro Monat erfassen, gemeinsame Kosten fair aufteilen und am Monatsende abrechnen. Selbst gehostet (Flask + SQLite/SQLCipher), als Docker-Container betreibbar, auf dem Handy als PWA installierbar. Oberfläche auf Deutsch und Englisch.

## Funktionen

- **Monatsansicht**: Ausgaben und Einnahmen in Tabellen mit Autosave, Formeln in Betragsfeldern (`=12,50*3`), Filter nach Person und Kategorie, Bargeld-Kennzeichnung, Beleg-Fotos.
- **Kategorien mit Aufteilung**: Eine Kategorie kann als "Split" markiert werden, optional mit eigenem Verhältnis (z. B. 60/40 bei der Miete).
- **Abrechnung**: Pro Monat wird berechnet, wer wem wie viel schuldet. Eingerechnet werden Ausgaben und Einnahmen in Split-Kategorien sowie "Bezahlt für"-Buchungen. Einnahmen in Nicht-Split-Kategorien zählen nicht zur Abrechnung, nur zur Ersparnis. Abgerechnete Monate lassen sich abschließen und wieder öffnen.
- **Übersicht und Auswertung**: Diagramme, Summen pro Person und Jahr, Status der Monatsabrechnungen.
- **Profil**: Jede Person hat eine eigene Seite mit ihren Kategorien, der Geldbörse (Bargeld-Startmonat und -betrag), der Monats-Matrix und ihren **wiederkehrenden Ausgaben**.
- **Vermögen**: Jede Person führt im Profil private Konten für Vermögen und Schulden mit datierten Salden. Daraus entstehen das Nettovermögen und sein Verlauf als Diagramm. Sichtbar nur für die eigene Person, auch für Admins.
- **Wiederkehrende Ausgaben**: Monatlich, quartalsweise oder jährlich. Jede Person verwaltet nur ihre eigenen Vorlagen, auch Admins. Im Monat fügt "Wiederkehrende" die eigenen Vorlagen ein.
- **Import und Export**: Kontoauszug als PDF (DKB, Sparkasse, ING und weitere), CSV-Import und -Export, Beleg-Scan per OCR (Tesseract).
- **Benutzer**: Rollen Admin und Mitglied. Jede Person hat genau ein Konto und umgekehrt. Das "Wer"-Feld einer Buchung ist immer die eigene Person und serverseitig gesperrt, "Für wen" bleibt frei wählbar.
- **Sicherung**: Backup-Manager (erstellen, herunterladen, wiederherstellen), automatische Sicherung vor Migrationen.
- **Erinnerungen**: Push-Benachrichtigungen und iCal-Export offener Abrechnungen.

## Schnellstart mit Docker

Voraussetzungen: Docker mit Compose-Plugin.

1. `.env` im Projektverzeichnis anlegen (siehe [Konfiguration](#konfiguration)). Mindestens `DB_ENCRYPTION_KEY` setzen.
2. Unter Linux dem Container Schreibrechte auf das Datenverzeichnis geben (einmalig):

   ```bash
   mkdir -p data && sudo chown -R 1000:1000 ./data
   ```

3. Starten:

   ```bash
   docker compose up -d --build
   ```

4. Anmelden als `admin`. Das Startpasswort ist `ADMIN_PASSWORD` aus der `.env`. Ist es nicht gesetzt, wird ein Einmalpasswort ins Container-Log geschrieben (`docker compose logs`). Beim ersten Login erzwingt die App einen Passwortwechsel.
5. Unter **Einstellungen** den Einrichtungsassistenten durchlaufen: eigenes Konto einer Person zuordnen, weitere Personen und Kategorien anlegen.

### Erreichbarkeit

Der Port ist in der [docker-compose.yml](docker-compose.yml) bewusst nur an `127.0.0.1:5000` gebunden. Ein TLS-Reverse-Proxy (Caddy, nginx) auf demselben Host erreicht die App darüber. Das ist die empfohlene Betriebsart, dann `HTTPS=1` in der `.env` setzen.

Für den reinen Heimnetz-Betrieb ohne Proxy die Port-Zeile auf die LAN-IP des Servers ändern, z. B. `"192.168.1.40:5000:5000"`, und `HTTPS=0` lassen. Passwort und Sitzungscookie gehen dann unverschlüsselt über das Netz. Mit `HTTPS=1` funktioniert die Anmeldung über reines http nicht, weil das Cookie mit Secure-Flag nicht gesendet wird.

Zum schnellen Testen ohne Änderung genügt ein SSH-Tunnel:

```bash
ssh -L 5000:127.0.0.1:5000 benutzer@server
```

Danach ist die App unter `http://localhost:5000` erreichbar.

## Konfiguration

Alle Einstellungen kommen aus Umgebungsvariablen, im Container aus der `.env`. Die `.env` ist in `.gitignore` und darf nie eingecheckt werden.

| Variable | Standard | Bedeutung |
|---|---|---|
| `DB_ENCRYPTION_KEY` | – | Schlüssel für die SQLCipher-Verschlüsselung der Datenbank. **Ohne ihn startet die App nicht**, außer `ALLOW_UNENCRYPTED=1` ist gesetzt. Schlüssel sicher aufbewahren: ohne ihn sind Datenbank und Backups nicht lesbar. |
| `ALLOW_UNENCRYPTED` | – | `1` erlaubt bewusst den Betrieb ohne Verschlüsselung. |
| `ADMIN_PASSWORD` | zufällig, im Log | Einmalpasswort des ersten Admin-Kontos (nur beim allerersten Start). |
| `DATABASE_PATH` | `/app/data/kassenstuerzle.db` | Pfad der Datenbank. Backups, Belege und Schlüsseldateien liegen im selben Verzeichnis. |
| `HTTPS` | `1` im Image | `1` setzt das Secure-Flag am Sitzungscookie. Nur `0`, wenn bewusst ohne TLS gearbeitet wird. |
| `SESSION_LIFETIME_HOURS` | `12` | Sitzungsdauer bis zur automatischen Abmeldung. |
| `SECRET_KEY` | automatisch erzeugt | Flask-Schlüssel. Ohne Angabe wird er in `data/.secret_key` abgelegt. |
| `GUNICORN_BIND` | `0.0.0.0:5000` | Bind-Adresse im Container. |
| `GUNICORN_WORKERS` / `GUNICORN_THREADS` | `1` / `4` | Bei mehr als einem Worker muss Flask-Limiter auf ein Redis-Backend umgestellt werden. |
| `GUNICORN_TIMEOUT` | `120` | Zeitlimit pro Anfrage, OCR und PDF-Import können dauern. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxys, denen `X-Forwarded-*`-Header vertraut wird. |
| `RECEIPTS_MAX_TOTAL_MB` | `500` | Obergrenze für das Beleg-Verzeichnis. |
| `PUSH_REMINDER_HOUR` | `9` | Stunde des täglichen Erinnerungs-Checks. |
| `VAPID_SUBJECT` | `mailto:admin@localhost` | Kontaktadresse für Web-Push. |
| `LOG_LEVEL` / `LOG_FILE` | `info` / – | Protokollstufe und optionale Logdatei mit Rotation. |
| `DISABLE_SCHEDULER`, `DISABLE_RATE_LIMIT` | – | `1` schaltet Erinnerungs-Scheduler bzw. Rate-Limits ab (für Tests und lokale Entwicklung). |

Eine neue Schlüsselfolge für `DB_ENCRYPTION_KEY` lässt sich zum Beispiel so erzeugen:

```bash
openssl rand -hex 32
```

## Daten und Sicherung

Alle Nutzdaten liegen im gemounteten Verzeichnis `./data`: Datenbank, Backups, Belege, `.secret_key` und der VAPID-Schlüssel für Push. Dieses Verzeichnis und die `.env` (mit dem `DB_ENCRYPTION_KEY`) sind zusammen zu sichern. Backups lassen sich in der App unter **Einstellungen → Backup Manager** erstellen, herunterladen und wiederherstellen. Vor jeder Schema-Migration legt die App automatisch eine Sicherungskopie an.

Beim Update einer bestehenden Installation die neue Version holen und den Container neu bauen. Migrationen laufen beim Start automatisch:

```bash
git pull && docker compose up -d --build
```

## Entwicklung

Voraussetzungen: Python 3.11, für OCR und PDF-Import zusätzlich `tesseract-ocr` mit deutschem Sprachpaket (`tesseract-ocr-deu`).

```bash
python3 -m venv .venv-dev
.venv-dev/bin/pip install -r requirements-dev.txt
```

Lokal starten (Entwicklungsserver auf `127.0.0.1:5000`, Daten in `./data`):

```bash
.venv-dev/bin/python run.py
```

Tests ausführen:

```bash
make test
```

Die Tests laufen gegen eine eigene Wegwerf-Datenbank, nie gegen `data/`. Zum manuellen Ausprobieren ebenfalls eine Wegwerf-Datenbank verwenden, damit die echten Daten unberührt bleiben:

```bash
DATABASE_PATH=/tmp/kassenstuerzle-test/kassenstuerzle.db ALLOW_UNENCRYPTED=1 .venv-dev/bin/python run.py
```

Die vollständig gepinnte `requirements.lock` (mit Hashes) wird mit `make lock` erzeugt, braucht Netzzugang und wird vom Dockerfile bevorzugt, sobald sie existiert.

### Projektstruktur

```
app/
  __init__.py        App-Factory, Sicherheits-Header, Rate-Limits, Scheduler
  routes/main.py     Routen, Berechnungen (Abrechnung, Übersicht), Migrationen
  translations.py    Texte Deutsch/Englisch (jeder Schlüssel in beiden Sprachen)
  templates/         Jinja2-Templates
  static/            PWA-Manifest, Service Worker, Icons
gunicorn.conf.py     Server-Konfiguration
tests/               Regressionstests (pytest)
docs/                Sicherheitsprüfung, Specs und Pläne
Dockerfile           Image (Python 3.11, Tesseract, Non-Root-Nutzer uid 1000)
docker-compose.yml   Dienst, Port-Bindung, Daten-Mount
```

## Sicherheit

- Datenbank mit SQLCipher verschlüsselt, Start ohne Schlüssel wird verweigert.
- Rollenprüfungen laufen serverseitig an jedem Endpunkt, nicht nur in der Oberfläche.
- Content-Security-Policy mit Nonce pro Anfrage, ohne `unsafe-inline` bei Skripten. Jedes Inline-Skript braucht `nonce="{{ csp_nonce() }}"`.
- CSRF-Token für die JSON-APIs und SameSite=Strict am Sitzungscookie, Rate-Limit und Sperre bei wiederholten Fehlversuchen am Login.
- Container läuft als Nicht-Root-Nutzer, der Port ist standardmäßig nur lokal gebunden.
