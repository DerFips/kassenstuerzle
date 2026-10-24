# Demo-Umgebung Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine öffentlich zeigbare Demo mit zwei Benutzern und sechs Monaten Beispieldaten, die sich täglich um 4:00 Uhr selbst zurücksetzt.

**Architecture:** Ein Demo-Modus (`DEMO_MODE=1`) sperrt per `before_request`-Wächter riskante Endpunkte und blendet Banner und Anmelde-Anleitung ein. Ein Job im vorhandenen Scheduler beendet den Container um 4:00, Docker startet ihn neu, beim Start erzeugt `python -m app.demo` die Startdaten frisch (Datenverzeichnis liegt im `tmpfs`). Eine eigene Compose-Datei hält die Demo von der echten Installation getrennt.

**Tech Stack:** Flask, SQLite, APScheduler, Gunicorn, Docker Compose, pytest.

**Spec:** [docs/superpowers/specs/2026-10-24-demo-umgebung-design.md](../specs/2026-10-24-demo-umgebung-design.md)

## Vorbedingungen

- Arbeiten auf einem Branch `demo-umgebung` (von `main`), Abschluss mit `superpowers:finishing-a-development-branch`.
- Tests: `./.venv-dev/bin/python -m pytest -q` (Ausgangsstand 449 grün). Commits nennen ihre Pfade explizit, nie `git add -A`. Der Arbeitsbaum muss zu Beginn sauber sein.

## Global Constraints

- Nie lesen oder schreiben: `data/`, `data_/`. Manuelle Läufe nur mit eigenem `DATABASE_PATH` im Scratchpad bzw. im Container-`tmpfs`.
- Kein `git stash`, kein `git checkout --`.
- Demo-Modus nur über `app.config['DEMO_MODE']` (aus `DEMO_MODE=1`), zur Laufzeit gelesen: `create_app()` setzt ihn aus der Umgebung, Tests schalten ihn um. Ohne Demo-Modus ändert sich am Verhalten der App **nichts**.
- Sperr-Antwort: Pfad beginnt mit `/api/` → HTTP 403 `{success: false, error: t('srv.demo_disabled')}`; sonst `flash(t('srv.demo_disabled'), 'warning')` und Weiterleitung zu `main.settings`.
- Jeder neue Übersetzungsschlüssel steht in `de` **und** `en` von `app/translations.py`. Kein `innerHTML` mit Nutzer- oder Konfigurationstexten; Jinja-Autoescaping genügt.
- Reset-Standard: `DEMO_RESET_HOUR=4`, `DEMO_RESET_MINUTE=0`; `DISABLE_SCHEDULER=1` schaltet auch den Reset-Job ab.
- Startdaten: sechs Kalendermonate (fünf volle Monate vor dem aktuellen plus der aktuelle bis einschließlich `today`), **kein Datum nach `today`**, `random.Random(42)`, keine Ausgabe über 1.200 € (größte: Miete 1.180 €), keine einzelne Einnahme über 3.100 €, mindestens vier Ausgaben je Person und Monat, ca. 3.300 bis 3.900 € Ausgaben bei ca. 5.700 € Einnahmen pro Monat.
- Commit-Nachrichten deutsch, Ende: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Der Reset darf nie außerhalb eines Containers etwas beenden**: ohne `KASSENSTUERZLE_IM_CONTAINER=1` nur eine Log-Warnung, kein `os.kill`. → Task 2.
2. **Ohne Demo-Modus ist nichts gesperrt**, jeder gesperrte Endpunkt verhält sich wie vorher. → Task 1.
3. **Startdaten an Monats-, Jahres- und Schaltjahresgrenzen** (`today` = 1. des Monats, 31.01., 28./29.02., Jahreswechsel): keine Zukunftsdaten, kein Absturz, sechs Monate stimmen. → Task 3.
4. **Ein Passwort mit Markup** in `DEMO_PASSWORD` wird auf der Anmeldeseite maskiert ausgegeben. → Task 1.
5. **Zweites Anlegen über vorhandene Dateien** (Neustart ohne leeres `tmpfs`) führt zum selben Ergebnis, keine doppelten Daten. → Task 3.

---

### Task 1: Demo-Modus, Sperr-Wächter, Banner und Anmelde-Anleitung

**Files:**
- Modify: `app/__init__.py` (in `create_app()`: `app.config['DEMO_MODE']`, `DEMO_PASSWORD`, `DEMO_RESET_HOUR`, `DEMO_RESET_MINUTE`; Kontextwerte im `inject_i18n`-Bereich ~Zeile 387)
- Modify: `app/routes/main.py` (Konstante `DEMO_GESPERRT`, Wächter nach `csrf_protect` ~Zeile 259)
- Modify: `app/templates/base.html` (Banner direkt nach `<body>`), `app/templates/login.html` (Kasten unter dem Formular), `app/translations.py`
- Create: `tests/test_demo.py`

