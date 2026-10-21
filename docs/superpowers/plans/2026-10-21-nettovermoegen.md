# Nettovermögen im Profil Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jede Person pflegt im Profil private Konten (Vermögen/Schuld) mit datierten Salden und sieht Nettovermögen samt Verlauf.

**Architecture:** Zwei neue Tabellen (`wealth_accounts`, `wealth_balances`) in `init_db()`, eine reine Berechnungsfunktion `_wealth_data`, zwei JSON-Endpunkte nach dem Muster von `save_recurring`, und eine Karte plus Kennzahl in `profile.html`. Eigentümer ist immer `g.current_user['person_id']`, nie ein Client-Wert.

**Tech Stack:** Flask, SQLite/SQLCipher, Jinja2, Bootstrap 5.3.3, Chart.js 4.5.1 (im Profil bereits geladen), pytest.

**Spec:** [docs/superpowers/specs/2026-10-21-nettovermoegen-design.md](../specs/2026-10-21-nettovermoegen-design.md)

## Vorbedingungen

- Arbeiten auf einem Branch `nettovermoegen` (von `main`), Abschluss mit `superpowers:finishing-a-development-branch`.
- `app/translations.py` und `app/templates/month.html` müssen im Arbeitsbaum **sauber** sein (die uncommitteten Umbuchen-Änderungen vorher separat committen). Sonst landen fremde Hunks in den Commits dieses Plans.
- Tests: `./.venv-dev/bin/python -m pytest -q` (Ausgangsstand 355 grün). Jeder Commit nennt seine Pfade explizit, nie `git add -A`.

## Global Constraints

- Nie lesen oder schreiben: `data/`, `data_/`. Manuelle Prüfung nur gegen eine Wegwerf-Datenbank über `DATABASE_PATH` im Scratchpad (`dev_server.py`, `run_dev.sh` dort vorhanden, Port 5050).
- Kein `git stash`, kein `git checkout --`, kein Entfernen eines bestehenden Schutzes zum "Rot-Nachweis".
- Eigentümer serverseitig an jedem Endpunkt: `person_id` stammt aus `g.current_user['person_id']`, steht nie in einer `SET`-Klausel, Zugriffe auf fremde Zeilen sind still wirkungslos. Hat das Konto keine Person: 400 mit `t('srv.account_not_linked')`.
- Kein `innerHTML` mit Nutzerstrings, keine Template-Literale mit Namen (Regel K1). Nur `textContent`, `value`, `new Option()`.
- Jedes neue Inline-Skript trägt `nonce="{{ csp_nonce() }}"` (das bestehende Profil-Skript tut es bereits).
- Jeder neue Übersetzungsschlüssel steht in `de` **und** `en` von `app/translations.py`.
- Tabellen und Spalten heißen englisch (wie `expenses`), UI-Texte deutsch. Typen genau: `checking, savings, fixed_deposit, securities, life_insurance, pension, real_estate, vehicle, cash, other_asset` (Vermögen) und `loan, mortgage, credit_card, overdraft, other_debt` (Schuld).
- Commit-Nachrichten deutsch, Ende: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Eingaben, die die Spec nicht nennt und die am ehesten Schaden anrichten (der Test steht jeweils beim Task, der den Code besitzt):

1. **Nicht numerischer Saldo** (`"abc"`, `null`, `true`, Liste) darf nicht zu 500 führen, sondern zu 400 ohne Schreibzugriff. → Task 4.
2. **Ungültige `account_id`/`id`** (String, `null`, fehlend) darf nicht zu 500 führen, die Zeile wird übersprungen. → Task 3 und 4.
3. **Namen mit Markup oder `${...}`** werden wörtlich gespeichert und im Profil maskiert ausgegeben, nie in einem Template-Literal. → Task 5.
4. **Stichtag heute ist erlaubt, morgen nicht** (Grenzfall, Server-Datum). → Task 4.
5. **Profil mit null Konten und mit genau einem Stichtag** rendert ohne Fehler (Kennzahl 0,00 €, kein Diagrammabsturz). → Task 5.

