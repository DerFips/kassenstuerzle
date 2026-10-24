# Design: Demo-Umgebung

**Datum:** 2026-10-24
**Status:** Entwurf abgestimmt, Spec zur Durchsicht
**Umfang:** Eine Umsetzungseinheit (ein Plan).

---

## 1 Ziel

Eine öffentlich zeigbare Demo von Kassenstürzle: zwei Benutzer mit sechs Monaten plausibler Beispieldaten, die sich **täglich um 4:00 Uhr** selbst zurücksetzt und danach wieder mit der Demo-Datenbank startet. Neue Funktionen kommen per `git pull` und neu gebautem Image, das darf von Hand passieren. Die echte Installation bleibt unberührt.

## 2 Getroffene Entscheidungen

| ID | Entscheidung | Begründung |
|---|---|---|
| D1 | **Eingeschränkte Rechte** (vom Nutzer gewählt): Kernfunktionen frei, gesperrt nur, was das System gefährdet oder die Demo für andere zerstört | Besucher sollen ausprobieren können, ohne Zugänge zu ändern, Dateien einzuspielen oder Backups zu ziehen. |
| D2 | **Reset durch Selbstbeendigung des Containers.** Ein Job der App beendet den Server täglich um 4:00. `restart: unless-stopped` startet den Container neu, beim Start entstehen die Demo-Daten frisch | Alles in einer Compose-Datei, kein Cron auf dem Host nötig. Verworfen: Cron auf dem Host (muss eingerichtet werden), Zusatz-Container (mehr Wartung). |
| D3 | **Daten nur im Arbeitsspeicher (`tmpfs`)** | Nichts bleibt über den Reset hinaus liegen: keine Uploads, Sitzungen, Schlüssel oder Reste. |
| D4 | **Startdaten werden bei jedem Start relativ zum heutigen Datum erzeugt** | Nach jedem Reset ist "die letzten sechs Monate" wieder aktuell. |
| D5 | **Eigenes Compose-Projekt ohne `.env` und ohne `./data`** | Die echte Installation und ihre Geheimnisse sind für die Demo nicht erreichbar, beide können nebeneinander laufen. |
| D6 | **Zugangsdaten stehen öffentlich auf der Anmeldeseite** | Die Demo ist zum Zeigen da, niemand soll raten müssen. |

## 3 Demo-Modus

Aktiv bei `DEMO_MODE=1` (nur in der Demo-Compose gesetzt). `create_app()` liest die Variable einmal in `app.config['DEMO_MODE']`; alle Prüfungen lesen den Konfigurationswert zur Laufzeit (so lässt er sich im Test umschalten).

### 3.1 Sperr-Wächter

Ein `before_request`-Wächter in `app/routes/main.py`, **nach** `csrf_protect`. Ist der Demo-Modus aktiv und der aufgerufene Endpunkt gesperrt, wird die Anfrage abgewiesen, bevor die eigentliche Funktion läuft.

**Gesperrte Endpunkte** (Blueprint `main`):

| Gruppe | Endpunkte |
|---|---|
| Passwörter | `change_password`, `force_password_change` |
| Benutzer | `api_users_create`, `api_users_update`, `api_users_delete`, `api_users_reset_password` (die Liste `api_users_list` bleibt lesbar) |
| Einrichtungsassistent | `api_setup_profiles_self`, `api_setup_profiles_complete` (er könnte einen Benutzer umbenennen und die Zugänge zerstören) |
| Backups | `api_backup_create`, `api_backup_list`, `api_backup_download`, `api_backup_restore`, `api_backup_delete` |
| Uploads und Importe | `import_csv`, `api_parse_pdf`, `api_commit_pdf`, `api_receipt_parse`, `api_receipt_upload`, `api_receipt_delete` |
| Push | `register_notification`, `unregister_notification` |

**Teilweise gesperrt:** `save_persons` verweigert im Demo-Modus jede Nutzlast, die Personen **anlegt** (Zeile ohne `id`) oder **löscht** (`deleted_ids` nicht leer). Änderungen an bestehenden Personen, etwa die eigene Geldbörse im Profil, bleiben erlaubt.

**Antwort bei gesperrtem Zugriff:**
- Pfad beginnt mit `/api/` → HTTP 403 mit `{success: false, error: t('srv.demo_disabled')}`.
- Sonst (Formular-Beitrag oder Seitenaufruf) → HTTP 403 mit einer kleinen Seite `demo_gesperrt.html` (erbt von `base.html`): die Meldung als Hinweiskasten und ein Link zurück zu den Einstellungen. (Ein Flash mit Weiterleitung taugt hier nicht, weil keine Seite außer der Anmeldung Flash-Meldungen anzeigt.)
- Text `srv.demo_disabled`: "Im Demo-Modus nicht verfügbar." / "Not available in demo mode." (de und en).

