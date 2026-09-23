# Profil-Seite, Navigation und Zusammenführung Personen/Benutzer — Design

> **Für agentische Bearbeiter:** ERFORDERLICHER TEILSKILL: Nutze superpowers:subagent-driven-development (empfohlen) oder superpowers:executing-plans, um den zugehörigen Plan Schritt für Schritt umzusetzen.

**Ziel:** Jeder Nutzer bekommt eine eigene „Profil"-Seite mit seiner persönlichen Ausgaben-/Einnahmen-Übersicht, erreichbar über den eigenen Benutzernamen. Personen und Benutzerkonten werden zu einem einzigen, zwingend 1:1 gekoppelten Konzept: jede Person hat ein Konto, jedes Konto eine Person — Personen ohne Konto sind kein gültiger Zustand mehr. Das „Wer"-Feld bei Ausgaben und Einnahmen wird abgeschafft zugunsten von „immer die eingeloggte Person, unveränderbar“; Zuordnung an eine andere Person läuft weiterhin über das bestehende „für wen"-Feld.

**Architektur:** Die bestehende, große Berechnung in `overview()` wird in eine wiederverwendbare Funktion `_overview_daten(conn)` ausgelagert, die von `overview()` unverändert und von einer neuen Route `profile()` (personen-gefiltert) genutzt wird. Die getrennten „Personen"- und „Benutzer"-Karten in den Einstellungen werden zu einer einzigen Tabelle; zwei bestehende Endpunkte (`POST /api/users`, `DELETE /api/users/<id>`) werden um die atomare Mitbehandlung der verknüpften Person erweitert, statt neue Endpunkte einzuführen. `save_expenses`/`save_income` erzwingen `person_id` serverseitig statt es vom Client zu übernehmen.

**Tech-Stack:** Flask/Jinja2, bestehendes JS ohne Framework, Chart.js (bereits eingebunden über `overview.html`), SQLite über `_open_db`.

## Global Constraints

- Rollenprüfung bleibt **ausschließlich serverseitig** an jedem Endpunkt — kein UI-Verstecken als einzige Schranke.
- Keine `innerHTML`-Interpolation von Namen ohne `escHtml()` (K1).
- Jeder neue nutzersichtbare String kommt in **beide** Sprachblöcke (`de`/`en`) von `app/translations.py`.
- `person_id` bleibt serverseitig 1:1 erzwungen (`CREATE UNIQUE INDEX idx_users_person ... WHERE person_id IS NOT NULL`) — diese Arbeit macht die neue Anwendungslogik zusätzlich zur bestehenden DB-Constraint auch beim *Anlegen* verbindlich (nicht nur beim Verknüpfen einer bestehenden Person).
- Sichtbarkeitsgrenze aus Q2 bleibt bestehen: Benutzername/Rolle/Aktiv/letzter Login/Passwort-Aktionen sind weiterhin **nur für Admins** sichtbar und änderbar. Name und Geldbörsen-Felder bleiben für **alle** sichtbar und änderbar — auch nachdem beide Konzepte in einer Zeile/Tabelle zusammengeführt sind.
- Löschen einer Zeile (Person **und** Konto zusammen) ist ab jetzt **admin-only** (Änderung gegenüber heute, wo Personen-Löschen allen offenstand — Nutzerentscheidung, da jede Person jetzt zwingend ein Konto mitbringt).

---

## Teil 1 — Geteilte Berechnungsfunktion `_overview_daten(conn)`

**Dateien:**
- Ändern: `app/routes/main.py:2614-2857` (Funktion `overview()`)

Der komplette Berechnungsblock von `overview()` (ab der ersten Query bis kurz vor `return render_template(...)`) wandert unverändert in eine neue Funktion `_overview_daten(conn)`, die ein Dict mit allen bisherigen lokalen Variablen zurückgibt:
`structured, inc_structured, gesamt_structured, cats_map, month_keys, settled_months, persons_list, income_map, exp_totals_by_month, chart_data_obj, overview_chart_json, person_totals, person_totals_by_year, months_by_year, grand_income, grand_expense, grand_savings, open_settlements_count`.

`overview()` ruft `_overview_daten(conn)` auf und rendert wie bisher — **keine Verhaltensänderung** für die bestehende Übersichtsseite. Ein bestehender Test, der `/overview` aufruft und auf einen der oben genannten Werte prüft, muss unverändert grün bleiben (Regressionsschutz für den Refactor).