---

### Task 1: Schema, Typen, Backup-Positivliste

**Files:**
- Modify: `app/routes/main.py` (`init_db()` nach `presence` ~Zeile 855; Konstante `WEALTH_TYPES` auf Modulebene; `_EIGENE_TABELLEN` ~Zeile 3680)
- Modify: `tests/conftest.py:28` (`_CLEANUP_TABLES`)
- Create: `tests/test_wealth.py`

**Interfaces:**
- Produces: `WEALTH_TYPES: dict[str, str]` in `app.routes.main`, Schlüssel = Typ, Wert = `'asset'` oder `'debt'` (15 Einträge laut Global Constraints). Tabellen laut Spec 3 (Spalten `wealth_accounts(id, person_id, name, type)`, `wealth_balances(account_id, balance_date, balance)`).

- [ ] **Step 1: Failing tests in `tests/test_wealth.py`**

Fixture `person` (legt 'Alice' an und verknüpft sie mit `admin`, wie in `tests/test_save_expenses.py`), Helfer `_konto(db, person_id, name, typ) -> id` und `_saldo(db, konto_id, datum, betrag)` (direkte `INSERT`s, `db.commit()`). Tests:
  - `test_tabellen_existieren`: beide Namen stehen in `sqlite_master`.
  - `test_wealth_types_zuordnung`: `len(WEALTH_TYPES) == 15` und `sorted(k for k, v in WEALTH_TYPES.items() if v == 'debt') == ['credit_card', 'loan', 'mortgage', 'other_debt', 'overdraft']`.
  - `test_person_loeschen_loescht_konten_und_salden`: Konto plus Saldo anlegen, `DELETE FROM persons WHERE id=?`, danach beide Tabellen leer.
  - `test_konto_loeschen_loescht_seine_salden`: ein Konto mit zwei Salden löschen, `wealth_balances` leer.

- [ ] **Step 2:** `pytest tests/test_wealth.py -v` → FAIL (Tabellen/`WEALTH_TYPES` fehlen).

- [ ] **Step 3: Implementieren.** `WEALTH_TYPES` als Konstante; in `init_db()` die zwei `CREATE TABLE IF NOT EXISTS` exakt wie in Spec 3; `'wealth_accounts'` und `'wealth_balances'` in `_EIGENE_TABELLEN`; beide Namen vor `'persons'` in `_CLEANUP_TABLES`.

- [ ] **Step 4:** `pytest tests/test_wealth.py tests/test_backup_restore_sicherheit.py -q` → PASS (insbesondere `test_erlaubte_objekte_bleiben_mit_init_db_im_gleichlauf`).

- [ ] **Step 5: Commit** `git add app/routes/main.py tests/conftest.py tests/test_wealth.py` — "Vermögen: Tabellen, Typen und Backup-Positivliste".

### Task 2: Berechnung `_wealth_data`

**Files:**
- Modify: `app/routes/main.py` (neben `_overview_daten`)
- Test: `tests/test_wealth.py`

**Interfaces:**
- Consumes: `WEALTH_TYPES`, Tabellen aus Task 1.
- Produces: `_wealth_data(conn, person_id: int) -> dict` mit den Schlüsseln aus Spec 4: `accounts` (je `id, name, type, kind, balance, balance_date`, sortiert nach `id`), `total_assets`, `total_debts`, `net_worth`, `series` (je `date, assets, debts, net_worth`, aufsteigend), `entries` (je `account_id, balance_date, balance`, absteigend nach Datum). `kind` ist `'asset'` oder `'debt'`. Summen auf 2 Nachkommastellen gerundet. Konto ohne Eintrag: `balance 0.0`, `balance_date None`.