**Nicht gesperrt** (bleibt voll nutzbar): Ausgaben, Einnahmen, Umbuchen, Kategorien, wiederkehrende Vorlagen, Vermögen, Monatsabrechnung, Profil, Übersicht, CSV-Export, Kalender-Export, Sprachwechsel, die Seite `/import/pdf` selbst (der Upload daraus ist gesperrt und zeigt die Meldung).

### 3.2 Banner und Zugangsdaten

- Ein Kontextwert `demo_mode` steht allen Templates zur Verfügung. `base.html` zeigt oben auf jeder Seite einen Hinweis: "Demo-Umgebung – die Daten werden täglich um 4:00 Uhr zurückgesetzt." (Uhrzeit aus der Konfiguration).
- Die Anmeldeseite (erbt von `base.html`) zeigt im Demo-Modus unter dem Formular einen Kasten **"So meldest du dich an"** mit einer kurzen Anleitung:
  - Schritte: als Benutzername `anna` oder `ben` eingeben, als Passwort das Demo-Passwort (aus `DEMO_PASSWORD`, steht im Text), auf "Anmelden" klicken.
  - Beide Zugänge mit ihrer Rolle: **anna** (Administrator, sieht in den Einstellungen zusätzlich die Personen- und Benutzerübersicht) und **ben** (Mitglied).
  - Ein Tipp: sich nacheinander mit beiden anzumelden, denn Profil, wiederkehrende Vorlagen und Nettovermögen sind für jede Person getrennt.
  - Ohne Demo-Modus erscheint der Kasten nicht.
- Neue Übersetzungsschlüssel (alle in `de` und `en`): `srv.demo_disabled`, `demo.banner`, `demo.login_title`, `demo.login_steps` (mit den Platzhaltern für Passwort), `demo.login_anna`, `demo.login_ben`, `demo.login_tip`.

### 3.3 Täglicher Reset

- Im Demo-Modus wird statt des Push-Erinnerungs-Jobs ein Job `demo_reset` im bestehenden `BackgroundScheduler` eingetragen: `cron`, `hour=DEMO_RESET_HOUR` (Standard `4`), `minute=DEMO_RESET_MINUTE` (Standard `0`), Zeitzone aus `TZ` (Compose setzt `Europe/Berlin`). `DISABLE_SCHEDULER=1` schaltet ihn wie alle Jobs ab.
- Der Job ruft `_demo_reset()` auf: Läuft der Prozess im Container (`KASSENSTUERZLE_IM_CONTAINER=1`, steht im Dockerfile), sendet er `SIGTERM` an den Gunicorn-Master (`os.getppid()`). Der Container endet, Docker startet ihn neu. **Außerhalb eines Containers tut die Funktion nichts außer einer Warnung ins Log**, damit sie nie die Shell eines Entwicklers beendet.
- `DEMO_RESET_HOUR` und `DEMO_RESET_MINUTE` gibt es, damit sich der Reset im Test auf wenige Minuten später stellen lässt. Im Betrieb bleiben die Standardwerte.
- Folgen des Resets: etwa 10 Sekunden Ausfall, alle Besucher werden abgemeldet, alle Eingaben der Besucher verschwinden.

## 4 Startdaten (`app/demo.py`)

`python -m app.demo` läuft bei jedem Container-Start vor Gunicorn. Ablauf: `DISABLE_SCHEDULER=1` setzen, vorhandene DB-Dateien am `DATABASE_PATH` entfernen, `create_app()` aufrufen (legt Schema und den Bootstrap-Admin an), danach `seed_demo(conn, today=None, password=None)`.

`seed_demo` ist eine eigene Funktion mit Verbindung, Stichtag und Passwort als Parameter, damit sie testbar ist. Der Bootstrap-Admin wird zu **Anna** umgewidmet (Benutzername `anna`, Rolle `admin`, `must_change_password=0`); **Ben** wird als Mitglied ergänzt. Jede Person ist mit ihrem Konto verknüpft (1:1). Das Passwort kommt aus `DEMO_PASSWORD` (Standard `demo1234`). In `app_meta` wird `profiles_setup_done=1` gesetzt, damit der Einrichtungsassistent nicht erscheint.

### 4.1 Zeitraum und Plausibilität

