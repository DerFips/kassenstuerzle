# Design: Nettovermögen im Profil

**Datum:** 2026-10-21
**Status:** Entwurf abgestimmt, Spec zur Durchsicht
**Umfang:** Eine Umsetzungseinheit (ein Plan).

---

## 1 Ziel

Jede Person kann im Profil ihr **Nettovermögen** berechnen, analog zu einer Vermögensaufstellung beim Finanzberater: Konten (Vermögen und Schulden) mit Bezeichnung und Typ anlegen, Salden zu frei wählbaren Stichtagen eintragen und den Verlauf des Nettovermögens sehen. Im Profil erscheint dafür eine neue Kennzahl.

## 2 Getroffene Entscheidungen

| ID | Entscheidung | Begründung |
|---|---|---|
| V1 | **Eine Liste von Konten** mit Art *Vermögen* oder *Schuld*, nicht zwei getrennte Konzepte | Gleiche Felder, gleiche Pflege. Die Art ergibt sich aus dem Typ. |
| V2 | **Strikt persönlich und privat**: nur die eigene Person sieht und pflegt ihre Konten, auch Admins nicht | Vermögensdaten sind sensibler als Buchungen. Gleiches Prinzip wie bei den wiederkehrenden Ausgaben. |
| V3 | **Keine automatische Verbindung** zu Buchungen und Geldbörse | Salden werden von Hand eingetragen, wie bei einer Beratung. |
| V4 | **Verlauf mit frei wählbaren Stichtagen** statt nur aktuellem Saldo | Der Wert einer Vermögensaufstellung liegt im Trend. |
| V5 | **Konten und Salden-Protokoll getrennt**: ein Konto ist nur Bezeichnung und Typ, Salden sind eigene datierte Einträge | Ein Stichtag für alle Konten auf einmal, alte Einträge bleiben unangetastet. |

## 3 Datenmodell

Beide Tabellen werden in `init_db()` mit `CREATE TABLE IF NOT EXISTS` angelegt (additiv, bestehende Installationen und ältere Sicherungen bleiben gültig).

```sql
CREATE TABLE IF NOT EXISTS wealth_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS wealth_balances (
    account_id INTEGER NOT NULL REFERENCES wealth_accounts(id) ON DELETE CASCADE,
    balance_date TEXT NOT NULL,          -- JJJJ-MM-TT
    balance REAL NOT NULL,
    PRIMARY KEY (account_id, balance_date));
```

- **Cascade:** `PRAGMA foreign_keys = ON` steht in `_open_db`. Wird eine Person gelöscht, verschwinden ihre Konten und Salden. Löscht man ein Konto, verschwinden seine Salden.
- **Art aus Typ:** Es gibt keine Spalte `kind`. Die Zuordnung Typ → Art steht an genau einer Stelle im Code (`WEALTH_TYPES`), damit sie nicht widersprüchlich sein kann.
- **Ein Eintrag pro Konto und Tag:** Der Primärschlüssel erzwingt das. Erneutes Eintragen am selben Tag überschreibt (Upsert).
- **Backup-Positivliste:** `wealth_accounts` und `wealth_balances` kommen in `_EIGENE_TABELLEN`. Der implizite Index `sqlite_autoindex_wealth_balances_1` wird über das bestehende Autoindex-Muster erlaubt. Den Gleichlauf sichert `test_erlaubte_objekte_bleiben_mit_init_db_im_gleichlauf`.
- **Tests:** Beide Tabellen kommen in `_CLEANUP_TABLES` in `tests/conftest.py`.

### 3.1 Typen

| Schlüssel | Art | Deutsch | Englisch |
|---|---|---|---|
| `checking` | Vermögen | Girokonto | Checking account |
| `savings` | Vermögen | Tagesgeld | Savings account |
| `fixed_deposit` | Vermögen | Festgeld | Fixed deposit |
| `securities` | Vermögen | Aktiendepot / ETF | Securities / ETF |
| `life_insurance` | Vermögen | Lebensversicherung (Rückkaufswert) | Life insurance (surrender value) |
| `pension` | Vermögen | Altersvorsorge | Retirement savings |
| `real_estate` | Vermögen | Immobilie | Real estate |
| `vehicle` | Vermögen | Fahrzeug | Vehicle |
| `cash` | Vermögen | Bargeld | Cash |
| `other_asset` | Vermögen | Sonstiges Vermögen | Other asset |
| `loan` | Schuld | Kredit | Loan |
| `mortgage` | Schuld | Baufinanzierung / Hypothek | Mortgage |
| `credit_card` | Schuld | Kreditkarte | Credit card |
| `overdraft` | Schuld | Dispo | Overdraft |
| `other_debt` | Schuld | Sonstige Schuld | Other debt |