## Teil 2 — „Mein Profil"-Seite

**Dateien:**
- Erstellen: `app/templates/profile.html`
- Ändern: `app/routes/main.py` (neue Route `profile()`, nach `overview()`)
- Ändern: `app/translations.py` (neue Keys, siehe unten)

```python
@main_bp.route('/profile')
@login_required
def profile():
    conn = get_db()
    person_id = g.current_user['person_id']
    if person_id is None:
        conn.close()
        return render_template('profile.html', active='profile', unlinked=True)

    person = conn.execute('SELECT * FROM persons WHERE id=?', (person_id,)).fetchone()
    if person is None:
        # Sollte durch ON DELETE SET NULL nicht vorkommen; Schutz gegen
        # einen inkonsistenten Zwischenstand.
        conn.close()
        return render_template('profile.html', active='profile', unlinked=True)

    daten = _overview_daten(conn)
    pn = person['name']

    eigene_kategorien = daten['structured'].get(pn, {})
    eigene_jahre = daten['person_totals_by_year'].get(pn, {})
    eigene_totale = daten['person_totals'].get(pn, {'inc': 0, 'exp': 0, 'savings': 0})
    eigenes_chart_json = {
        'months': daten['overview_chart_json']['months'],
        'categories': daten['overview_chart_json']['categories'],
        'category_by_month': eigene_kategorien,
    }
    conn.close()

    return render_template('profile.html', active='profile', unlinked=False,
        person=person, person_name=pn, month_names=MONTH_NAMES,
        person_totals=eigene_totale, person_totals_by_year=eigene_jahre,
        profile_chart_json=eigenes_chart_json,
        all_cats=daten['overview_chart_json']['categories'])
```

`g.current_user` führt bereits `person_id` (siehe `login_required`, main.py:297) — kein zusätzlicher Datenbankzugriff dafür nötig. Der `unlinked=True`-Zweig ist nach Teil 4 (zwingende 1:1-Kopplung) im Normalbetrieb nicht mehr erreichbar, bleibt aber als defensiver Fallback bestehen (z. B. für den seltenen Übergangszustand direkt nach einer Migration aus einer sehr alten Sicherung).

`profile.html` (neu, erweitert `base.html` wie `overview.html`):
- **`unlinked=True`:** eine Karte mit Hinweistext `t('profile.unlinked_hint')` — keine weitere Interaktion.
- **Sonst:** Kennzahlen-Zeile (Einnahmen/Ausgaben/Saldo aus `person_totals`); Kategorie-Chart (Chart.js, `profile_chart_json`, gleiches Muster wie `overview.html`, ohne Jahres-Filter — YAGNI); Jahres-/Monatstabelle aus `person_totals_by_year` (identisches Rendering-Muster wie `overview.html` ab Zeile ~239, mit bereits gefilterten Daten); **neu:** eine kleine Karte „Meine Geldbörse" mit zwei Feldern (Bargeld-Startdatum, -betrag, vorbefüllt aus `person`), gespeichert über den bestehenden `/api/persons/save`-Endpunkt mit genau einer Zeile (`rows: [{id: person.id, name: person.name, wallet_start_date, wallet_start_amount}]`) — kein neuer Endpunkt nötig, der bestehende v2-Endpunkt erlaubt bereits jedem, seine eigene Zeile zu speichern.

**Übersetzungen** (`app/translations.py`, beide Blöcke):
```
'nav.profile': 'Mein Profil' / 'My Profile'
'profile.title': 'Mein Profil' / 'My Profile'
'profile.unlinked_hint': 'Dein Konto ist noch keiner Person zugeordnet. Bitte wende dich an einen Administrator.' / 'Your account is not yet linked to a person. Please contact an administrator.'
'profile.stat_income': 'Einnahmen gesamt' / 'Total income'
'profile.stat_expense': 'Ausgaben gesamt' / 'Total expenses'
'profile.stat_savings': 'Ersparnis' / 'Savings'
'profile.categories_title': 'Meine Kategorien' / 'My Categories'
'profile.years_title': 'Nach Jahr' / 'By Year'
'profile.wallet_title': 'Meine Geldbörse' / 'My Wallet'
```