- [ ] **Step 1: Failing tests** (Helfer aus Task 1, `conn = db`):
  - `test_aktueller_saldo_juengster_eintrag`: Girokonto, `2026-01-31`→1000, `2026-03-31`→1500 ⇒ `accounts[0]['balance'] == 1500` und `balance_date == '2026-03-31'`.
  - `test_netto_mit_schulden`: Giro 1000, Hypothek 800 ⇒ `total_assets == 1000`, `total_debts == 800`, `net_worth == 200`.
  - `test_verlauf`: Giro `01-01`→1000, `03-01`→1500; Kredit `02-01`→400 ⇒ Datums-Liste `['2026-01-01','2026-02-01','2026-03-01']`, `net_worth`-Reihe `[1000, 600, 1100]`, `assets` `[1000, 1000, 1500]`, `debts` `[0, 400, 400]`.
  - `test_konto_ohne_eintrag_zaehlt_null`: Konto ohne Salden ⇒ `balance == 0.0`, `balance_date is None`, `series == []`.
  - `test_leer`: Person ohne Konten ⇒ alle Summen 0, `accounts == series == entries == []`.
  - `test_nur_eigene_person`: Konto einer zweiten Person taucht nicht auf.
  - `test_rundung`: Salden 0.1 und 0.2 (zwei Vermögenskonten) ⇒ `total_assets == 0.3`.

- [ ] **Step 2:** → FAIL (`_wealth_data` fehlt).
- [ ] **Step 3: Implementieren.** Ein Lauf über alle Einträge der Person, aufsteigend sortiert; je Stichtag den "letzten Stand bis dahin" pro Konto mitführen (laufendes Dict statt Neuabfrage pro Datum).
- [ ] **Step 4:** `pytest tests/test_wealth.py -q` → PASS.
- [ ] **Step 5: Commit** `git add app/routes/main.py tests/test_wealth.py` — "Vermögen: Berechnung von Nettovermögen und Verlauf".

### Task 3: Endpunkt `POST /api/wealth/accounts/save`

**Files:**
- Modify: `app/routes/main.py` (nach `save_recurring`), `app/translations.py` (keine neuen Schlüssel nötig, nutzt `srv.stale_client` und `srv.account_not_linked`)
- Test: `tests/test_wealth.py`

**Interfaces:**
- Consumes: `_require_v2(d)`, `WEALTH_TYPES`, `_wealth_data`.
- Produces: Route `save_wealth_accounts()` auf `/api/wealth/accounts/save`, `@login_required`. Eingabe `{v:2, rows:[{id|client_key, name, type}], deleted_ids:[int]}`. Antwort `{success: True, id_map: {client_key: id}, accounts: <_wealth_data(...)['accounts']>}`. Alte Payload ohne `v:2` ⇒ 409, Konto ohne Person ⇒ 400. Kein `_bump_activity` (private Daten, Spec 5.2).

- [ ] **Step 1: Failing tests** (Hilfsfunktion `_post_konten(client, csrf, **payload)` wie `_save_recur`):
  - `test_konten_anlegen_person_aus_session`: Zeile mit `client_key 'a'`, `person_id: <fremde Person>` ⇒ `id_map['a']` gesetzt, gespeicherte `person_id == person` (die eigene).
  - `test_konten_aendern_behaelt_person`: Name und Typ ändern ⇒ übernommen, `person_id` unverändert.
  - `test_fremdes_konto_unantastbar`: Konto einer zweiten Person direkt per DB anlegen; Änderung und `deleted_ids` per API ⇒ Zeile unverändert und noch vorhanden.
  - `test_ungueltige_zeilen_uebersprungen`: im selben Payload eine gültige Zeile plus Zeilen mit Typ `'xyz'`, leerem Namen, Name aus 61 Zeichen und `id: 'abc'` ⇒ nur die gültige gespeichert, Status 200.
  - `test_konto_ohne_person_400` (Admin ohne Verknüpfung, ohne `person`-Fixture) und `test_alte_payload_409`.
  - `test_name_wird_woertlich_gespeichert`: Name `<img src=x onerror=alert(1)>${fetch(1)}` ⇒ in der DB identisch.