## 4 Berechnung

Eine reine Funktion `_wealth_data(conn, person_id)` in `app/routes/main.py`, analog zu `_overview_daten`. Sie liest nur Konten und Salden der übergebenen Person.

**Rückgabe:**

```python
{
  'accounts': [{'id', 'name', 'type', 'kind', 'balance', 'balance_date'}],  # aktueller Stand je Konto
  'total_assets': float,
  'total_debts': float,
  'net_worth': float,                                  # total_assets - total_debts
  'series': [{'date', 'assets', 'debts', 'net_worth'}],
  'entries': [{'account_id', 'balance_date', 'balance'}],  # alle Einträge, für die Verlaufsliste
}
```

- **Aktuell:** Pro Konto zählt der jüngste Eintrag (größtes `balance_date`). Ein Konto ohne Eintrag zählt 0 und hat kein `balance_date`.
- **Verlauf:** Für jedes Datum, an dem mindestens ein Eintrag der Person existiert (aufsteigend, ohne Duplikate), nimmt jedes Konto seinen letzten Stand mit `balance_date <= Datum`. Ein Konto ohne früheren Eintrag zählt an diesem Datum 0. Vermögen und Schulden werden getrennt summiert.
- **Rundung:** Alle Summen auf 2 Nachkommastellen.
- **Leer:** Ohne Konten sind alle Summen 0 und `series` ist leer.

## 5 Server-Endpunkte

Beide Endpunkte brauchen `@login_required`, laufen mit CSRF-Token wie die übrigen JSON-APIs und verwenden die eigene Person aus `g.current_user['person_id']`. **Ein Client-Wert für `person_id` wird nie ausgewertet.** Hat das Konto keine Person, antworten sie mit 400 und `srv.account_not_linked` (wie `save_recurring`).

### 5.1 `POST /api/wealth/accounts/save`

Gleiches Protokoll wie die übrigen Einstellungs-Endpunkte (`{v: 2, rows, deleted_ids}`, `client_key`, Antwort mit `id_map`):

- `rows[]`: `{id | client_key, name, type}`.
- **Anlegen:** `INSERT` mit der eigenen Person. **Ändern:** `UPDATE ... WHERE id=? AND person_id=?`, `person_id` steht nie in der `SET`-Klausel. **Löschen:** `DELETE ... WHERE id=? AND person_id=?`. Ein Treffer auf ein fremdes Konto bleibt still wirkungslos.
- **Validierung:** `name` wird mit `strip()` bereinigt, nicht leer, höchstens 60 Zeichen. `type` muss in `WEALTH_TYPES` stehen. Eine ungültige Zeile wird übersprungen, die übrigen werden gespeichert.
- Die Antwort enthält `id_map` und die aktuelle Kontenliste der Person.

### 5.2 `POST /api/wealth/balances/save`

```json
{ "v": 2, "date": "2026-10-21",
  "entries": [{ "account_id": 3, "balance": 1250.40 }],
  "deleted": [{ "account_id": 3, "balance_date": "2026-01-31" }] }
```

- `entries`: Upsert je `(account_id, date)` mit `INSERT ... ON CONFLICT(account_id, balance_date) DO UPDATE`. Ein Eintrag wird nur geschrieben, wenn das Konto der eigenen Person gehört (Join auf `wealth_accounts.person_id`), sonst still übersprungen.
- `deleted`: löscht Einträge nach `(account_id, balance_date)`, ebenfalls nur bei eigenem Konto.
- **Validierung:** `date` muss ein gültiges Datum im Format `JJJJ-MM-TT` sein (`datetime.strptime`) und darf nicht in der Zukunft liegen. `balance` muss eine **endliche** Zahl sein (`math.isfinite`, denn Pythons JSON-Parser nimmt auch `NaN` und `Infinity` an) mit Betrag unter 1e12. Ungültiges wird mit 400 abgewiesen, ohne etwas zu schreiben.
- Kein Aktivitätszähler (`_bump_activity`): Die Daten sind privat, ein Live-Hinweis an andere wäre unnötig.
- Die Antwort enthält die neu berechneten Vermögensdaten (`_wealth_data`).

## 6 Oberfläche (Profil)

Die Daten kommen serverseitig über `profile()` (`wealth=_wealth_data(...)`, plus Typliste für die Auswahl) in das Template [profile.html](../../../app/templates/profile.html).

