# Profil-Seite, Navigation und Zusammenführung Personen/Benutzer — Design

> **Für agentische Bearbeiter:** ERFORDERLICHER TEILSKILL: Nutze superpowers:subagent-driven-development (empfohlen) oder superpowers:executing-plans, um den zugehörigen Plan Schritt für Schritt umzusetzen.

**Ziel:** Jeder Nutzer bekommt eine eigene „Profil"-Seite mit seiner persönlichen Ausgaben-/Einnahmen-Übersicht, erreichbar über den eigenen Benutzernamen. Die „Personen"- und „Benutzer"-Verwaltung in den Einstellungen werden zu einer einzigen, personen-zentrierten Tabelle zusammengeführt. Beim Anlegen einer neuen Ausgaben-/Einnahmenzeile wird das „Wer"-Feld standardmäßig auf die eigene verknüpfte Person gesetzt.

**Architektur:** Die bestehende, große Berechnung in `overview()` (App-weite Auswertung aller Personen) wird in eine wiederverwendbare Funktion `_overview_daten(conn)` ausgelagert, die von `overview()` unverändert und von einer neuen Route `profile()` (personen-gefiltert) genutzt wird. Die Einstellungsseite bekommt eine neue, personen-zentrierte Tabelle statt zweier getrennter; Rollenprüfung bleibt serverseitig unverändert (nur `@admin_required`-Endpunkte dürfen Login-Felder ändern). `month.html`s `addRow`/`addIncomeRow` bekommen einen neuen Parameter für die Default-Person.

**Tech-Stack:** Flask/Jinja2, bestehendes JS ohne Framework, Chart.js (bereits eingebunden über `overview.html`), SQLite über `_open_db`.

## Global Constraints

- Rollenprüfung bleibt **ausschließlich serverseitig** an jedem Endpunkt — kein UI-Verstecken als einzige Schranke (bestehende Regel dieses Projekts).
- Keine `innerHTML`-Interpolation von Namen ohne `escHtml()` — bestehende Regel aus der Sicherheitsprüfung (K1).
- Jeder neue nutzersichtbare String kommt in **beide** Sprachblöcke (`de` und `en`) von `app/translations.py`.
- Bestehende Endpunkte (`/api/persons/save`, `/api/users`, `/api/users/<id>`, `/api/users/<id>/reset-password`) ändern sich in ihrer Signatur NICHT — nur die Frontend-Ansteuerung wird umgebaut.
- `person_id` ist serverseitig bereits 1:1 erzwungen (`CREATE UNIQUE INDEX idx_users_person ... WHERE person_id IS NOT NULL`) — die Oberfläche muss diese Invariante nur korrekt darstellen, nicht neu absichern.
- Das Löschen einer Person mit aktivem Login bleibt serverseitig blockiert (`save_persons`, bestehende Prüfung) — unverändert.

---

## Teil 1 — Geteilte Berechnungsfunktion `_overview_daten(conn)`

**Dateien:**
- Ändern: `app/routes/main.py:2614-2857` (Funktion `overview()`)

Der komplette Berechnungsblock von `overview()` (Zeilen ~2618 bis kurz vor `return render_template(...)`) wandert unverändert in eine neue Funktion `_overview_daten(conn)`, die ein Dict mit allen bisherigen lokalen Variablen zurückgibt:
`structured, inc_structured, gesamt_structured, cats_map, month_keys, settled_months, persons_list, income_map, exp_totals_by_month, chart_data_obj, overview_chart_json, person_totals, person_totals_by_year, months_by_year, grand_income, grand_expense, grand_savings, open_settlements_count`.

`overview()` ruft `_overview_daten(conn)` auf und rendert wie bisher — **keine Verhaltensänderung** für die bestehende Übersichtsseite. Ein bestehender Test, der `/overview` aufruft und auf einen der oben genannten Werte prüft, muss unverändert grün bleiben (Regressionsschutz für den Refactor).

## Teil 2 — „Mein Profil"-Seite

**Dateien:**
- Erstellen: `app/templates/profile.html`
- Ändern: `app/routes/main.py` (neue Route `profile()`, nach `overview()`)
- Ändern: `app/translations.py` (neue Keys, siehe unten)

Neue Route:

```python
@main_bp.route('/profile')
@login_required
def profile():
    conn = get_db()
    person_id = g.current_user['person_id']
    if person_id is None:
        conn.close()
        return render_template('profile.html', active='profile', unlinked=True)

    person = conn.execute('SELECT name FROM persons WHERE id=?', (person_id,)).fetchone()
    if person is None:
        # Verknuepfte Person wurde geloescht, Verknuepfung aber noch nicht
        # nachgezogen (sollte durch ON DELETE SET NULL nicht vorkommen,
        # ist aber ein guenstiger Schutz gegen einen inkonsistenten Zwischenstand).
        conn.close()
        return render_template('profile.html', active='profile', unlinked=True)

    daten = _overview_daten(conn)
    pn = person['name']
    conn.close()

    eigene_kategorien = daten['structured'].get(pn, {})
    eigene_einnahmen = daten['inc_structured'].get(pn, {})
    eigene_jahre = daten['person_totals_by_year'].get(pn, {})
    eigene_totale = daten['person_totals'].get(pn, {'inc': 0, 'exp': 0, 'savings': 0})

    # Kategorie-Chart nur mit den eigenen Werten, gleiche Form wie
    # overview_chart_json, damit dieselbe Chart.js-Logik wiederverwendbar ist.
    eigenes_chart_json = {
        'months': [m for m in daten['overview_chart_json']['months']],
        'categories': daten['overview_chart_json']['categories'],
        'category_by_month': {mk: cats for mk, cats in eigene_kategorien.items()},
    }

    return render_template('profile.html', active='profile', unlinked=False,
        person_name=pn,
        month_names=MONTH_NAMES,
        person_totals=eigene_totale,
        person_totals_by_year=eigene_jahre,
        profile_chart_json=eigenes_chart_json,
        all_cats=daten['overview_chart_json']['categories'])
```

`g.current_user` führt bereits `person_id` (siehe `login_required`, main.py:297) — kein neuer Datenbankzugriff dafür nötig.

`profile.html` (neu, ~150-200 Zeilen, erweitert `base.html` wie `overview.html`):
- **Ohne Verknüpfung** (`unlinked=True`): eine Karte mit Hinweistext `t('profile.unlinked_hint')` — keine weitere Interaktion.
- **Mit Verknüpfung**: Kopfzeile mit `person_name`; eine Kennzahlen-Zeile (Einnahmen/Ausgaben/Saldo, analog zu `overview.html`s `stat_income`/`stat_expense`/`stat_savings`, aus `person_totals`); ein Kategorie-Chart (Chart.js, Datenquelle `profile_chart_json`, gleiches Muster wie `overview.html`s Kategorie-Chart, aber ohne Jahres-Filter-Dropdown, da eine Einzelperson typischerweise wenige Jahre an Daten hat — YAGNI, kann später ergänzt werden, falls gebraucht); eine Jahres-/Monatstabelle direkt aus `person_totals_by_year` (identisches Rendering-Muster wie `overview.html`s bestehende Jahresblöcke ab Zeile ~239, nur mit den bereits gefilterten Daten statt der Schleife über `person_totals_by_year.items()`).

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
```
(Exakte zusätzliche Keys für Tabellen-Spaltenköpfe werden im Implementierungsplan aus den bereits vorhandenen `overview.*`-Keys übernommen, wo identische Beschriftung passt — kein Duplikat-Text mit neuem Key, wo ein bestehender Key exakt passt.)

## Teil 3 — Navigation: anklickbarer Benutzername + mobiles Symbol

**Dateien:**
- Ändern: `app/templates/base.html`

**Desktop** (um `base.html:139-141`): der bestehende `<span>` wird zu einem `<a>`:
```jinja
<a href="{{ url_for('main.profile') }}"
   class="text-white-50 small d-none d-lg-inline text-decoration-none">
  <i class="bi bi-person-circle me-1"></i>{{ session.get('username', '') }}
</a>
```
(`text-decoration-none` als Basis, `:hover` Unterstreichung kommt aus einer neuen, kleinen CSS-Regel im `<style>`-Block von `base.html`, damit erkennbar bleibt, dass es klickbar ist, ohne die bestehende dezente Optik zu stören.)

**Mobil** (bottom-nav, `base.html:179-193`): ein fünftes Element vor oder nach „Einstellungen":
```jinja
<a href="{{ url_for('main.profile') }}" class="{% if active=='profile' %}active{% endif %}">
  <i class="bi bi-person-circle"></i><span>{{ t('nav.profile') }}</span>