- [ ] **Step 2:** → FAIL (404).
- [ ] **Step 3: Minimalversion ohne Eigentümer-Filter** (Anlegen mit eigener Person, `UPDATE`/`DELETE` nur über `id`), Validierung nach Spec 5.1 (`strip()`, 1 bis 60 Zeichen, Typ in `WEALTH_TYPES`). Ungültige Zeilen überspringen.
- [ ] **Step 4:** `pytest tests/test_wealth.py -q` → genau `test_fremdes_konto_unantastbar` ist **rot** (echter Assertion-Fehler, kein 404). Das ist der Rot-Nachweis.
- [ ] **Step 5: Eigentümer-Schutz ergänzen:** `UPDATE ... WHERE id=? AND person_id=?`, `DELETE ... WHERE id=? AND person_id=?`; `person_id` nie in `SET`.
- [ ] **Step 6:** `pytest tests/test_wealth.py -q` → PASS.
- [ ] **Step 7: Commit** `git add app/routes/main.py tests/test_wealth.py` — "Vermögen: Endpunkt zum Speichern der Konten".

### Task 4: Endpunkt `POST /api/wealth/balances/save`

**Files:**
- Modify: `app/routes/main.py` (nach Task 3), `app/translations.py` (`srv.wealth_invalid_date`, `srv.wealth_invalid_balance`, je `de` und `en`)
- Test: `tests/test_wealth.py`

**Interfaces:**
- Consumes: `_require_v2`, `_wealth_data`.
- Produces: Route `save_wealth_balances()` auf `/api/wealth/balances/save`. Eingabe `{v:2, date, entries:[{account_id, balance}], deleted:[{account_id, balance_date}]}`. Antwort `{success: True, wealth: <_wealth_data(...)>}`. Die **gesamte** Nutzlast wird vor dem Schreiben validiert (Datum nur nötig, wenn `entries` nicht leer ist, jeder `balance` endlich und unter 1e12 im Betrag und kein `bool`); ein Fehler ⇒ 400 und nichts geschrieben. Einträge für fremde oder nicht ganzzahlige `account_id` werden still übersprungen, ebenso `deleted`-Paare fremder Konten.

- [ ] **Step 1: Failing tests** (`_post_salden(client, csrf, **payload)`; Konten per DB der eigenen Person):
  - `test_saldo_upsert_gleicher_tag_ueberschreibt`: zweimal 2026-10-01 mit 100, dann 150 ⇒ eine Zeile mit 150.
  - `test_saldo_negativ_bei_vermoegen_erlaubt`: `-20.5` bei Girokonto gespeichert.
  - `test_saldo_fremdes_konto_still_uebersprungen` und `test_saldo_loeschen_nur_eigene`: fremdes Konto per DB; Eintrag und Löschpaar per API ⇒ fremde Salden unverändert, Status 200.
  - `test_ungueltiges_datum_400_nichts_geschrieben`: Daten `'2026-13-40'`, `'21.10.2026'`, `''` mit zusätzlich gültigem Eintrag im selben Payload ⇒ 400, `wealth_balances` leer.
  - `test_heute_erlaubt_morgen_abgewiesen`: Datum heute (`datetime.now()`, Server-Datum) ⇒ 200, morgen ⇒ 400.
  - `test_nan_infinity_zu_gross_400`: Rohtext-Body mit `NaN`, `Infinity` und `1e12` (per `data=` mit `content_type='application/json'`) ⇒ jeweils 400, nichts geschrieben.
  - `test_saldo_nicht_numerisch_400`: `"abc"`, `null`, `true`, `[]` ⇒ 400, kein 500.
  - `test_account_id_ungueltig_uebersprungen`: `"x"`, `null`, fehlender Schlüssel ⇒ 200, nichts geschrieben.
  - `test_antwort_enthaelt_vermoegensdaten`: `wealth.net_worth` stimmt mit dem erwarteten Wert überein.
  - `test_ohne_person_400`, `test_alte_payload_409`.

