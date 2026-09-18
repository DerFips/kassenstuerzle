# Design: Benutzerprofile & Nebenläufigkeit

**Datum:** 2026-09-18
**Status:** Design abgestimmt, Umsetzung offen
**Umfang:** Zwei getrennt umsetzbare Phasen. Jede Phase erhält ihren eigenen Implementierungsplan.

---

## 1 Ausgangslage

| Thema | Ist-Zustand |
|---|---|
| Login | Eine `users`-Tabelle existiert bereits (`id`, `username`, `password_hash`, `main.py:217`) mit genau einem Datensatz `admin`. Login prüft dagegen (`main.py:655`), Session trägt bereits `user_id` (`main.py:664`). |
| Rollen | Keine. Alle Endpunkte sind nur mit `login_required` geschützt (`main.py:184`) — inklusive Datenbank-Wiederherstellung. |
| Personen | `persons` (`main.py:250`) sind Abrechnungs-Subjekte (wer zahlte, wer schuldet wem, Geldbeutel-Startwert). Keine Verknüpfung zu `users`. |
| Login-Sperre | IP-basiert (`main.py:641`). Im gemeinsamen WLAN sperrt ein Nutzer mit Tippfehlern alle anderen aus. |
| Session | HttpOnly, SameSite=Strict, Secure via `HTTPS`-Env, 12 h Lebensdauer. `session.clear()` + frisches CSRF-Token beim Login — Session-Fixation ist bereits korrekt behandelt. |
| Speichern | Replace-All: Monat wird gelöscht und neu geschrieben (`main.py:783`, `main.py:1034`). Personen/Kategorien löschen alles, was im Payload fehlt (`main.py:820`, `main.py:844`). Daueraufträge werden bedingungslos komplett gelöscht (`main.py:989`). |
| Autosave | Feuert 800 ms nach jeder Änderung und schreibt stets den kompletten Monat zurück. |
| SQLite | Kein WAL, kein `busy_timeout` (`main.py:44`), aber 4 Gunicorn-Threads. |

### 1.1 Bekannte Datenverlust-Szenarien (heute reproduzierbar)

1. **Person verschwindet.** Beide laden die Einstellungen. User 1 legt „Charlie" an und speichert. User 2 speichert seinen älteren Stand → der Server rechnet `{1,2,3} − {1,2} = {3}` und führt `DELETE FROM persons WHERE id=3` aus. Charlie ist weg, ohne Fehlermeldung. Gleiches gilt für Kategorien.
2. **Daueraufträge verschwinden.** `DELETE FROM recurring_expenses` ohne Bedingung — wer mit veraltetem Stand speichert, löscht alle zwischenzeitlich angelegten Aufträge.
3. **Ausgabenzeile verschwindet.** Monat wird gelöscht und neu geschrieben. Die Notbremse (`existing >= 3 und incoming < existing/2`, `main.py:777`) greift nur bei massivem Verlust; 10 von 11 Zeilen passieren sie ungehindert. Durch den Autosave überschreiben sich zwei parallel Arbeitende im Sekundentakt.

---

## 2 Getroffene Entscheidungen