- Zeitraum: die **fünf vollen Monate vor dem aktuellen Monat plus der aktuelle Monat bis einschließlich heute**, also sechs Kalendermonate. **Kein Datum liegt nach `today`.**
- Die Daten entstehen mit einem **festen Zufallsstart** (`random.Random(42)`): nach jedem Reset sehen sie gleich plausibel aus.
- Beträge bleiben im Alltagsrahmen: keine einzelne Ausgabe über 1.200 € (die Miete mit 1.180 € ist die größte), kein einzelner Betrag einer Einnahme über 3.100 €. Mindestens vier Ausgaben pro Person und Monat. Das Haushaltsbudget bleibt im Rahmen von ca. 3.300 bis 3.900 € Ausgaben bei ca. 5.700 € Einnahmen pro Monat (inklusive der einmaligen Posten aus 4.2).

### 4.2 Inhalt

- **Kategorien** (mit Farbe). Geteilt (Split): Miete (55/45), Lebensmittel (50/50), Strom und Internet (50/50). Privat: Restaurant, Mobilität, Freizeit, Versicherungen, Gesundheit, Kleidung. Einnahmen-Kategorien: Gehalt, Nebenverdienst, Erstattung.
- **Ausgaben:** Miete 1.180 € am 1. (Anna zahlt), Strom 78 bis 92 €, Internet 39,99 €, Handy (Anna 24,99 €, Ben 19,99 €), Streaming 12,99 €, Lebensmittel mit acht bis zehn Einkäufen pro Monat zwischen 25 und 115 €, Drogerie, Restaurant zwei- bis dreimal 22 bis 68 €, Deutschlandticket (Ben 49 €), Tanken und Kfz-Versicherung, Fitnessstudio (Anna 29,90 €), Versicherungen, Apotheke 8 bis 25 €, gelegentlich Kleidung 35 bis 120 €. Zwei einmalige Posten in verschiedenen Monaten: eine Reise (ca. 640 €) und ein Möbelstück (ca. 420 €).
- **Einnahmen:** Gehalt Anna ca. 3.050 € und Ben ca. 2.680 € am 1. (das Gehalt steht am Monatsanfang, damit der aktuelle Monat nie ein Minus durch ein noch fehlendes Gehalt zeigt), gelegentlich Nebenverdienst von Ben (120 bis 280 €), einmalig eine Erstattung (ca. 310 €).
- **Rückbuchung:** eine negative Ausgabe ("Retoure", ca. -25 €), damit sich das Umbuchen vorführen lässt.
- **Abrechnung:** Alle Monate **außer den letzten beiden** stehen in `month_status` als abgerechnet (`settled=1`, mit `settled_at`), der aktuelle und der vorherige sind offen.
- **Wiederkehrende Vorlagen:** drei bis fünf pro Person (z. B. Miete bei Anna, Deutschlandticket bei Ben, Streaming).
- **Vermögen (nur eigene Person, wie in der echten App):**
  - Anna: Girokonto, Tagesgeld, Aktiendepot / ETF, Altersvorsorge, Kreditkarte (Schuld).
  - Ben: Girokonto, Tagesgeld, Aktiendepot, Studienkredit (Schuld, sinkend um etwa 250 € pro Monat).
  - Je Konto ein Saldo zu jedem Monatsende des Zeitraums plus ein Stand für heute (nie in der Zukunft). Schulden als positive Beträge, Salden wachsen plausibel und nicht sprunghaft.
- **Geldbörse:** nicht belegt.

## 5 Betrieb (`docker-compose.demo.yml`)

```yaml
name: kassenstuerzle-demo
services:
  web:
    build: .
    command: ["sh", "-c", "python -m app.demo && exec gunicorn --config gunicorn.conf.py run:app"]
    ports: ["127.0.0.1:${DEMO_PORT:-5001}:5000"]
    user: "1000:1000"
    tmpfs: ["/app/data:uid=1000,gid=1000,mode=0700,size=64m"]
    restart: unless-stopped
    environment:   # keine env_file, keine Bind-Mounts
      DEMO_MODE: "1"
      ALLOW_UNENCRYPTED: "1"
      DEMO_PASSWORD: ${DEMO_PASSWORD:-demo1234}
      ADMIN_PASSWORD: ${DEMO_PASSWORD:-demo1234}
      HTTPS: ${DEMO_HTTPS:-1}
      TZ: Europe/Berlin
      GUNICORN_WORKERS: "1"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 512m
    pids_limit: 200
```