- [ ] **Step 2:** → FAIL (404).
- [ ] **Step 3: Minimalversion ohne Eigentümer-Prüfung** (Upsert mit `INSERT ... ON CONFLICT(account_id, balance_date) DO UPDATE`, Löschen über das Paar), Validierung wie oben.
- [ ] **Step 4:** `pytest tests/test_wealth.py -q` → genau `test_saldo_fremdes_konto_still_uebersprungen` und `test_saldo_loeschen_nur_eigene` sind **rot** (echter Assertion-Fehler).
- [ ] **Step 5: Eigentümer-Prüfung ergänzen:** Einträge nur für Konten der eigenen Person (vorher `SELECT id FROM wealth_accounts WHERE person_id=?`), `deleted` mit Join auf `wealth_accounts.person_id`.
- [ ] **Step 6:** `pytest tests/test_wealth.py -q` → PASS.
- [ ] **Step 7: Commit** `git add app/routes/main.py app/translations.py tests/test_wealth.py` — "Vermögen: Endpunkt zum Speichern der Salden".

### Task 5: Profil serverseitig (Kennzahl, Karte, Texte)

**Files:**
- Modify: `app/routes/main.py` (`profile()` ~Zeile 3047: `wealth=_wealth_data(conn, person_id)`, `wealth_types=WEALTH_TYPES`), `app/templates/profile.html` (Kennzahlen-Reihe Zeilen 20 bis 43: vier Karten `col-md-3`, neue Karte nach dem Geldbörse-Block), `app/translations.py`
- Test: `tests/test_wealth.py`

**Interfaces:**
- Consumes: `_wealth_data`, `WEALTH_TYPES`.
- Produces: IDs für Task 6: `#statNetWorth`, `#wealthCard`, `#wealthAccountsList` (Zeilen `.wealth-row` mit `data-id`), `#wealthDate`, `#wealthHistory`, `#wealthChart`, Buttons `#wealthAddAccountBtn`, `#wealthSaveAccountsBtn`, `#wealthSaveBalancesBtn`. Die Kennzahl nutzt `'%.2f'|format(...)`, positiv `text-success`, negativ `text-danger`. Übersetzungsschlüssel: `profile.stat_net_worth`, `profile.stat_assets`, `profile.stat_debts`, `wealth.title`, `wealth.group_assets`, `wealth.group_debts`, `wealth.add_account`, `wealth.save_accounts`, `wealth.account_name_placeholder`, `wealth.date_label`, `wealth.save_balances`, `wealth.history_title`, `wealth.history_empty`, `wealth.chart_title`, `wealth.no_accounts`, `wealth.toast_accounts_saved`, `wealth.toast_balances_saved`, dazu `wealth.type.<Typ>` für alle 15 Typen (Bezeichnungen aus Spec 3.1).

- [ ] **Step 1: Failing tests:**
  - `test_uebersetzungsschluessel_in_beiden_sprachen`: feste Liste oben plus `f'wealth.type.{k}' for k in WEALTH_TYPES` in `TRANSLATIONS['de']` und `['en']`, plus `srv.wealth_invalid_date` und `srv.wealth_invalid_balance`.
  - `test_profil_zeigt_nettovermoegen`: Giro 1000, Hypothek 800 ⇒ `#statNetWorth` enthält `200.00 €` mit Klasse `text-success`; bei Hypothek 1500 ⇒ `-500.00 €` mit `text-danger`.
  - `test_profil_ohne_konten_rendert` (Review Focus 5): Status 200, `0.00 €`; zusätzlich mit genau einem Stichtag Status 200.
  - `test_fremde_konten_nicht_im_profil`: Kontoname der zweiten Person steht nicht im HTML.
  - `test_namen_maskiert_und_nicht_im_template_literal` (Review Focus 3): Kontoname `<img src=x onerror=1>${fetch(2)}` ⇒ der Rohstring `<img src=x onerror=1>` steht nicht im HTML, `&lt;img` schon, und kein Backtick-Literal enthält `${fetch(2)}` (Hilfsfunktion `_template_literale` aus `tests/test_xss_namen.py` wiederverwenden).
  - `test_unverknuepft_keine_karte`: Admin ohne Person ⇒ `id="wealthCard"` fehlt.