| ID | Entscheidung | Begründung |
|---|---|---|
| E1 | **Modell A:** Login pro Haushaltsmitglied, `users.person_id` verknüpft, **ein gemeinsamer Datenbestand** | Die App ist fachlich auf einen Haushalt ausgelegt (Abrechnung „wer schuldet wem", geteilte Kategorien, gemeinsamer Geldbeutel). Mandantenfähigkeit würde dieses Kernfeature zerstören. |
| E2 | Rollen **`admin` + `member`**, kein `viewer` | Zwei Rollen genügen für einen Haushalt. |
| E3 | Nebenläufigkeit: **nur K3** (Live-Hinweis), kein optimistisches Sperren | Kein Versions-Konflikt-Dialog; stattdessen wird das Datenmodell selbst nicht-destruktiv gemacht (E4). *Abzugrenzen davon:* die 409-Antwort auf Payloads ohne `"v": 2` (A2) ist **kein** Datenkonflikt-Check, sondern eine Client-Versionsprüfung gegen veraltete Browser-Tabs. |
| E4 | **Alles nicht-destruktiv:** Ausgaben/Einnahmen auf Einzelzeilen-Upserts, Personen/Kategorien/Daueraufträge löschen nur auf ausdrückliche Anforderung | Behebt alle drei Szenarien aus 1.1 an der Wurzel statt sie abzufangen. |
| E5 | **Reihenfolge:** erst Nebenläufigkeit (Phase A), dann Profile (Phase B) | Der Datenverlust besteht heute schon, unabhängig von Profilen. |
| E6 | **Alle dürfen alle Buchungen** bearbeiten | Haushaltsbuch, keine Eigentumstrennung an Buchungen. |
| Q2 | Nur Admin: **Benutzerverwaltung + Backup/Restore** | Restore überschreibt die komplette Datenbank. Buchungen, Einstellungen und CSV-/PDF-Import bleiben für alle offen. |
| P1 | Passwort-Übergabe: **Initialpasswort einmalig anzeigen**, Wechsel beim ersten Login erzwingen | Ohne Mailserver der einzige praktikable Weg; für einen Haushalt ausreichend. |

**Bewusst akzeptierte Konsequenz von E3:** Bearbeiten zwei Personen exakt dieselbe Zeile gleichzeitig, gewinnt die letzte Speicherung **dieser einen Zeile**. Der Schaden ist damit auf ein Feld begrenzt statt auf einen ganzen Monat.

---

## 3 Phase A — Nebenläufigkeit

### A1 Datenbank-Härtung

In `_open_db` (`main.py:44`) **nach** dem `PRAGMA key` und vor `PRAGMA foreign_keys` (`main.py:52`):

```sql
PRAGMA journal_mode = WAL;    -- persistent in der DB-Datei
PRAGMA busy_timeout = 5000;   -- pro Verbindung
```

**Wechselwirkung mit Backup/Restore:** WAL erzeugt `-wal`/`-shm`-Dateien neben der Datenbank. `create_backup` nutzt die SQLite-Backup-API und ist damit korrekt. `api_backup_restore` kopiert die Datei jedoch roh über die bestehende DB — eine übrig gebliebene alte `-wal`-Datei kann Teile der wiederhergestellten Datenbank zurückrollen. **Der Restore muss `<db>-wal` und `<db>-shm` mit löschen.**

### A2 Ausgaben/Einnahmen auf Einzelzeilen

**Zeilenidentität herstellen.** `month_view` liefert die `id` bereits mit (`main.py:732`), das Frontend verwirft sie. `addRow()` / `addIncomeRow()` erhalten die `id` als zusätzlichen Parameter und legen sie in `tr.dataset.rowId` ab.

**Request (v2), `POST /api/expenses/save`:**

```json
{
  "v": 2,
  "year": 2026,
  "month": 9,
  "rows": [
    { "id": 12, "day": 1, "description": "Miete", "person_id": 1,
      "category_id": 2, "amount": 750.0, "is_cash": false,
      "paid_for_person_id": null, "receipt_filename": null },
    { "client_key": "c1", "day": 3, "description": "Einkauf", "person_id": 2,
      "category_id": 3, "amount": 41.2, "is_cash": true,
      "paid_for_person_id": null, "receipt_filename": null }
  ],
  "deleted_ids": [17, 18]
}
```

**Response:**

```json
{ "success": true, "id_map": { "c1": 44 }, "activity_version": 7,
  "ov_p": {}, "ov_c": {}, "settlement": {}, "wallet": [],
  "savings_pp": [], "ov_settlements": {} }
```

**Server-Regeln:**

- Zeile mit `id` → `UPDATE ... WHERE id=? AND year=? AND month=?`. Die Jahr/Monat-Bedingung verhindert Manipulation über Monatsgrenzen.
- Zeile ohne `id` → `INSERT`; die neue id wird unter ihrem `client_key` in `id_map` zurückgegeben.
- `deleted_ids` → `DELETE FROM expenses WHERE id=? AND year=? AND month=?`.
- **Betrag geleert:** Zeile *mit* `id` und Betrag 0 → löschen. Zeile *ohne* `id` und Betrag 0 → ignorieren. Damit bleibt das heutige Verhalten erhalten (bisher verschwand eine geleerte Zeile implizit durch `if amt == 0: continue`, `main.py:787`).
- Payload **ohne** `"v": 2` → HTTP 409 mit „Bitte Seite neu laden" (siehe M-Sonderfall „offener Browser-Tab").
- Die Notbremse `save_lock_error` (`main.py:777`, `main.py:1031`) entfällt — sie existierte nur wegen des Replace-All.