</a>
```
Die bestehende `.bottom-nav`-CSS-Regel (gleich breite Flex-Kinder) verträgt ein fünftes Element rechnerisch, muss aber am echten Gerät/Emulator geprüft werden — bei zu enger Beschriftung wird der Text kleiner skaliert oder (falls nötig) ausgeblendet zugunsten des Icons allein, analog zu vorhandenen Lösungen für schmale Viewports in diesem Projekt (siehe die frühere mobile-Überlauf-Behebung).

## Teil 4 — Zusammenführung Personen/Benutzer in den Einstellungen

**Dateien:**
- Ändern: `app/templates/settings.html` (Karte „Personen" wird zur kombinierten Karte, Karte „Benutzer" entfällt)

**Datenfluss:** Die Seite braucht weiterhin sowohl `persons` (Jinja-Kontext, wie bisher) als auch die Benutzerliste — letztere lädt bereits heute per JS (`benutzerLaden()` gegen ein bestehendes `/api/users`-GET). Für die Zusammenführung wird `benutzerLaden()` so erweitert, dass es nach dem Laden der Benutzerliste eine Zuordnung `person_id -> Benutzerobjekt` aufbaut und beim Rendern jeder Personen-Zeile (`persons-list`, bestehende Schleife) das passende Benutzerobjekt (falls vorhanden) daneben einblendet.

**Struktur je Zeile** (nur für `current_role == 'admin'` sichtbar erweitert; alle anderen sehen nur die bestehenden drei Personen-Felder + Löschen-Button unverändert):
- Bestehende Felder: Name, Bargeld-Startdatum, -betrag, Löschen-Button — **unverändert**, gleiche IDs/Klassen, gleicher Speicherweg (`save-persons-btn` → `/api/persons/save`).
- Neu, nur Admin, rechts an der Zeile angehängt (`d-flex` mit den bestehenden Feldern, umbrechend auf schmalen Bildschirmen):
  - **Falls verknüpft:** Benutzername (Text, nicht editierbar an dieser Stelle — Umbenennen bleibt ein Sonderfall über die bestehende Wizard-Funktion, siehe unten), Rollen-Auswahl (`admin`/`member`), Aktiv-Schalter, „Passwort zurücksetzen"-Knopf, „Konto löschen"-Knopf. Jede Aktion ruft exakt denselben bestehenden Endpunkt wie heute in der alten Benutzer-Tabelle (`PATCH /api/users/<id>`, `POST /api/users/<id>/reset-password`, `DELETE /api/users/<id>`) — nur die aufrufende Zeile ändert sich, nicht die Endpunkte.
  - **Falls nicht verknüpft:** ein Knopf „+ Konto anlegen“, der denselben Ablauf wie der heutige `add-user-btn` auslöst (Prompt für den Benutzernamen, `POST /api/users`), aber `person_id` direkt auf die ID dieser Zeile setzt statt `null`.

**Entfernt:**
- Die gesamte „Benutzer"-Karte (`settings.html:205-228` inkl. Tabelle, `users-tbody`, zugehörige JS-Funktionen soweit nicht wiederverwendet).
- Der Einrichtungsassistent-Schritt, der unverknüpfte Personen als Vorschläge listet (`_wizardVorschlaege`, das entsprechende Wizard-UI-Fragment) — die neue Tabelle zeigt das jetzt direkt und dauerhaft, nicht nur einmalig im Assistenten.
- **Bleibt unverändert:** Schritt 1 des Assistenten (eigene Person zuordnen + optionales Umbenennen des Bestandskontos, `/api/setup/profiles/self`) — anderer Zweck (einmalige Selbstzuordnung beim ersten Login), bleibt bestehen.

**Übersetzungen:** neuer Key `settings.person_add_account` („+ Konto anlegen" / "+ Add account"), Rest verwendet bestehende `settings.user_*`-Keys weiter.

## Teil 5 — Standardperson im „Wer"-Feld

**Dateien:**
- Ändern: `app/templates/month.html` (`addRow`, `addIncomeRow`, deren Aufrufstellen für eine leere neue Zeile)
- Ändern: `app/routes/main.py` (Route `month_view`, zusätzlicher Kontextwert)

Die Route für die Monatsansicht bekommt einen zusätzlichen Render-Kontext-Wert `current_user_person_id` (aus `g.current_user['person_id']`, kann `None` sein). In `month.html` wird dieser Wert als JS-Konstante ausgegeben:
```jinja
const CURRENT_USER_PERSON_ID = {{ current_user_person_id | tojson }};
```

`addRow(day='', desc='', pid='', ...)` und `addIncomeRow(...)`: an jeder Stelle, an der diese Funktionen für eine **neue, leere** Zeile aufgerufen werden (Tab-Taste, „+ Zeile"-Knopf — nicht beim Nachladen bestehender, gespeicherter Zeilen aus dem Server), wird `pid` auf `CURRENT_USER_PERSON_ID ?? ''` gesetzt statt auf `''`. Bestehende Zeilen (aus `EXISTING`/Server-Daten geladen) übergeben weiterhin ihre eigene, gespeicherte `person_id` unverändert — diese Änderung betrifft ausschließlich den Default für frisch angelegte, leere Zeilen.

---

## Selbstprüfung

- **Platzhalter-Scan:** keine TBD/TODO — alle Endpunkte, Dateipfade und Feldnamen sind konkret benannt.
- **Konsistenz:** `_overview_daten` liefert exakt die Schlüssel, die `profile()` und `overview()` beide konsumieren; keine widersprüchlichen Feldnamen zwischen den Abschnitten.
- **Scope:** Ein zusammenhängender Umsetzungsplan (fünf klar abgegrenzte Teile, jeder für sich testbar). Keine Aufteilung in separate Specs nötig.
- **Mehrdeutigkeit:** Die Frage „was zeigt die Profil-Seite ohne Verknüpfung" ist explizit als reiner Hinweistext ohne Selbstbedienung festgelegt (Nutzerentscheidung). Die Frage „Wizard-Vorschlagsschritt behalten oder entfernen" ist explizit als „entfernen" festgelegt (Nutzerentscheidung).