- [ ] **Step 2:** → FAIL.
- [ ] **Step 3: Implementieren.** Route, vier Kennzahlen-Karten (`col-md-3`), Karte "Mein Vermögen" mit zwei Gruppen (Konten als Zeilen mit Namensfeld per `value`, Typ-Select gruppiert nach Art, Saldofeld mit aktuellem Saldo, Lösch-Button), Datumsfeld `#wealthDate`, aufklappbare Verlaufsliste, Canvas `#wealthChart`; alle Ausgaben über Jinja-Autoescaping. Übersetzungen in `de` und `en`.
- [ ] **Step 4:** `pytest -q` → alles PASS (355 plus neue; `test_csp_nonce.py` bleibt grün).
- [ ] **Step 5: Commit** `git add app/routes/main.py app/templates/profile.html app/translations.py tests/test_wealth.py` — "Vermögen: Kennzahl und Karte im Profil".

### Task 6: Interaktion, Diagramm, Browser-Prüfung

**Files:**
- Modify: `app/templates/profile.html` (im bestehenden `nonce`-Skript), `README.md` (Funktionsliste: Bullet "Vermögen")
- Test: `tests/test_wealth.py`

**Interfaces:**
- Consumes: IDs und Endpunkte aus Task 3 bis 5. Protokoll: Konten `{v:2, rows, deleted_ids}` mit `client_key`; Salden `{v:2, date, entries, deleted}`. Das CSRF-Token ergänzt der globale Fetch-Interceptor in `base.html` automatisch (kein eigener Header nötig, wie im bestehenden Profil-Code); Meldungen laufen über `showToast(msg, type)` aus `base.html` mit `t('wealth.toast_...')`.

- [ ] **Step 1: Failing tests (statisch, Seitenquelltext):** `test_profil_enthaelt_vermoegens_skript`: HTML enthält `wealthAddAccountBtn`, `/api/wealth/accounts/save`, `/api/wealth/balances/save`, `new Chart(document.getElementById('wealthChart')`; `WEALTH_SERIES` ist per `tojson` ausgegeben (`json.loads` des Literals ergibt die erwartete Reihe).
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3: Implementieren (nur DOM-API):** Konto hinzufügen (neue Zeile mit `client_key`), Konten speichern (`rows`, `deleted_ids`, nach Erfolg `location.reload()`), Salden speichern (nur Zeilen, deren Wert vom Ausgangswert abweicht, zum Datum aus `#wealthDate`, Standard heute lokal, `max` = heute), Verlaufseinträge löschen (`deleted`-Paar, danach Reload), Löschen eines Kontos mit `confirm()`, Liniendiagramm mit drei Datensätzen (Nettovermögen kräftig, Vermögen und Schulden dünn); ohne Serie wird der Canvas ausgeblendet. Erfolg und Fehler über `showToast`.
- [ ] **Step 4:** `pytest -q` → PASS.
- [ ] **Step 5: Browser-Prüfung** gegen frische Wegwerf-Datenbank (Dev-Server aus dem Scratchpad, Admin mit `must_change_password=0` per `sqlite3` verknüpft, zweite Person mit eigenem Konto): Konten anlegen/ändern/löschen, Salden an zwei Stichtagen, Kennzahl und Diagramm stimmen, Verlaufseintrag löschen, die zweite Person sieht nichts von der ersten, Konsole ohne Fehler, Seite ohne Konten rendert. `data/` und `data_/` unberührt.
- [ ] **Step 6: Commit** `git add app/templates/profile.html README.md tests/test_wealth.py` — "Vermögen: Eingabe, Verlauf und Diagramm im Profil".