**`id_map` ist zwingend.** Ohne Rückmeldung der erzeugten ids würde der Autosave alle 800 ms erneut `INSERT` ausführen und Duplikate anlegen. Das Frontend stempelt die ids sofort in `tr.dataset.rowId`.

**Löschen im Frontend.** Der Zeilen-Mülleimer und „Alle Zeilen leeren" (`clearTable`) müssen die ids vor dem Entfernen aus dem DOM in eine `pendingDeletes`-Liste übernehmen; diese wird beim nächsten Speichern als `deleted_ids` mitgesendet und danach geleert.

`POST /api/income/save` analog (`rows`, `deleted_ids`, `id_map`).

### A3 Einstellungen nicht-destruktiv

Gleiches Schema (`v: 2`, `rows`, `deleted_ids`, `id_map`) **einschließlich der 409-Regel für Payloads ohne `"v": 2`** für die drei folgenden Endpunkte. Zu beachten: `save_persons` und `save_categories` empfangen heute ein reines Array (`data = request.json; for p in data`) — die Anfrage wird also von einem Array zu einem Objekt mit `rows`, was die Versionsmarke zusätzlich nötig macht.

- **`save_persons`** (`main.py:816`): Der Block `for did in existing - incoming: DELETE` (`main.py:820`) entfällt.
- **`save_categories`** (`main.py:840`): Der entsprechende Block (`main.py:844`) entfällt. `splits` bleibt Teil der Zeile.
- **`save_recurring`** (`main.py:987`): `DELETE FROM recurring_expenses` (`main.py:989`) wird durch Upsert-per-id ersetzt. Voraussetzung: das Template muss die vorhandenen ids in die Zeilen schreiben.

Beim Löschen einer Person/Kategorie bleibt es bei `ON DELETE SET NULL` — historische Buchungen überleben wie heute mit leerer Referenz.

### A4 Live-Hinweis (K3)

**Polling, nicht SSE.** Der Sync-Worker hat 4 Threads; eine offene SSE-Verbindung belegt dauerhaft einen davon. Bei zwei bis drei Nutzern wäre der Pool blockiert. Intervall: 10 s.

```sql
CREATE TABLE IF NOT EXISTS activity (
  scope TEXT PRIMARY KEY,
  version INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT,
  updated_by TEXT);

CREATE TABLE IF NOT EXISTS presence (
  user_id INTEGER NOT NULL,
  scope TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  PRIMARY KEY (user_id, scope));
```

- `scope` ist `month:2026-09` bzw. `settings`.
- Jeder schreibende Endpunkt ruft `_bump_activity(conn, scope, username)`.
- `GET /api/activity?scope=month:2026-09` → `{"version": 7, "updated_at": "...", "updated_by": "bob", "others": ["bob"]}` und schreibt gleichzeitig den Heartbeat des aufrufenden Nutzers. `others` = andere Nutzer mit `last_seen` innerhalb der letzten 30 s.
- Frontend: dezentes Badge „Bob ist hier" bei Anwesenheit; Banner „Bob hat diesen Monat geändert — neu laden" wenn `version` gestiegen ist und `updated_by` nicht der eigene Nutzer ist.
- **Kein Auto-Reload**, solange ein Eingabefeld fokussiert ist oder ungespeicherte Änderungen offen sind. Immer nur ein Button.

A4 funktioniert bereits **vor** Phase B, weil `session['username']` existiert.

---

## 4 Phase B — Benutzerprofile (Modell A)

### B1 Schema

Ergänzung der bestehenden, durch `try/except` idempotenten `ALTER TABLE`-Liste (`main.py:298`):