## Teil 3 — Navigation: anklickbarer Benutzername + mobiles Symbol

**Dateien:**
- Ändern: `app/templates/base.html`

**Desktop** (um `base.html:139-141`): der bestehende `<span>` wird zu einem `<a>`:
```jinja
<a href="{{ url_for('main.profile') }}"
   class="text-white-50 small d-none d-lg-inline text-decoration-none profile-link">
  <i class="bi bi-person-circle me-1"></i>{{ session.get('username', '') }}
</a>
```
Neue CSS-Regel `.profile-link:hover { text-decoration: underline; }` im `<style>`-Block von `base.html`.

**Mobil** (bottom-nav, `base.html:179-193`): fünftes Element:
```jinja
<a href="{{ url_for('main.profile') }}" class="{% if active=='profile' %}active{% endif %}">
  <i class="bi bi-person-circle"></i><span>{{ t('nav.profile') }}</span>
</a>
```
Die bestehende `.bottom-nav`-Flex-Regel muss am echten Gerät/Emulator mit fünf statt vier Kindern geprüft werden; bei zu enger Beschriftung Textgröße reduzieren, analog zu bereits vorhandenen Lösungen für schmale Viewports in diesem Projekt.

## Teil 4 — Zusammenführung Personen/Benutzer: eine Zeile, zwingend 1:1

**Dateien:**
- Ändern: `app/templates/settings.html` (Karte „Personen" wird zur kombinierten Karte, Karte „Benutzer" entfällt)
- Ändern: `app/routes/main.py` (`api_users_create`, `api_users_delete` erweitert)

**Invariante:** Ab dieser Änderung hat jede Person zwingend genau ein Benutzerkonto und jedes Konto zwingend genau eine Person. Eine Person ohne Konto oder ein Konto ohne Person ist kein gültiger, aktiv herstellbarer Zustand mehr (Migrationsbestand aus der Zeit davor wird nicht rückwirkend zwangsbereinigt, siehe Teil 2s defensiver Fallback).

**Datenfluss:** `benutzerLaden()` (bestehende JS-Funktion) baut zusätzlich eine Zuordnung `person_id -> Benutzerobjekt` und rendert pro Personen-Zeile die passenden Konto-Felder daneben (statt einer eigenen Tabelle).

**Struktur je Zeile:**
- Für alle sichtbar/editierbar (unverändert): Name, Bargeld-Startdatum, -betrag — gleicher Speicherweg wie heute (`save-persons-btn` → `/api/persons/save`).
- Nur für Admins zusätzlich sichtbar, in derselben Zeile: Benutzername (Text), Rollen-Auswahl, Aktiv-Schalter, letzter Login, „Passwort zurücksetzen", „Zeile löschen" — dieselben Endpunkte wie heute (`PATCH /api/users/<id>`, `POST /api/users/<id>/reset-password`), nur andere Anordnung.

**Neu anlegen** (admin-only, ein Eingabefeld: Name, dient gleichzeitig als Benutzername): `POST /api/users` wird erweitert — fehlt `person_id` im Request, legt der Endpunkt in derselben Transaktion zuerst eine neue `persons`-Zeile mit `name = username` an und verwendet deren `id` als `person_id` für den `users`-INSERT. Beide Inserts committen gemeinsam oder keiner (ein Fehlschlag beim zweiten Insert lässt die Transaktion rollbacken, keine verwaiste Person). Ein Aufruf **mit** vorhandener `person_id` funktioniert unverändert wie heute (Rückwärtskompatibilität für den seltenen Fall eines nachträglichen Verknüpfens).

**Löschen** (admin-only, eine Aktion je Zeile): `DELETE /api/users/<id>` wird erweitert — nach den bestehenden Selbstschutzprüfungen (kein Selbstlöschen, letzter Admin bleibt) wird in derselben Transaktion zusätzlich die verknüpfte `persons`-Zeile gelöscht, falls `person_id` gesetzt ist. Die bestehende Schutzregel in `save_persons` (Löschen einer Person mit aktivem Login blockiert) bleibt als zusätzliche Absicherung unverändert bestehen; sie greift unter der neuen Regel nur noch, falls eine Person direkt über den alten `deleted_ids`-Pfad gelöscht werden sollte, was in der neuen Oberfläche nicht mehr passiert (kein Löschen-Knopf mehr auf Personen-Ebene für Nicht-Admins).