**Interfaces:**
- Produces: `app.config['DEMO_MODE']: bool`, `app.config['DEMO_PASSWORD']: str` (Standard `demo1234`), `app.config['DEMO_RESET_HOUR']: int`, `app.config['DEMO_RESET_MINUTE']: int`; Template-Kontext `demo_mode: bool`, `demo_password: str`, `demo_reset_time: str` (`'04:00'`); `DEMO_GESPERRT: frozenset[str]` (volle Endpunktnamen `main.<name>`) und `demo_guard()` in `app/routes/main.py`.

**Gesperrt** (genau diese Endpunkte): `main.change_password`, `main.force_password_change`, `main.api_users_create`, `main.api_users_update`, `main.api_users_delete`, `main.api_users_reset_password`, `main.api_setup_profiles_self`, `main.api_setup_profiles_complete`, `main.api_backup_create`, `main.api_backup_list`, `main.api_backup_download`, `main.api_backup_restore`, `main.api_backup_delete`, `main.import_csv`, `main.api_parse_pdf`, `main.api_commit_pdf`, `main.api_receipt_parse`, `main.api_receipt_upload`, `main.api_receipt_delete`, `main.register_notification`, `main.unregister_notification`. **Teilweise:** `main.save_persons` verweigert Nutzlasten mit einer Zeile ohne `id` oder mit nicht leerem `deleted_ids`; Änderungen bestehender Personen (Geldbörse) bleiben erlaubt.

- [ ] **Step 1: Failing tests in `tests/test_demo.py`.** Fixtures: `demo_modus(app)` (setzt `app.config['DEMO_MODE']=True`, `DEMO_PASSWORD='demo1234'` und stellt beides zurück), `person` (Alice, mit `admin` verknüpft, wie in `tests/test_save_expenses.py`). Tests:
  - `test_gesperrte_endpunkte_antworten_mit_demo_meldung` (parametrisiert über alle 21 Endpunkte mit Methode und Beispiel-URL, z. B. `('post','/api/users')`, `('delete','/api/users/1')`, `('get','/api/backup/download/x.db')`, `('delete','/api/receipt/x.jpg')`; mutierende Aufrufe mit `X-CSRF-Token`): `/api/*` ⇒ 403 und `error == 'Im Demo-Modus nicht verfügbar.'`.
  - `test_formular_und_seite_leiten_mit_hinweis_um`: `POST /change_password` und `GET /password/aendern` ⇒ 302 nach `/settings`, der Hinweis steht im Flash der Folgeseite.
  - `test_persons_anlegen_und_loeschen_gesperrt_aendern_erlaubt`: Zeile ohne `id` ⇒ 403; `deleted_ids=[1]` ⇒ 403; Zeile mit `id` der eigenen Person und `wallet_start_amount` ⇒ 200.
  - `test_kernfunktionen_bleiben_nutzbar`: `/api/expenses/save` (v2, eine Zeile), `/api/wealth/accounts/save`, `GET /api/users` ⇒ jeweils 200.
  - `test_ohne_demo_modus_ist_nichts_gesperrt` (Review Focus 2): bei `DEMO_MODE=False` liefert `GET /api/backup/list` als Admin 200, und `POST /change_password` mit falschem aktuellem Passwort leitet mit der **üblichen** Fehlermeldung um: die Folgeseite `/settings` enthält `Demo-Modus` nicht.
  - `test_banner_nur_im_demo_modus`: `/overview` enthält `id="demo-banner"` und `04:00` nur mit Demo-Modus.
  - `test_anmelde_kasten_nur_im_demo_modus`: `GET /login` (ohne Anmeldung) enthält mit Demo-Modus `id="demo-login-box"`, `anna`, `ben`, das Passwort `demo1234`, die Rollenhinweise; ohne Demo-Modus nichts davon.
  - `test_demo_passwort_wird_maskiert` (Review Focus 4): `DEMO_PASSWORD='<b>x</b>'` ⇒ die Seite enthält `&lt;b&gt;x&lt;/b&gt;` und nicht `<b>x</b>`.
  - `test_demo_texte_in_beiden_sprachen`: `srv.demo_disabled`, `demo.banner`, `demo.login_title`, `demo.login_steps`, `demo.login_anna`, `demo.login_ben`, `demo.login_tip` in `de` und `en`.