```sql
ALTER TABLE users ADD COLUMN person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'member';
ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN created_at TEXT;
ALTER TABLE users ADD COLUMN last_login TEXT;
ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_person
  ON users(person_id) WHERE person_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT);
```

Der partielle Unique-Index erzwingt die 1:1-Verknüpfung; beide Seiten bleiben optional (Person ohne Login und Login ohne Person sind erlaubt). `app_meta` trägt das Flag `profiles_setup_done` und kann später eine `schema_version` aufnehmen, die dem Projekt heute fehlt.

### B2 Authentifizierung

- **`login_required`** (`main.py:184`) prüft zusätzlich `is_active` und ob `session_epoch` der Session noch dem DB-Wert entspricht (eine zusätzliche Abfrage pro Request).
- **`session_epoch`** ist der Hebel zum serverseitigen Beenden von Sessions: Passwortwechsel, Rollenänderung, Deaktivierung und Umbenennung erhöhen den Wert → bestehende Cookies werden sofort ungültig. Flask-Cookie-Sessions lassen sich anders nicht widerrufen.
- **`admin_required`** als neuer Decorator.
- **Login-Sperre auf Benutzernamen umstellen** (`main.py:641`): Zähler und Sperre werden über `username.lower()` geführt statt über die IP. Eine großzügige IP-Obergrenze bleibt als Rückfallnetz. Die Fehlermeldung bleibt unverändert unspezifisch (`main.py:679`), um Account-Enumeration zu verhindern.
- **`must_change_password`** erzwingt eine Weiterleitung auf die Passwortänderung und blockiert alle anderen Routen.
- `last_login` wird beim erfolgreichen Login gesetzt.

### B3 Rollen und geschützte Endpunkte

| Endpunkt | Zugriff |
|---|---|
| `/api/users*`, Einrichtungsassistent | **admin** |
| `/api/backup/create|list|download|restore|delete` | **admin** |
| Buchungen, Einstellungen, CSV-Import/Export, PDF-Import, Suche, Übersicht | admin + member |

Geprüft wird **serverseitig am Endpunkt**, nicht nur durch Ausblenden im UI.

### B4 Benutzerverwaltung (UI + API)

Neue Karte „Benutzer" in den Einstellungen, nur für Admins gerendert und serverseitig geschützt. Spalten: Benutzername, verknüpfte Person, Rolle, aktiv, letzter Login.

| Route | Zweck |
|---|---|
| `GET /api/users` | Liste |
| `POST /api/users` | Anlegen (`username`, `password`, `role`, `person_id`), setzt `must_change_password=1` |
| `POST /api/users/<id>` | Ändern (`role`, `person_id`, `is_active`) |
| `POST /api/users/<id>/reset-password` | Neues Initialpasswort, Rückgabe **einmalig** im Response |
| `DELETE /api/users/<id>` | Löschen |

**Schutzgeländer:**

- Die eigene Rolle ist nicht änderbar.
- Der letzte aktive Admin kann nicht degradiert, deaktiviert oder gelöscht werden.
- Eine Person kann nicht gelöscht werden, solange sie mit einem aktiven Benutzer verknüpft ist.

---

## 5 Migrationsprozess bestehender Instanzen

Grundsatz: **Bestehende Personen werden nicht automatisch zu Logins.** Passwörter lassen sich ohne Mailserver nicht zustellen, und es ist nicht bekannt, welcher Mensch die gemeinsame `admin`-Kennung nutzt. Personen werden zu *verknüpfbaren Kandidaten*; das Anlegen eines Kontos bleibt eine bewusste Admin-Handlung.

### M0 Sicherheitsnetz (automatisch)

Vor der ersten Ausführung der Profil-Migration wird `create_backup("pre-profiles")` aufgerufen — dieselbe Funktion, die bereits vor jedem Restore läuft.

### M1 Automatische Schema-Migration (beim Start, ohne Interaktion)

Die `ALTER TABLE`-Anweisungen aus B1 laufen in der bestehenden idempotenten Liste. Bestandsnutzer erhalten dadurch `is_active=1`, `session_epoch=0`; `created_at` und `last_login` bleiben leer und werden im UI als „—" dargestellt.