1. **Kennzahl:** Eine vierte Karte "Nettovermögen" in der oberen Reihe (alle vier `col-md-3`). Darunter in klein: Vermögen und Schulden. Negatives Nettovermögen wird rot dargestellt, positives grün.
2. **Karte "Mein Vermögen":**
   - Zwei Gruppen **Vermögen** und **Schulden**, je mit Konten (Bezeichnung, Typ-Auswahl gruppiert nach Art, Löschen) und "Konto hinzufügen". Eine Schaltfläche speichert die Konten über 5.1.
   - **Salden aktualisieren:** Ein Datumsfeld "Stand vom" (vorbelegt mit heute, nicht in der Zukunft) und pro Konto ein Saldofeld, vorbelegt mit dem aktuellen Saldo. "Salden speichern" sendet nur Konten, deren Wert vom angezeigten aktuellen Saldo abweicht, zu diesem Datum (5.2). Unveränderte Konten bekommen keinen neuen Eintrag, ihr letzter Stand wird im Verlauf fortgeschrieben. Eine Korrektur eines alten Eintrags geschieht über die Verlaufsliste (löschen und neu eintragen).
   - **Verlaufsliste** (aufklappbar): alle Einträge mit Datum, Konto und Saldo, jeweils mit Löschen-Button.
   - **Diagramm:** Liniendiagramm des Nettovermögens über die Stichtage (Chart.js, auf der Seite bereits geladen). Zusätzlich Vermögen und Schulden als eigene Linien.
3. **Nach dem Speichern** lädt die Seite neu. So sind Kennzahl, Gruppen, Verlaufsliste und Diagramm immer konsistent mit dem Server, ohne zweite Render-Logik im Browser.
4. **Sicherheit der Oberfläche:** Kontonamen werden nur über `textContent`, `value` und `new Option()` ausgegeben, nie über `innerHTML` oder Template-Literale (Regel K1). Das Inline-Skript trägt `nonce="{{ csp_nonce() }}"`. Daten fürs Diagramm gehen über `tojson`.
5. **Ohne Verknüpfung:** Ist das Konto keiner Person zugeordnet, zeigt das Profil wie bisher nur den Hinweis, die Karte erscheint nicht.

**Texte:** Alle neuen Schlüssel mit Präfix `wealth.` bzw. `profile.stat_net_worth` stehen in **beiden** Blöcken `de` und `en` von `app/translations.py`, einschließlich der 15 Typbezeichnungen aus 3.1.

## 7 Rechte und Datenschutz

- Jeder Zugriff filtert serverseitig auf `g.current_user['person_id']`. Die Oberfläche versteckt nichts, was der Server nicht ohnehin verweigert.
- **Ehrliche Einschränkung:** Das ist Schutz auf Anwendungsebene. Ein Admin kann die ganze Datenbank als Backup herunterladen, die Salden stehen darin. Die Datenbank ist mit SQLCipher verschlüsselt, den Schlüssel hat aber, wer Zugriff auf den Server hat.
- Die Daten gehören nicht in CSV-Export und PDF-Import.

## 8 Tests

Neue Testdatei `tests/test_wealth.py`:

- **Berechnung:** aktueller Saldo (jüngster Eintrag gewinnt), Summen, Nettovermögen mit Schulden, Verlauf mit mehreren Stichtagen (Konto ohne früheren Eintrag zählt 0), leerer Zustand.
- **Rechte:** Fremde Konten lassen sich weder ändern noch löschen noch mit Salden beschreiben (jeweils per direktem API-Aufruf), Client-gesendetes `person_id` wird ignoriert, Konto ohne Person bekommt 400. Jeder Rechte-Test muss ohne den `AND person_id=?`-Schutz nachweislich rot sein.
- **Validierung:** ungültiger Typ, leerer oder zu langer Name, ungültiges und zukünftiges Datum, `NaN`, `Infinity` und überlange Beträge.
- **Cascade:** Person löschen entfernt Konten und Salden, Konto löschen entfernt seine Salden.
- **Upsert:** zweites Eintragen am selben Tag überschreibt, es entsteht keine Dublette.
- **Backup:** `test_erlaubte_objekte_bleiben_mit_init_db_im_gleichlauf` bleibt grün.
- **Oberfläche:** Seitenquelltext des Profils enthält die Karte, die Kennzahl und das Nonce, Übersetzungsschlüssel existieren in beiden Sprachen.

Dazu eine Browser-Prüfung gegen eine Wegwerf-Datenbank (Anlegen, Salden an zwei Stichtagen, Verlauf, Löschen, zwei Personen mit getrennter Sicht, keine Konsolenfehler). `data/` wird nie angefasst.

## 9 Bewusst nicht Teil dieser Umsetzung

- Automatische Verknüpfung mit Buchungen, Geldbörse oder Ersparnis.
- Gemeinschaftskonten mit Anteilen mehrerer Personen und eine Haushaltssumme.
- Währungen, Zinsen, Tilgungspläne und Prognosen.
- Archivieren von Konten (ein aufgelöstes Konto wird gelöscht oder mit Saldo 0 weitergeführt).
- Export der Vermögensdaten.