- [ ] **Step 2:** `pytest tests/test_demo.py -q` ⇒ FAIL.
- [ ] **Step 3: Implementieren.** `create_app()` liest die vier Werte aus der Umgebung (`DEMO_MODE=='1'`, `DEMO_PASSWORD` mit Standard, Stunde und Minute als int) in `app.config` und liefert sie über eine `context_processor` als `demo_mode`, `demo_password`, `demo_reset_time` (`HH:MM`). `demo_guard()` als `@main_bp.before_request`, registriert **nach** `csrf_protect`; sie prüft `current_app.config.get('DEMO_MODE')` bei jedem Aufruf. Banner und Kasten mit `{% if demo_mode %}`; Texte über `t('demo.…')` (`demo.login_steps` bekommt das Passwort als Platzhalter, die Ausgabe bleibt autoescaped, nicht `|safe`). Texte (de): Banner "Demo-Umgebung – die Daten werden täglich um {time} Uhr zurückgesetzt.", Titel "So meldest du dich an", Schritte "Gib als Benutzername anna oder ben ein, als Passwort {password}, und klicke auf „Anmelden“.", `demo.login_anna` "anna – Administrator (sieht in den Einstellungen zusätzlich die Personen- und Benutzerübersicht)", `demo.login_ben` "ben – Mitglied", `demo.login_tip` "Tipp: Melde dich nacheinander mit beiden an, denn Profil, wiederkehrende Vorlagen und Nettovermögen sind für jede Person getrennt." Englische Entsprechungen sinngemäß.
- [ ] **Step 4:** `pytest -q` ⇒ alles PASS (`tests/test_csp_nonce.py` bleibt grün, im Banner kein Inline-Skript).
- [ ] **Step 5: Commit** `git add app/__init__.py app/routes/main.py app/templates/base.html app/templates/login.html app/translations.py tests/test_demo.py` — "Demo-Modus: Sperr-Wächter, Banner und Anmelde-Anleitung".

### Task 2: Täglicher Reset-Job

**Files:**
- Create: `app/demo.py`
- Modify: `app/__init__.py` (Scheduler-Block ~Zeile 413: im Demo-Modus `schedule_demo_reset(scheduler)` statt des Push-Erinnerungs-Jobs)
- Test: `tests/test_demo.py`

**Interfaces:**
- Consumes: `app.config['DEMO_RESET_HOUR']`, `app.config['DEMO_RESET_MINUTE']` aus Task 1.
- Produces: `demo_reset() -> None` und `schedule_demo_reset(scheduler, hour: int, minute: int) -> None` in `app/demo.py`. Der Job heißt `demo_reset`, Trigger `cron` mit genau diesen Werten.

- [ ] **Step 1: Failing tests:**
  - `test_reset_job_steht_standardmaessig_auf_vier_uhr`: `BackgroundScheduler()` (nicht gestartet), `schedule_demo_reset(s, 4, 0)` ⇒ `s.get_job('demo_reset')` existiert, `hour`-Feld `4`, `minute`-Feld `0`; mit `(6, 30)` entsprechend.
  - `test_reset_beendet_im_container_den_gunicorn_master`: `KASSENSTUERZLE_IM_CONTAINER=1` (monkeypatch), `os.getppid` ⇒ `4242`, `os.kill` aufgezeichnet ⇒ genau ein Aufruf `(4242, signal.SIGTERM)`.
  - `test_reset_tut_ausserhalb_des_containers_nichts` (Review Focus 1): ohne die Variable (monkeypatch.delenv) ⇒ `os.kill` nie aufgerufen, ein Warn-Eintrag im Log (`caplog`).
  - `test_create_app_im_demo_modus_traegt_reset_statt_erinnerung_ein`: neue App über `create_app()` mit `DEMO_MODE=1`, `DISABLE_SCHEDULER` entfernt (monkeypatch) ⇒ im `app.extensions['scheduler']` gibt es `demo_reset` und **kein** `push_reminders`; ohne `DEMO_MODE` umgekehrt; der Scheduler wird im `finally` mit `shutdown(wait=False)` beendet.
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3: Implementieren.** `demo_reset()` loggt und sendet `SIGTERM` an `os.getppid()` nur bei `os.environ.get('KASSENSTUERZLE_IM_CONTAINER') == '1'` (bewusst nicht über `/.dockerenv`, damit ein Dev-Container nie getroffen wird), sonst nur eine Warnung. `schedule_demo_reset` nutzt `scheduler.add_job(demo_reset, 'cron', hour=…, minute=…, id='demo_reset', replace_existing=True)`. In `create_app()` den Zweig im Scheduler-Block verzweigen.
- [ ] **Step 4:** `pytest -q` ⇒ PASS.
- [ ] **Step 5: Commit** `git add app/demo.py app/__init__.py tests/test_demo.py` — "Demo-Modus: täglicher Reset um 4:00 durch Neustart des Containers".