### M2 Admin-Bestimmung (deterministische Kaskade, bei jedem Start)

1. Existiert ein aktiver Benutzer mit `role='admin'` → nichts tun.
2. Sonst: existiert ein Benutzer `admin` → dieser wird Admin.
3. Sonst: der Benutzer mit der niedrigsten `id` wird Admin.
4. Sonst (leere Tabelle) → der bestehende Bootstrap-Code legt `admin` aus `ADMIN_PASSWORD` an (`main.py:223`) und markiert ihn als Admin.

**Grund für die Kaskade:** `role` erhält per `ALTER TABLE` den Default `member`. Ohne Regel 2 würde der einzige vorhandene Benutzer zum Member degradiert und **niemand könnte mehr Benutzer verwalten**. Da die Regeln bei jedem Start laufen, kann eine Instanz nie ohne Admin enden.

### M3 Automatische Person-Verknüpfung

- Genau **eine** Person und genau **ein** Benutzer → werden automatisch verknüpft (eindeutig).
- Alle anderen Fälle → `person_id` bleibt `NULL`. Kein Namens-Matching: eine falsche Verknüpfung würde später dem falschen Menschen Buchungen zuordnen.

### M4 Einrichtungsassistent (interaktiv, einmalig, Admin)

Der bestehende Login funktioniert nach dem Update **unverändert weiter**; nichts wird erzwungen. Der Admin sieht einen aufschiebbaren Hinweis „Benutzerprofile sind neu — jetzt einrichten", jederzeit erneut aufrufbar aus den Einstellungen.

**Schritt 1 — „Wer bist du?"** Auswahl, welche bestehende Person der Admin selbst ist (oder „keine") → setzt `users.person_id`. Optional Umbenennung von `admin` auf den echten Namen, mit deutlicher Warnung, dass der Login danach auf den neuen Namen lautet. Nach der Umbenennung wird `session_epoch` erhöht, was eine bewusste Neuanmeldung erzwingt.

**Schritt 2 — Logins anlegen.** Liste aller Personen **ohne** verknüpften Benutzer, je Zeile mit Checkbox, vorgeschlagenem Benutzernamen (editierbar) und Rolle (Default `member`). Vor dem Anlegen zeigt der Assistent eine Vorschau der entstehenden Konten. Je angelegtem Konto wird ein Initialpasswort generiert und **einmalig** angezeigt (P1), `must_change_password=1` gesetzt.

**Schritt 3 — Abschluss.** Setzt `app_meta.profiles_setup_done = 1` und erinnert an M7.

### M5 Ableitung der Benutzernamen

Aus `persons.name`: Kleinschreibung, Umlaut-Transliteration (ä→ae, ö→oe, ü→ue, ß→ss), Leerzeichen zu Punkt, alle Zeichen außer `[a-z0-9._-]` entfernt. Bei Kollision mit der `UNIQUE`-Bedingung wird eine Zahl angehängt (`bob`, `bob2`). Eine Person namens „Admin" kollidiert mit dem Bestandskonto — der Vorschlag ist deshalb editierbar und wird nicht still umbenannt.

### M6 Passwort-Übergabe (P1)

Der Assistent generiert ein Initialpasswort und zeigt es genau einmal an, mit dem Hinweis, dass es nicht erneut abrufbar ist. Der Admin gibt es persönlich weiter; der Nutzer muss es beim ersten Login ändern. Ein späterer Reset erzeugt ein neues Initialpasswort über `POST /api/users/<id>/reset-password`.

### M7 Aufräumen

Sobald weitere Konten existieren, erinnert der Assistent daran, **das Passwort des ursprünglichen gemeinsamen Logins zu ändern** — die anderen Haushaltsmitglieder kennen es noch. Die bestehende Warnung „Admin-Passwort ist noch 'admin'" (`main.py:243`) bleibt erhalten.

**Nichts zu tun ist ein gültiger Endzustand.** Wer weiter mit einem gemeinsamen Login arbeiten möchte, ignoriert den Assistenten dauerhaft; die App verhält sich dann wie heute.

### M8 Sonderfälle und Rückwege