- `exec gunicorn` macht Gunicorn zum Hauptprozess (PID 1), damit `SIGTERM` an den Master den Container sauber beendet.
- `ADMIN_PASSWORD` verhindert, dass beim Anlegen des Bootstrap-Admins ein Zufallspasswort ins Log geschrieben wird.
- `ALLOW_UNENCRYPTED=1`, weil die Demo keine echten Daten enthält.
- `HTTPS` steht standardmäßig auf `1` (Cookie mit Secure-Flag): Die Demo gehört hinter einen Reverse-Proxy mit TLS. Zum reinen Ausprobieren im lokalen Netz lässt sich `DEMO_HTTPS=0` setzen.
- Der Standardport bindet nur an `127.0.0.1`. Reverse-Proxy, Domain und Zertifikat richtet der Betreiber ein, das ist nicht Teil dieser Umsetzung.

### 5.1 Aktualisieren

`scripts/demo-update.sh` (ausführbar):

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose -f docker-compose.demo.yml up -d --build
```

Der Container wird dabei neu erstellt und startet mit frischen Demo-Daten. Dasselbe geht von Hand mit den beiden Befehlen.

### 5.2 Dokumentation

- README: Abschnitt "Demo-Umgebung" (Starten, Zugänge, was gesperrt ist, Reset, Aktualisieren, Reverse-Proxy-Hinweis).
- `CHANGELOG.md`: Eintrag unter einem neuen Abschnitt für die nächste Version.

## 6 Tests

Neue Testdatei `tests/test_demo.py`:

- **Startdaten** (ohne die gemeinsame Test-Datenbank zu verschmutzen, als Unterprozess mit eigenem `DATABASE_PATH`): zwei Konten (`anna` Admin, `ben` Mitglied, je verknüpft), `profiles_setup_done=1`, Passwort stimmt; alle Daten im Zeitraum, **keines nach heute**; Beträge im Plausibilitätsrahmen aus 4.1; mindestens vier Ausgaben je Person und Monat; Splits und Kategorien vorhanden; genau die richtigen Monate abgerechnet; Vermögensstände nie in der Zukunft, Schulden positiv; mit einem festen `today` ist das Ergebnis reproduzierbar; zweimaliges Anlegen führt zum gleichen Ergebnis; `calculate_settlement`, `_wealth_data` und `_overview_daten` laufen über den Demo-Daten ohne Fehler.
- **Sperr-Wächter** (über `app.config['DEMO_MODE']` umgeschaltet): jeder gesperrte Endpunkt liefert 403 mit der Demo-Meldung (API) bzw. die Sperrseite mit Status 403 (Formular und Seitenaufruf); `save_persons` verweigert Anlegen und Löschen, erlaubt aber das Ändern der Geldbörse; erlaubte Funktionen (Ausgaben speichern, Vermögen, Umbuchen) funktionieren; **ohne Demo-Modus ist nichts gesperrt**.
- **Reset-Job:** im Demo-Modus mit Scheduler ist `demo_reset` bei 4:00 eingetragen (und per `DEMO_RESET_HOUR`/`DEMO_RESET_MINUTE` änderbar), ohne Demo-Modus nicht; `_demo_reset()` sendet im Container `SIGTERM` an den Elternprozess (`os.kill` ersetzt) und tut außerhalb des Containers nichts.
- **Oberfläche:** Banner und der Anmelde-Kasten (Benutzernamen `anna` und `ben`, das Passwort, die Rollen und die Schritte) erscheinen nur im Demo-Modus; alle neuen Texte in `de` und `en`.
- **Compose-Datei:** `docker compose -f docker-compose.demo.yml config` läuft ohne Fehler, enthält weder `env_file` noch einen Bind-Mount.

**Manuelle Prüfung** (lokal mit Docker): Image bauen, Demo starten, anmelden als Anna und Ben, Daten und Abrechnung ansehen, eine gesperrte Aktion auslösen (Meldung kommt), eine Buchung eintragen. Reset testen: Uhrzeit auf zwei Minuten später stellen, warten, prüfen, dass der Container neu gestartet ist, die Eingabe verschwunden ist und die Demo-Daten wieder da sind. Die echte `data/` wird nie angefasst.

## 7 Bewusst nicht Teil dieser Umsetzung

- Reverse-Proxy, Domain und TLS-Zertifikat.
- Getrennte Datensätze pro Besucher: alle Besucher teilen sich dieselben zwei Zugänge und sehen die Eingaben der anderen bis zum Reset.
- Ein "Jetzt zurücksetzen"-Knopf. Der Reset läuft nur zeitgesteuert (oder von Hand per `docker compose restart`).
- Ein vollständiger Schutz vor Missbrauch (Ratenbegrenzung gegen Massenzugriffe, Überwachung). Es gelten die vorhandenen Rate-Limits und die Container-Grenzen.
- Lokalisierte Demo-Texte jenseits von Deutsch und Englisch.