### Task 3: Startdaten (`python -m app.demo`)

**Files:**
- Modify: `app/demo.py`
- Test: `tests/test_demo_seed.py` (neu)

**Interfaces:**
- Produces: `seed_demo(conn, today: date | None = None, password: str | None = None) -> dict` (Zusammenfassung mit den Schlüsseln `persons`, `users`, `expenses`, `income`, `months_settled`, `accounts`, `balances`, jeweils Anzahlen) und `main() -> None` (CLI, `python -m app.demo`).

**Verhalten** (Spec 4): `main()` setzt `DISABLE_SCHEDULER=1`, löscht vorhandene Dateien am `DATABASE_PATH` (`.db`, `-wal`, `-shm`), ruft `create_app()` auf und danach `seed_demo` in einem App-Kontext. `seed_demo` widmet den Bootstrap-Admin zu `anna` um (Rolle `admin`, `must_change_password=0`, Passwort aus `password` oder `DEMO_PASSWORD`, Standard `demo1234`), legt `ben` als Mitglied an (je 1:1 mit Person verknüpft) und setzt `app_meta('profiles_setup_done','1')`. Inhalt und Spannen laut Spec 4.2: Kategorien (Miete 55/45, Lebensmittel, Strom, Internet geteilt; Restaurant, Mobilität, Freizeit, Versicherungen, Gesundheit, Kleidung privat; Einnahmen-Kategorien Gehalt, Nebenverdienst, Erstattung), Ausgaben, Einnahmen, eine Rückbuchung (negative Ausgabe ca. -25 €), alle Monate außer den letzten beiden in `month_status` abgerechnet, drei bis fünf wiederkehrende Vorlagen je Person, Vermögenskonten je Person mit Salden zu jedem Monatsende des Zeitraums und einem Stand für `today` (Schulden positiv), Geldbörse leer.

**Test-Weg:** `seed_demo` braucht ein echtes Schema und läuft deshalb **im Unterprozess** mit eigenem `DATABASE_PATH` (Muster aus `tests/test_backup_restore_sicherheit.py::test_erlaubte_objekte_bleiben_mit_init_db_im_gleichlauf`): ein kleines Skript ruft `create_app()`, dann `seed_demo(conn, today=<fest>)`, rechnet `calculate_settlement`, `_wealth_data`, `_overview_daten` und gibt das Ergebnis als JSON aus; die Tests lesen die erzeugte Datenbank danach mit `sqlite3`.

- [ ] **Step 1: Failing tests in `tests/test_demo_seed.py`** (Hilfsfunktion `_seed(tmp_path, today, zweimal=False)` startet den Unterprozess und liefert `(db_path, ausgabe)`):
  - `test_zwei_konten_mit_rollen_und_verknuepfung`: Benutzer `anna` (Rolle `admin`) und `ben` (Rolle `member`), je `person_id` einer eigenen Person, `must_change_password=0`, `profiles_setup_done='1'`, Anmeldung mit dem Passwort möglich (`check_password_hash`).
  - `test_zeitraum_sechs_monate_ohne_zukunft` mit `today=2026-10-24`: kleinstes Datum in `expenses` und `income` liegt im Mai 2026 (fünf volle Monate Mai bis September plus Oktober), **kein** Datum nach `today` (Ausgaben, Einnahmen, Salden).
  - `test_betraege_im_plausiblen_rahmen`: jede Ausgabe `<= 1200`, genau eine Ausgabe ist negativ (die Rückbuchung, zwischen -50 und 0), jede Einnahme `0 < x <= 3100`, die Summe der Ausgaben je voll abgeschlossenem Monat liegt zwischen 3.000 und 4.200 €, die der Einnahmen zwischen 5.000 und 6.500 €; mindestens vier Ausgaben je Person und Monat.
  - `test_kategorien_splits_und_abrechnung`: die vier geteilten Kategorien haben `is_split=1` und Splits; `month_status` hat genau die Monate älter als die letzten beiden als abgerechnet; `calculate_settlement` und `_overview_daten` laufen fehlerfrei.
  - `test_vermoegen_je_person`: jede Person hat mindestens 3 Konten mit je mindestens 6 Salden, nie in der Zukunft; Schulden sind positiv; `_wealth_data(...)['net_worth']` ist für beide `> 0` und `series` hat mindestens 6 Punkte.
  - `test_grenzdaten` (Review Focus 3): `today` ∈ {2026-03-01, 2026-01-31, 2026-02-28, 2028-02-29, 2027-01-01} ⇒ kein Absturz, kein Datum nach `today`, früheste Daten im richtigen Monat (jeweils sechs Kalendermonate, auch über den Jahreswechsel).
  - `test_zweimal_anlegen_ist_wiederholbar` (Review Focus 5): `main()` zweimal hintereinander gegen dasselbe `DATABASE_PATH` (zweiter Lauf über vorhandene Dateien) ⇒ gleiche Zusammenfassung, genau zwei Benutzer und keine doppelten Buchungen.
  - `test_reproduzierbar`: gleiches `today`, zweimal ⇒ identische Buchungslisten.