**Entfernt:**
- Die gesamte „Benutzer"-Karte (`settings.html:205-228`) als eigenständige Karte.
- Der Einrichtungsassistent-Schritt, der unverknüpfte Personen vorschlägt (`_wizardVorschlaege`) — die neue Tabelle hat diesen Zustand nicht mehr.
- **Bleibt unverändert:** Assistent-Schritt 1 (eigene Person zuordnen + optionales Umbenennen, `/api/setup/profiles/self`).

**Übersetzungen:** neuer Key `settings.person_add_prompt` (Eingabeaufforderung für den gemeinsamen Namen), Rest verwendet bestehende `settings.user_*`-Keys weiter.

## Teil 5 — „Wer"-Feld: fest statt wählbar

**Dateien:**
- Ändern: `app/templates/month.html` (`addRow`, `addIncomeRow`, Rendering der „Wer"-Spalte)
- Ändern: `app/routes/main.py` (`month_view`-Route, `save_expenses`, `save_income`)

**Frontend:** Die „Wer"-Spalte zeigt für jede Zeile (neu **und** bestehend) nur noch den Namen als Text, kein `<select>` mehr. `month_view` übergibt `current_user_person_name` (aus `g.current_user['person_id']` per Join auf `persons.name`, `None` falls unverknüpft) in den Render-Kontext; `month.html` zeigt diesen Namen für neue Zeilen, bzw. bei geladenen Zeilen den serverseitig gespeicherten Namen (kommt bereits über die bestehende Datenauslieferung).

**Backend, `save_expenses`** (analog `save_income`):
```python
if rid is not None:
    # "Wer" ist nach dem Anlegen unveraenderbar (Nutzerentscheidung) -
    # deshalb fehlt person_id hier bewusst in der SET-Klausel.
    conn.execute(
        'UPDATE expenses SET day=?, description=?, category_id=?,'
        ' amount=?, is_cash=?, paid_for_person_id=?, receipt_filename=?'
        ' WHERE id=? AND year=? AND month=?',
        (day, desc, cid, amt, is_cash, pfpid, rfn, rid, year, month))
else:
    # Neue Zeile: "Wer" kommt IMMER aus der Sitzung, nie vom Client -
    # serverseitig erzwungen, nicht nur im Frontend entfernt (sonst liesse
    # sich das per direktem API-Aufruf umgehen).
    pid = g.current_user['person_id']
    cur = conn.execute(
        'INSERT INTO expenses (year,month,day,description,person_id,category_id,'
        'amount,is_cash,paid_for_person_id,receipt_filename) VALUES (?,?,?,?,?,?,?,?,?,?)',
        (year, month, day, desc, pid, cid, amt, is_cash, pfpid, rfn))
    ...
```
Ein vom Client mitgesendetes `person_id`-Feld wird für beide Fälle vollständig ignoriert. Gilt identisch für `save_income`. „Für wen" (`paid_for_person_id`) bleibt in beiden Tabellen unverändert frei wählbar.

---

## Selbstprüfung

- **Platzhalter-Scan:** keine TBD/TODO — alle Endpunkte, Dateipfade und Feldnamen sind konkret benannt.
- **Konsistenz:** `_overview_daten` liefert exakt die Schlüssel, die `profile()` und `overview()` beide konsumieren. Die 1:1-Invariante aus Teil 4 und der defensive `unlinked`-Zweig aus Teil 2 widersprechen sich nicht (Migrationsbestand vs. Normalbetrieb sind explizit unterschieden).
- **Scope:** Ein zusammenhängender Umsetzungsplan (fünf klar abgegrenzte Teile, jeder für sich testbar).
- **Mehrdeutigkeit, alle durch Nutzerentscheidung aufgelöst:** Profil ohne Verknüpfung → nur Hinweistext, keine Selbstbedienung. Wizard-Vorschlagsschritt → entfernt. 1:1-Pflicht → zwingend, auch beim Anlegen. „Wer"-Sperre → vollständig, auch bei Bestandszeilen, beide Tabellen. Löschrecht → admin-only. Name/Login beim Anlegen → ein gemeinsames Feld. Sichtbarkeit Rolle/Aktiv/Benutzername → bleibt admin-only, auch in der zusammengeführten Zeile.