| Fall | Verhalten |
|---|---|
| **Altes Backup einspielen** | M1–M3 laufen beim nächsten Start automatisch erneut; alles idempotent, die Instanz ist danach wieder konsistent. |
| **Code-Rollback** | Die Migration ist abwärtskompatibel: Alte Programmversionen lesen `SELECT id, password_hash FROM users WHERE username=?` (`main.py:655`) und ignorieren die zusätzlichen Spalten. Ein Zurückrollen des Containers bleibt ohne DB-Eingriff möglich. |
| **Offener Browser-Tab beim Deploy** | Phase A ändert die Payload-Struktur. Ein alter Tab sendet Zeilen ohne `id` und ohne `deleted_ids` — der nicht-destruktive Backend würde daraus **Duplikate** anlegen. Deshalb: Payload-Version `"v": 2`; Anfragen ohne diese Marke werden mit HTTP 409 und „Bitte Seite neu laden" abgewiesen. **Zwingend Teil von Phase A.** |
| **Phase A selbst** | Braucht keine Datenmigration: WAL wird beim Verbindungsaufbau gesetzt, `activity`/`presence` entstehen per `CREATE TABLE IF NOT EXISTS`, und die Einzelzeilen-Umstellung nutzt die vorhandenen Primärschlüssel. |
| **Protokollierung** | Die Migration schreibt ins Log, welche Spalten ergänzt wurden, wer nach welcher Regel Admin wurde und ob automatisch verknüpft wurde. Bei verschlüsselter Produktions-DB der einzige Weg zur Nachvollziehbarkeit. |

---

## 6 Nicht im Umfang (optionale Phase C)

Persönliche Push-Erinnerungen pro Nutzer, Sprache/Theme pro Benutzer statt pro Session, Vorbelegung der eigenen Person in neuen Zeilen, Audit-Spalten (`created_by_user_id`), Viewer-Rolle, Mandantenfähigkeit, private Buchungen.

---

## 7 Testplan

**Phase A** — zwei parallele Browser-Sessions; keines der drei Szenarien aus 1.1 darf noch Daten verlieren:

1. „Charlie" bleibt nach dem Speichern durch den zweiten Nutzer erhalten.
2. Daueraufträge bleiben erhalten.
3. Die parallel angelegte Ausgabenzeile bleibt erhalten.
4. Autosave über mehrere Zyklen erzeugt keine Duplikate (`id_map` greift).
5. Geleerter Betrag löscht die Zeile wie bisher.
6. Legacy-Payload ohne `"v": 2` wird mit 409 abgewiesen.
7. Restore mit vorhandener `-wal`-Datei stellt den korrekten Stand her.

**Phase B:**

1. Migration gegen eine **Kopie** der verschlüsselten Produktions-DB; bestehender Login funktioniert unverändert.
2. Nach der Migration existiert genau ein Admin (Kaskade M2), auch bei mehrfachem Neustart.
3. Deaktivierter Nutzer wird trotz gültigem Cookie abgewiesen (`session_epoch`/`is_active`).
4. Member erhält bei `/api/backup/restore` HTTP 403.
5. Letzter Admin lässt sich nicht degradieren/deaktivieren/löschen.
6. Fehlversuche eines Nutzers sperren andere Nutzer derselben IP nicht.
7. Erstes Login eines neuen Kontos erzwingt Passwortwechsel.

---

## 8 Betroffene Dateien

| Datei | Phase A | Phase B |
|---|---|---|
| `app/routes/main.py` | `_open_db`, `save_expenses`, `save_income`, `save_persons`, `save_categories`, `save_recurring`, `api_backup_restore`, neu `/api/activity` | `init_db`, `login`, `login_required`, neu `admin_required`, neu `/api/users*`, Assistent, Backup-Endpunkte |
| `app/templates/month.html` | Zeilen-ids, `deleted_ids`, `id_map`, Autosave, Live-Banner | — |
| `app/templates/settings.html` | Zeilen-ids und `deleted_ids` für Personen/Kategorien/Daueraufträge | Karte „Benutzer", Einrichtungsassistent |
| `app/translations.py` | Live-Hinweis, 409-Meldung | Benutzerverwaltung, Assistent, Rollen |