- [ ] **Step 2:** `pytest tests/test_demo_seed.py -q` ⇒ FAIL.
- [ ] **Step 3: Implementieren** (`seed_demo` und `main`, Beträge und Verteilung nach Spec 4.1/4.2 mit `random.Random(42)`; Monatsgrenzen über Hilfsfunktionen, die auch `today` am Monatsersten und am 29.02. beherrschen).
- [ ] **Step 4:** `pytest -q` ⇒ PASS.
- [ ] **Step 5: Commit** `git add app/demo.py tests/test_demo_seed.py` — "Demo-Modus: Startdaten für zwei Benutzer über sechs Monate".

### Task 4: Compose-Datei, Update-Skript, Dokumentation

**Files:**
- Create: `docker-compose.demo.yml`, `scripts/demo-update.sh` (ausführbar)
- Modify: `README.md` (Abschnitt "Demo-Umgebung"), `CHANGELOG.md` (neuer Abschnitt `## [Unreleased]` mit der Demo-Umgebung)
- Test: `tests/test_demo.py`

**Interfaces:**
- Consumes: `python -m app.demo` (Task 3), `DEMO_MODE`, `DEMO_RESET_HOUR`, `DEMO_RESET_MINUTE` (Tasks 1 und 2).

- [ ] **Step 1: Failing tests:**
  - `test_compose_demo_ohne_env_file_und_bind_mount`: Textprüfung von `docker-compose.demo.yml`: enthält `name: kassenstuerzle-demo`, `DEMO_MODE`, `tmpfs`, `/app/data`, `cap_drop`, `no-new-privileges`, `exec gunicorn`, `python -m app.demo`, `restart: unless-stopped`; enthält **weder** `env_file` noch `./data` noch `.env`.
  - `test_compose_demo_ist_gueltig`: `docker compose -f docker-compose.demo.yml config` Exit 0 (übersprungen, wenn `docker` fehlt).
  - `test_update_skript_ist_ausfuehrbar_und_strikt`: Datei ausführbar, enthält `set -euo pipefail`, `git pull --ff-only` und `docker compose -f docker-compose.demo.yml up -d --build`.
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3: Implementieren** `docker-compose.demo.yml` genau nach Spec 5 (zusätzlich `DEMO_RESET_HOUR: ${DEMO_RESET_HOUR:-4}` und `DEMO_RESET_MINUTE: ${DEMO_RESET_MINUTE:-0}` in `environment`), `scripts/demo-update.sh` nach Spec 5.1, README-Abschnitt (Starten, Zugänge, Sperren, Reset, Aktualisieren, Reverse-Proxy-Hinweis, `DEMO_HTTPS=0` nur zum Ausprobieren) und Changelog-Eintrag.
- [ ] **Step 4:** `pytest -q` ⇒ PASS.
- [ ] **Step 5: Manuelle Prüfung (Controller, nicht der Implementer; echter Container, `data/` bleibt unberührt):** Image bauen und Demo starten (`DEMO_HTTPS=0`), Anmeldeseite mit Anleitung ansehen, als `anna` und `ben` anmelden, Daten, Abrechnung und Vermögen prüfen, eine gesperrte Aktion auslösen (Meldung), eine Buchung eintragen. Reset testen: Container mit `DEMO_RESET_MINUTE` zwei Minuten später neu erstellen, warten, prüfen, dass der Container neu gestartet ist, die Buchung weg ist und die Demo-Daten zurück sind; danach `docker compose … down`.
- [ ] **Step 6: Commit** `git add docker-compose.demo.yml scripts/demo-update.sh README.md CHANGELOG.md tests/test_demo.py` — "Demo-Umgebung: Compose-Datei, Update-Skript und Dokumentation".
