# Profil-Seite, Navigation und Zusammenführung Personen/Benutzer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine „Mein Profil"-Seite pro Benutzer mit persönlicher Ausgaben-/Einnahmen-Auswertung; Personen und Benutzerkonten zu einem zwingend 1:1 gekoppelten Konzept zusammenführen; das „Wer"-Feld bei Ausgaben/Einnahmen serverseitig unveränderbar auf die eingeloggte Person festlegen.

**Architecture:** Die bestehende Berechnung in `overview()` wird in `_overview_daten(conn)` ausgelagert und von einer neuen `/profile`-Route wiederverwendet. Zwei bestehende Endpunkte (`POST /api/users`, `DELETE /api/users/<id>`) werden um die atomare Mitbehandlung der verknüpften Person erweitert. `save_expenses`/`save_income` erzwingen `person_id` serverseitig.

**Tech Stack:** Flask/Jinja2, SQLite über `_open_db`/`get_db`, Chart.js (bereits eingebunden), Bootstrap 5, vanilla JS ohne Framework.

## Global Constraints

- Design-Spec: `docs/superpowers/specs/2026-09-23-profil-und-personen-benutzer-zusammenfuehrung-design.md` — bei Widersprüchen zwischen Plan und Spec gilt die Spec, Abweichungen sind dem Nutzer zu melden.
- Rollenprüfung ausschließlich serverseitig (`@admin_required`/`@login_required`), nie nur durch UI-Verstecken.
- Keine `innerHTML`-Interpolation von Namen ohne `escHtml()` (bestehender Helfer, exportiert als `window.escHtml` aus `base.html`).
- Jeder neue nutzersichtbare String kommt in **beide** Sprachblöcke (`de` und `en`) von `app/translations.py`.
- Niemals `data_/` oder `data/` lesen oder beschreiben in Tests — Tests laufen ausschließlich gegen die Wegwerf-Datenbank aus `tests/conftest.py` (`DATABASE_PATH` zeigt dorthin, `DISABLE_RATE_LIMIT=1` ist gesetzt).
- Kein `git stash`, kein `git checkout --` — nichts, das uncommittete Arbeit außerhalb der eigenen Dateien verwerfen könnte.
- Tests: `.venv-dev/bin/pytest tests/ -q` (aktuell 305 grün — Basiszahl vor Task 1 gegenprüfen und im jeweiligen Taskbericht angeben).
- Bestehende Tabellen `users`/`app_meta` sind **nicht** in `tests/conftest.py`s `_CLEANUP_TABLES` — jeder Test, der einen Benutzer/eine Person anlegt, muss ihn in einem `finally`-Block selbst wieder entfernen (bestehendes Muster, siehe `tests/test_users_api.py`).

---

### Task 1: Geteilte Overview-Berechnung `_overview_daten(conn)`

**Files:**
- Modify: `app/routes/main.py:2614-2857` (Funktion `overview()`)
- Test: `tests/test_overview_daten.py` (neu)

**Interfaces:**
- Produces: `_overview_daten(conn) -> dict` mit den Schlüsseln `structured, inc_structured, gesamt_structured, cats_map, month_keys, settled_months, persons_list, income_map, exp_totals_by_month, chart_data_obj, overview_chart_json, person_totals, person_totals_by_year, months_by_year, grand_income, grand_expense, grand_savings, open_settlements_count`. Reine Umbenennung/Extraktion des bestehenden Codes — keine Logikänderung.

- [ ] **Step 1: Bestehendes Verhalten von `/overview` als Regressionsschutz festhalten**

Vor der Umstellung prüfen, ob ein bestehender Test bereits `/overview` mit echten Daten abdeckt:

```bash
grep -rn "def test.*overview\|'/overview'" tests/ | grep -v test_overview_daten
```

Falls kein Test die gerenderte Seite mit befüllten Daten prüft, folgenden neuen Test zuerst schreiben (er dient als Fang-Netz für den Refactor in Schritt 2):

```python
# tests/test_overview_daten.py
"""Regressionsschutz fuer den _overview_daten()-Refactor (Task 1):
overview() darf nach der Extraktion exakt dieselben Werte rendern wie vorher."""
from datetime import datetime

import pytest


@pytest.fixture()
def testdaten(db):
    db.execute("INSERT INTO persons (name) VALUES ('Anna'), ('Ben')")
    db.execute("INSERT INTO categories (name, color) VALUES ('Essen', '#111111')")
    anna = db.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()['id']
    kat = db.execute("SELECT id FROM categories WHERE name='Essen'").fetchone()['id']
    jahr = datetime.now().year
    db.execute(
        "INSERT INTO expenses (year, month, day, description, person_id, category_id, amount)"
        " VALUES (?, 1, 1, 'Testausgabe', ?, ?, 42.5)", (jahr, anna, kat))
    db.execute(
        "INSERT INTO income (year, month, day, description, person_id, category_id, amount)"
        " VALUES (?, 1, 1, 'Testeinnahme', ?, ?, 100)", (jahr, anna, kat))
    db.commit()
    return {'jahr': jahr, 'anna_name': 'Anna'}


def test_overview_zeigt_person_totale_unveraendert(auth_client, testdaten):
    """Ankerwert VOR dem Refactor: Annas Ausgaben-/Einnahmensumme muss nach
    der Extraktion von _overview_daten() identisch bleiben."""
    res = auth_client.get('/overview')
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # person_totals rendert in der Personen-Uebersichtstabelle - der Name
    # und mindestens einer der beiden Betraege muessen auftauchen.
    assert 'Anna' in html
    assert '42,5' in html.replace('.', ',') or '42.5' in html
```

- [ ] **Step 2: Test ausführen, muss VOR dem Refactor bereits grün sein (Ankerwert)**

Run: `.venv-dev/bin/pytest tests/test_overview_daten.py -v`
Expected: PASS (das ist absichtlich kein TDD-Rot-Schritt — dieser Test verankert bestehendes, schon funktionierendes Verhalten, das der Refactor in Schritt 3 nicht brechen darf).

- [ ] **Step 3: `_overview_daten(conn)` extrahieren**

In `app/routes/main.py`, direkt **vor** `@main_bp.route('/overview')` (Zeile 2614), eine neue Funktion einfügen, die exakt den kompletten Berechnungsblock von `overview()` übernimmt (jede Zeile von der ersten `conn.execute('SELECT DISTINCT year,month ...')`-Query bis unmittelbar vor `conn.close()`/`return render_template(...)`, unverändert kopiert) und am Ende ein Dict zurückgibt:

```python
def _overview_daten(conn):
    """Berechnet alle Kennzahlen der Übersichtsseite für ALLE Personen.

    Ausgelagert aus overview(), damit /profile dieselbe Berechnung
    wiederverwenden kann, gefiltert auf eine einzelne Person. Reine
    Extraktion ohne Logikänderung - overview() selbst verhält sich danach
    identisch (siehe test_overview_daten.py als Ankerwert).
    """
    month_keys = [(r['year'],r['month']) for r in conn.execute(
        'SELECT DISTINCT year,month FROM expenses UNION SELECT DISTINCT year,month FROM income ORDER BY year DESC, month DESC')]
    # [... kompletter, unveraendert kopierter Berechnungsblock aus overview(),
    #      Zeilen 2618 bis vor dem bisherigen "conn.close()" ...]

    return {
        'structured': structured,
        'inc_structured': inc_structured,
        'gesamt_structured': gesamt_structured,
        'cats_map': cats_map,
        'month_keys': month_keys,
        'settled_months': settled_months,
        'persons_list': persons_list,
        'income_map': income_map,
        'exp_totals_by_month': exp_totals_by_month,
        'chart_data_obj': chart_data_obj,
        'overview_chart_json': overview_chart_json,
        'person_totals': person_totals,
        'person_totals_by_year': person_totals_by_year,
        'months_by_year': months_by_year,
        'grand_income': grand_income,
        'grand_expense': grand_expense,
        'grand_savings': grand_savings,
        'open_settlements_count': open_settlements_count,
    }


@main_bp.route('/overview')
@login_required
def overview():
    conn = get_db()
    daten = _overview_daten(conn)
    conn.close()
    return render_template('overview.html', active='overview',
        structured=daten['structured'],
        inc_structured=daten['inc_structured'],
        gesamt_structured=daten['gesamt_structured'],
        all_cats=sorted(daten['cats_map'].items()),
        month_keys=daten['month_keys'],
        month_names=MONTH_NAMES,
        persons_list=daten['persons_list'],
        settled_months=[(y,m) for y,m in daten['settled_months']],
        income_map={(f'{k[0]}-{k[1]}'):v for k,v in daten['income_map'].items()},
        exp_totals_by_month={k:round(v,2) for k,v in daten['exp_totals_by_month'].items()},
        chart_data=daten['chart_data_obj'],
        overview_chart_json=daten['overview_chart_json'],
        grand_income=daten['grand_income'],
        grand_expense=daten['grand_expense'],
        grand_savings=daten['grand_savings'],
        open_settlements_count=daten['open_settlements_count'],
        months_by_year=daten['months_by_year'],
        person_totals=daten['person_totals'],
        person_totals_by_year=daten['person_totals_by_year'])
```

Wichtig beim Kopieren: `conn.close()` steht jetzt **nicht** mehr in `_overview_daten` (der Aufrufer öffnet/schließt die Verbindung), und `all_cats=sorted(cats_map.items())` wird erst beim Rendern gebildet (aus dem zurückgegebenen `cats_map`), nicht innerhalb der Funktion — `cats_map` selbst wandert unverändert mit ins Rückgabe-Dict.

- [ ] **Step 4: Test erneut ausführen — muss weiterhin grün sein**

Run: `.venv-dev/bin/pytest tests/test_overview_daten.py -v`
Expected: PASS (identisches Ergebnis wie Schritt 2 — der Refactor hat nichts verändert).

- [ ] **Step 5: Volle Suite**

Run: `.venv-dev/bin/pytest tests/ -q`
Expected: alle Tests grün, keine neuen Fehlschläge.

- [ ] **Step 6: Commit**

```bash
git add app/routes/main.py tests/test_overview_daten.py
git commit -m "Overview-Berechnung in _overview_daten() ausgelagert (Task 1)"
```

---

### Task 2: „Mein Profil"-Seite mit Navigation

**Files:**
- Create: `app/templates/profile.html`
- Modify: `app/routes/main.py` (neue Route `profile()`, nach `overview()`)
- Modify: `app/templates/base.html` (anklickbarer Benutzername, mobiles Symbol)
- Modify: `app/translations.py` (neue Keys)
- Test: `tests/test_profile.py` (neu)

**Interfaces:**
- Consumes: `_overview_daten(conn)` aus Task 1; `g.current_user['person_id']` aus `login_required` (main.py:297, bereits vorhanden).
- Produces: Route `main.profile` (`GET /profile`), Template `profile.html`.

- [ ] **Step 1: Fehlschlagenden Test für die Route schreiben**

```python
# tests/test_profile.py
"""Tests fuer die neue /profile-Route (Task 2)."""
from werkzeug.security import generate_password_hash


def test_ohne_verknuepfte_person_zeigt_hinweis(auth_client, db):
    """Das admin-Konto aus conftest ist standardmaessig mit keiner Person
    verknuepft (person_id ist NULL) - der Hinweistext muss erscheinen."""
    db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
    db.commit()
    res = auth_client.get('/profile')
    assert res.status_code == 200
    assert 'noch keiner Person zugeordnet' in res.get_data(as_text=True) \
        or 'not yet linked' in res.get_data(as_text=True)


def test_mit_verknuepfter_person_zeigt_eigene_zahlen(auth_client, db):
    """Nur Annas eigene Ausgabe darf auf der Profilseite auftauchen, nicht
    Bens - das ist der eigentliche Zweck der Seite (Personenfilter)."""
    db.execute("INSERT INTO persons (name) VALUES ('Anna'), ('Ben')")
    anna = db.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()['id']
    ben = db.execute("SELECT id FROM persons WHERE name='Ben'").fetchone()['id']
    db.execute("INSERT INTO categories (name, color) VALUES ('Essen', '#111111')")
    kat = db.execute("SELECT id FROM categories WHERE name='Essen'").fetchone()['id']
    db.execute("INSERT INTO expenses (year,month,day,description,person_id,category_id,amount)"
               " VALUES (2026,1,1,'Annas Ausgabe',?,?,17)", (anna, kat))
    db.execute("INSERT INTO expenses (year,month,day,description,person_id,category_id,amount)"
               " VALUES (2026,1,1,'Bens Ausgabe',?,?,99)", (ben, kat))
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (anna,))
    db.commit()
    try:
        res = auth_client.get('/profile')
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert '17' in html
        assert '99' not in html
    finally:
        db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
        db.commit()


def test_profil_verlinkt_von_der_navigation(auth_client):
    html = auth_client.get('/').get_data(as_text=True)
    assert 'href="/profile"' in html


def test_wallet_selbstbedienung_speichert_eigene_zeile(auth_client, csrf, db):
    """Die Geldboerse-Karte auf /profile nutzt den bestehenden
    /api/persons/save-Endpunkt mit genau einer Zeile."""
    db.execute("INSERT INTO persons (name) VALUES ('Anna')")
    anna = db.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()['id']
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (anna,))
    db.commit()
    try:
        res = auth_client.post('/api/persons/save', json={
            'v': 2,
            'rows': [{'id': anna, 'name': 'Anna',
                     'wallet_start_date': '2026-01', 'wallet_start_amount': 250}],
            'deleted_ids': []
        }, headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        row = db.execute("SELECT wallet_start_amount FROM persons WHERE id=?", (anna,)).fetchone()
        assert row['wallet_start_amount'] == 250
    finally:
        db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
        db.commit()
```

- [ ] **Step 2: Test ausführen, muss fehlschlagen**

Run: `.venv-dev/bin/pytest tests/test_profile.py -v`
Expected: FAIL mit `404 NOT FOUND` bzw. `AssertionError` — die Route `/profile` existiert noch nicht, `href="/profile"` steht noch nicht in der Navigation.

- [ ] **Step 3: Route implementieren**

In `app/routes/main.py`, direkt nach der (jetzt in Task 1 umgebauten) `overview()`-Funktion einfügen:

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
        # Verknuepfte Person existiert nicht mehr (ON DELETE SET NULL sollte
        # das eigentlich verhindern) - Schutz gegen einen inkonsistenten
        # Zwischenstand statt eines 500ers.
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
        profile_chart_json=eigenes_chart_json)
```

- [ ] **Step 4: Template erstellen**

`app/templates/profile.html` (neu):

```jinja
{% extends 'base.html' %}
{% block title %}{{ t('profile.title') }} – {{ t('nav.brand') }}{% endblock %}

{% block content %}
<div class="row g-3">
  <div class="col-12">
    <h4 class="mb-3"><i class="bi bi-person-circle me-2"></i>{{ t('profile.title') }}</h4>
  </div>

  {% if unlinked %}
  <div class="col-12">
    <div class="alert alert-info mb-0">{{ t('profile.unlinked_hint') }}</div>
  </div>
  {% else %}
  <div class="col-12">
    <h5 class="mb-1">{{ person_name }}</h5>
  </div>

  <div class="col-12 col-md-4">
    <div class="card shadow-sm text-center h-100">
      <div class="card-body">
        <div class="text-muted small">{{ t('profile.stat_income') }}</div>
        <div class="fs-4 fw-bold text-success">{{ '%.2f'|format(person_totals.inc) }} €</div>
      </div>
    </div>
  </div>
  <div class="col-12 col-md-4">
    <div class="card shadow-sm text-center h-100">
      <div class="card-body">
        <div class="text-muted small">{{ t('profile.stat_expense') }}</div>
        <div class="fs-4 fw-bold text-danger">{{ '%.2f'|format(person_totals.exp) }} €</div>
      </div>
    </div>
  </div>
  <div class="col-12 col-md-4">
    <div class="card shadow-sm text-center h-100">
      <div class="card-body">
        <div class="text-muted small">{{ t('profile.stat_savings') }}</div>
        <div class="fs-4 fw-bold">{{ '%.2f'|format(person_totals.savings) }} €</div>
      </div>
    </div>
  </div>

  <div class="col-12 col-lg-7">
    <div class="card shadow-sm h-100">
      <div class="card-header py-3">
        <h5 class="mb-0"><i class="fas fa-chart-line me-2 text-primary"></i>{{ t('profile.categories_title') }}</h5>
      </div>
      <div class="card-body">
        <canvas id="profileCategoryChart" height="220"></canvas>
      </div>
    </div>
  </div>

  <div class="col-12 col-lg-5">
    <div class="card shadow-sm h-100">
      <div class="card-header py-3">
        <h5 class="mb-0"><i class="bi bi-wallet2 me-2 text-warning"></i>{{ t('profile.wallet_title') }}</h5>
      </div>
      <div class="card-body">
        <div class="mb-2">
          <label class="form-label small text-muted">{{ t('settings.wallet_start_amount_placeholder') }}</label>
          <input type="month" id="profileWalletDate" class="form-control form-control-sm"
                 value="{{ person.wallet_start_date or '' }}">
        </div>
        <div class="mb-2">
          <input type="number" step="0.01" id="profileWalletAmount" class="form-control form-control-sm"
                 value="{{ person.wallet_start_amount or '' }}">
        </div>
        <button class="btn btn-sm btn-warning" id="profileWalletSave">
          <i class="bi bi-save me-1"></i>{{ t('settings.save_persons') }}
        </button>
      </div>
    </div>
  </div>

  <div class="col-12">
    <div class="card shadow-sm">
      <div class="card-header py-3">
        <h5 class="mb-0"><i class="fas fa-calendar me-2 text-warning"></i>{{ t('profile.years_title') }}</h5>
      </div>
      <div class="card-body">
        {% for year, jahresdaten in person_totals_by_year.items() | sort(reverse=true) %}
        <div class="mb-3">
          <div class="fw-semibold">{{ year }} — {{ '%.2f'|format(jahresdaten.savings) }} €</div>
          <table class="table table-sm mb-0">
            <thead class="table-light">
              <tr><th>{{ t('nav.month') }}</th><th class="text-end">{{ t('overview.stat_income') }}</th>
                  <th class="text-end">{{ t('overview.stat_expense') }}</th>
                  <th class="text-end">{{ t('profile.stat_savings') }}</th></tr>
            </thead>
            <tbody>
              {% for m in jahresdaten.months %}
              <tr>
                <td>{{ month_names[m.month - 1] }}</td>
                <td class="text-end">{{ '%.2f'|format(m.inc) }} €</td>
                <td class="text-end">{{ '%.2f'|format(m.exp) }} €</td>
                <td class="text-end">{{ '%.2f'|format(m.savings) }} €</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        {% endfor %}
      </div>
    </div>
  </div>
  {% endif %}
</div>
{% endblock %}

{% block scripts %}
{% if not unlinked %}
<script nonce="{{ csp_nonce() }}">
const PROFILE_CHART = {{ profile_chart_json | tojson }};
(function() {
  const monate = PROFILE_CHART.months.map(m => m.label);
  const kategorien = PROFILE_CHART.categories;
  const datasets = kategorien.map(k => ({
    label: k.name, backgroundColor: k.color,
    data: PROFILE_CHART.months.map(m => (PROFILE_CHART.category_by_month[m.key] || {})[k.name] || 0)
  }));
  new Chart(document.getElementById('profileCategoryChart'), {
    type: 'bar',
    data: { labels: monate, datasets },
    options: { responsive: true, scales: { x: { stacked: true }, y: { stacked: true } } }
  });
})();

document.getElementById('profileWalletSave').addEventListener('click', async () => {
  const res = await fetch('/api/persons/save', {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken},
    body: JSON.stringify({
      v: 2,
      rows: [{
        id: {{ person.id }}, name: {{ person_name | tojson }},
        wallet_start_date: document.getElementById('profileWalletDate').value || null,
        wallet_start_amount: parseFloat(document.getElementById('profileWalletAmount').value) || 0
      }],
      deleted_ids: []
    })
  });
  const d = await res.json();
  showToast(d.success ? t('settings.toast_persons_saved') : t('settings.toast_error', {error: d.error || ''}),
            d.success ? 'success' : 'danger');
});
</script>
{% endif %}
{% endblock %}
```

Hinweis für den Umsetzer: `base.html` stellt `csrfToken` bereits als globale JS-Variable bereit (siehe die bestehenden `fetch(...)`-Aufrufe in `settings.html`, die denselben Namen verwenden) — vor dem Schreiben mit `grep -n "csrfToken" app/templates/base.html` den exakten Variablennamen bestätigen und ggf. anpassen, falls er anders lautet. `Chart` (Chart.js) ist bereits global verfügbar, da `overview.html` es genauso nutzt — mit `grep -n "chart.js\|Chart(" app/templates/overview.html app/templates/base.html` das Einbinde-Script-Tag lokalisieren, falls `profile.html` es selbst referenzieren muss (nicht automatisch über `base.html` verfügbar, falls `overview.html` es selbst einbindet statt `base.html`).

- [ ] **Step 5: Navigation — Desktop**

In `app/templates/base.html`, den bestehenden `<span>` (um Zeile 139-141) ersetzen:

```jinja
      <a href="{{ url_for('main.profile') }}"
         class="text-white-50 small d-none d-lg-inline text-decoration-none profile-link">
        <i class="bi bi-person-circle me-1"></i>{{ session.get('username', '') }}
      </a>
```

Im `<style>`-Block von `base.html` (neben der bestehenden `.bottom-nav`-Regel) ergänzen:
```css
.profile-link:hover { text-decoration: underline !important; }
```

- [ ] **Step 6: Navigation — Mobil**

In `app/templates/base.html`, im `<nav class="bottom-nav d-md-none">`-Block (um Zeile 179-193), vor dem „Einstellungen"-Link ein neues Element einfügen:

```jinja
  <a href="{{ url_for('main.profile') }}" class="{% if active=='profile' %}active{% endif %}">
    <i class="bi bi-person-circle"></i><span>{{ t('nav.profile') }}</span>
  </a>
```

Die bestehende `.bottom-nav a { flex: 1; min-width: 0; ... }`-Regel handhabt ein fünftes Kind bereits automatisch (gleichmäßige Aufteilung, Text kürzt sich per `text-overflow: ellipsis` — keine CSS-Änderung nötig).

- [ ] **Step 7: Übersetzungen**

In `app/translations.py`, im `de`-Block (neben den bestehenden `nav.*`-Keys):
```python
        'nav.profile': 'Mein Profil',
        'profile.title': 'Mein Profil',
        'profile.unlinked_hint': 'Dein Konto ist noch keiner Person zugeordnet. Bitte wende dich an einen Administrator.',
        'profile.stat_income': 'Einnahmen gesamt',
        'profile.stat_expense': 'Ausgaben gesamt',
        'profile.stat_savings': 'Ersparnis',
        'profile.categories_title': 'Meine Kategorien',
        'profile.years_title': 'Nach Jahr',
        'profile.wallet_title': 'Meine Geldbörse',
```
Im `en`-Block, identisch positioniert:
```python
        'nav.profile': 'My Profile',
        'profile.title': 'My Profile',
        'profile.unlinked_hint': 'Your account is not yet linked to a person. Please contact an administrator.',
        'profile.stat_income': 'Total income',
        'profile.stat_expense': 'Total expenses',
        'profile.stat_savings': 'Savings',
        'profile.categories_title': 'My Categories',
        'profile.years_title': 'By Year',
        'profile.wallet_title': 'My Wallet',
```

- [ ] **Step 8: Tests ausführen, müssen jetzt passen**

Run: `.venv-dev/bin/pytest tests/test_profile.py -v`
Expected: PASS (alle vier Tests).

- [ ] **Step 9: Browser-Check (mobil)**

Da die Navigation nun fünf statt vier Elemente hat, im Browser/iOS-Simulator bei schmaler Viewport-Breite (z. B. 375px) prüfen, dass kein Text abgeschnitten wirkt und alle fünf Symbole antippbar bleiben. Bei Bedarf `font-size` in `.bottom-nav a` geringfügig reduzieren (z. B. `.68rem` statt `.70rem`) — nur falls der Browser-Check ein echtes Problem zeigt, nicht vorsorglich.

- [ ] **Step 10: Volle Suite + Commit**

Run: `.venv-dev/bin/pytest tests/ -q` — muss grün sein.

```bash
git add app/routes/main.py app/templates/profile.html app/templates/base.html app/translations.py tests/test_profile.py
git commit -m "Profil-Seite mit Navigation (Task 2)"
```

---

### Task 3: „Wer"-Feld serverseitig sperren

**Files:**
- Modify: `app/routes/main.py` (`save_expenses`, `save_income`, `month_view`)
- Modify: `app/templates/month.html` (`addRow`, `addIncomeRow`, Rendering der „Wer"-Spalte)
- Test: `tests/test_save_expenses.py`, `tests/test_save_income.py` (bestehende Dateien erweitern)

**Interfaces:**
- Consumes: `g.current_user['person_id']`.
- Produces: `save_expenses`/`save_income` ignorieren jedes vom Client gesendete `person_id`-Feld vollständig; `month_view` liefert `current_user_person_name`.

- [ ] **Step 1: Fehlschlagenden Test schreiben (Ausgaben)**

An `tests/test_save_expenses.py` anhängen:

```python
def test_neue_zeile_bekommt_immer_die_eigene_person(auth_client, csrf, db):
    """Serverseitig erzwungen: ein Client kann person_id einer NEUEN Zeile
    nicht selbst bestimmen, egal was er mitschickt."""
    db.execute("INSERT INTO persons (name) VALUES ('Anna'), ('Ben')")
    anna = db.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()['id']
    ben = db.execute("SELECT id FROM persons WHERE name='Ben'").fetchone()['id']
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (anna,))
    db.commit()
    try:
        res = auth_client.post('/api/expenses/save', json={
            'v': 2, 'year': 2026, 'month': 1,
            'rows': [{'day': 1, 'description': 'Test', 'person_id': ben,
                     'category_id': None, 'amount': 10}],
            'deleted_ids': []
        }, headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        row = db.execute("SELECT person_id FROM expenses WHERE description='Test'").fetchone()
        assert row['person_id'] == anna, 'Client-gesendetes person_id wurde uebernommen statt ignoriert'
    finally:
        db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
        db.commit()


def test_bestehende_zeile_behaelt_ihre_person_beim_update(auth_client, csrf, db):
    """Serverseitig erzwungen: person_id einer BESTEHENDEN Zeile aendert
    sich nie, auch wenn der Client versucht, sie umzuschreiben."""
    db.execute("INSERT INTO persons (name) VALUES ('Anna'), ('Ben')")
    anna = db.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()['id']
    ben = db.execute("SELECT id FROM persons WHERE name='Ben'").fetchone()['id']
    cur = db.execute("INSERT INTO expenses (year,month,day,description,person_id,amount)"
                     " VALUES (2026,1,1,'Alt',?,5)", (anna,))
    db.commit()
    rid = cur.lastrowid
    res = auth_client.post('/api/expenses/save', json={
        'v': 2, 'year': 2026, 'month': 1,
        'rows': [{'id': rid, 'day': 1, 'description': 'Alt-geaendert',
                 'person_id': ben, 'category_id': None, 'amount': 5}],
        'deleted_ids': []
    }, headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    row = db.execute("SELECT person_id, description FROM expenses WHERE id=?", (rid,)).fetchone()
    assert row['person_id'] == anna, 'person_id einer Bestandszeile wurde veraendert'
    assert row['description'] == 'Alt-geaendert', 'andere Felder muessen weiterhin aenderbar bleiben'
```

- [ ] **Step 2: Test ausführen, muss fehlschlagen**

Run: `.venv-dev/bin/pytest tests/test_save_expenses.py -k "eigene_person or behaelt_ihre_person" -v`
Expected: FAIL — beide Assertions schlagen fehl, weil `save_expenses` aktuell den vom Client gesendeten Wert übernimmt.

- [ ] **Step 3: `save_expenses` ändern**

In `app/routes/main.py`, in der Funktion `save_expenses` (ab Zeile 1936), die Zeile `pid = int(e['person_id']) if e.get('person_id') else None` entfernen und die beiden Zweige (Update/Insert) wie folgt anpassen:

```python
        cid     = int(e['category_id'])        if e.get('category_id')        else None
        pfpid   = int(e['paid_for_person_id']) if e.get('paid_for_person_id') else None
        is_cash = 1 if e.get('is_cash') else 0
        rfn, _name_abgelehnt = _pruefe_belegname(e.get('receipt_filename'))
        day     = int(e.get('day') or 1)
        desc    = e.get('description', '')

        if rid is not None:
            # "Wer" ist nach dem Anlegen unveraenderbar (Nutzerentscheidung) -
            # person_id fehlt hier deshalb bewusst in der SET-Klausel, ein
            # vom Client mitgesendeter Wert wird vollstaendig ignoriert.
            _alter_beleg = _beleg_von(rid)
            if _alter_beleg and _alter_beleg != rfn and not _name_abgelehnt:
                _belegkandidaten.add(_alter_beleg)
            conn.execute(
                'UPDATE expenses SET day=?, description=?, category_id=?,'
                ' amount=?, is_cash=?, paid_for_person_id=?, receipt_filename=?'
                ' WHERE id=? AND year=? AND month=?',
                (day, desc, cid, amt, is_cash, pfpid, rfn, rid, year, month))
        else:
            # Neue Zeile: "Wer" kommt IMMER aus der Sitzung, nie vom Client -
            # serverseitig erzwungen, nicht nur im Frontend entfernt (sonst
            # liesse sich das per direktem API-Aufruf umgehen).
            pid = g.current_user['person_id']
            cur = conn.execute(
                'INSERT INTO expenses (year,month,day,description,person_id,category_id,'
                'amount,is_cash,paid_for_person_id,receipt_filename) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (year, month, day, desc, pid, cid, amt, is_cash, pfpid, rfn))
            ck = e.get('client_key')
            if ck:
                id_map[str(ck)] = cur.lastrowid
```

- [ ] **Step 4: `save_income` analog ändern**

Gleiches Muster in `save_income` (ab Zeile 2420): `pid = int(i['person_id']) if i.get('person_id') else None` entfernen, das UPDATE ohne `person_id` in der SET-Klausel, das INSERT setzt `pid = g.current_user['person_id']` für neue Zeilen. Exakte Umsetzung analog zu Schritt 3 — Feldnamen dort mit `sed -n '2420,2470p' app/routes/main.py` gegenprüfen, da die genaue Spaltenreihenfolge im UPDATE/INSERT von der in `save_expenses` abweichen kann (Income hat kein `is_cash`/`paid_for_person_id`/`receipt_filename`).

- [ ] **Step 5: Tests ausführen, müssen jetzt passen**

Run: `.venv-dev/bin/pytest tests/test_save_expenses.py tests/test_save_income.py -v`
Expected: PASS, inklusive aller bereits vorher vorhandenen Tests in beiden Dateien (keine Regression).

- [ ] **Step 6: `month_view` liefert den eigenen Personennamen**

Die Route für die Monatsansicht (`grep -n "^def month_view\|@main_bp.route('/month" app/routes/main.py` zur genauen Fundstelle) bekommt einen zusätzlichen Render-Kontext-Wert:

```python
    conn_p = get_db()
    eigene_person_row = None
    if g.current_user['person_id'] is not None:
        eigene_person_row = conn_p.execute(
            'SELECT name FROM persons WHERE id=?', (g.current_user['person_id'],)).fetchone()
    current_user_person_name = eigene_person_row['name'] if eigene_person_row else None
```

Diesen Wert als `current_user_person_name=current_user_person_name` an den bestehenden `render_template('month.html', ...)`-Aufruf anhängen — nicht die bestehende `conn`-Variable der Route überschreiben, falls dort schon eine offene Verbindung existiert; ggf. dieselbe Verbindung wiederverwenden statt `conn_p` neu zu öffnen (mit `grep -n "def month_view" -A 15 app/routes/main.py` den bestehenden Verbindungsaufbau der Route prüfen und `conn_p` durch die vorhandene Variable ersetzen, um keine zweite Verbindung unnötig zu öffnen).

- [ ] **Step 7: `month.html` — „Wer"-Spalte zu Text statt Auswahl**

In `app/templates/month.html`, `fillPersonSelect`-Aufrufe für die „Wer"-Spalte (`tr.querySelector('[name=person_id]')`, Zeilen ~676 und ~712) entfernen; stattdessen beim Zeilenaufbau (`addRow`/`addIncomeRow`) eine reine Text-Zelle einsetzen:

```javascript
// Ersetzt die bisherige <select name="person_id"> Zelle:
const werZelle = tr.querySelector('td.wer-zelle') || document.createElement('td');
werZelle.className = 'wer-zelle text-nowrap';
werZelle.textContent = pid ? (PERSONS.find(p => p.id === pid)?.name || '') : (CURRENT_USER_PERSON_NAME || '');
```

Das exakte Einfügen (welche `<td>` in der Zeilenvorlage ersetzt wird, `tr.innerHTML` vs. gezielter DOM-Zugriff) hängt vom aktuellen Aufbau von `addRow`/`addIncomeRow` ab — vor dem Ändern `sed -n '637,720p' app/templates/month.html` lesen und die bestehende `<select name="person_id">`-Zeile in der Zeilenvorlage gezielt durch eine `<td class="wer-zelle text-nowrap"></td>` ersetzen (leer im Markup, per JS befüllt wie oben), analog zum bestehenden Muster für andere reine Anzeige-Zellen in derselben Datei.

`CURRENT_USER_PERSON_NAME` als neue JS-Konstante am Anfang des `<script>`-Blocks (neben der bestehenden `const PERSONS = ...`-Zeile, main.py:369) ergänzen:
```jinja
const CURRENT_USER_PERSON_NAME = {{ current_user_person_name | tojson }};
```

Bei bestehenden, aus `EXISTING`/Server-Daten geladenen Zeilen bleibt der server-gelieferte `pid`-Wert (bzw. der daraus in `PERSONS` aufgelöste Name) maßgeblich — nur bei einer komplett neuen, leeren Zeile (kein `pid` übergeben) wird `CURRENT_USER_PERSON_NAME` angezeigt.

- [ ] **Step 8: Manuelle Browser-Prüfung**

Gegen eine Wegwerf-Datenbank im Scratchpad-Verzeichnis: neue Zeile anlegen (Tab-Taste und „+ Zeile"-Knopf), „Wer"-Spalte zeigt sofort den eigenen Namen als Text ohne Auswahlmöglichkeit; „für wen" bleibt weiterhin ein editierbares Auswahlfeld. Eine bestehende, gespeicherte Zeile lädt weiterhin ihren ursprünglichen „Wer"-Namen korrekt als Text.

- [ ] **Step 9: Volle Suite + Commit**

Run: `.venv-dev/bin/pytest tests/ -q` — muss grün sein.

```bash
git add app/routes/main.py app/templates/month.html tests/test_save_expenses.py tests/test_save_income.py
git commit -m "Wer-Feld serverseitig auf eingeloggte Person gesperrt (Task 3)"
```

---

### Task 4: Personen/Benutzer zusammenführen — Backend (atomare Erstellung/Löschung)

**Files:**
- Modify: `app/routes/main.py` (`api_users_create`, `api_users_delete`)
- Test: `tests/test_users_api.py` (erweitern)

**Interfaces:**
- Produces: `POST /api/users` legt bei fehlender `person_id` automatisch eine neue `persons`-Zeile mit `name = username` an (atomar). `DELETE /api/users/<id>` löscht die verknüpfte Person in derselben Transaktion mit, falls vorhanden.

- [ ] **Step 1: Fehlschlagenden Test schreiben**

An `tests/test_users_api.py` anhängen:

```python
def test_anlegen_ohne_person_id_erzeugt_person_automatisch(auth_client, csrf, db):
    """Kernaenderung von Task 4: fehlt person_id, entsteht die Person
    automatisch mit demselben Namen wie der Benutzername - atomar mit
    dem Benutzerkonto."""
    try:
        res = _anlegen(auth_client, csrf, username='dora', person_id=None)
        assert res.status_code == 200
        body = res.get_json()
        person = db.execute("SELECT id, name FROM persons WHERE name='dora'").fetchone()
        assert person is not None, 'Es wurde keine Person zum neuen Konto angelegt'
        benutzer = db.execute("SELECT person_id FROM users WHERE username='dora'").fetchone()
        assert benutzer['person_id'] == person['id']
    finally:
        db.execute("DELETE FROM users WHERE username='dora'")
        db.execute("DELETE FROM persons WHERE name='dora'")
        db.commit()


def test_anlegen_mit_vorhandener_person_id_bleibt_moeglich(auth_client, csrf, db):
    """Rueckwaertskompatibilitaet: ein Aufruf MIT person_id legt keine
    zusaetzliche Person an, nutzt die uebergebene."""
    db.execute("INSERT INTO persons (name) VALUES ('Elias')")
    elias = db.execute("SELECT id FROM persons WHERE name='Elias'").fetchone()['id']
    db.commit()
    try:
        res = _anlegen(auth_client, csrf, username='eliaskonto', person_id=elias)
        assert res.status_code == 200
        anzahl_personen_namens_elias = db.execute(
            "SELECT COUNT(*) c FROM persons WHERE name='Elias'").fetchone()['c']
        assert anzahl_personen_namens_elias == 1
        benutzer = db.execute("SELECT person_id FROM users WHERE username='eliaskonto'").fetchone()
        assert benutzer['person_id'] == elias
    finally:
        db.execute("DELETE FROM users WHERE username='eliaskonto'")
        db.execute("DELETE FROM persons WHERE name='Elias'")
        db.commit()


def test_loeschen_entfernt_verknuepfte_person_atomar(auth_client, csrf, db):
    """Kernaenderung von Task 4: Konto loeschen entfernt die verknuepfte
    Person in derselben Aktion mit."""
    try:
        res = _anlegen(auth_client, csrf, username='finn', person_id=None)
        neue_id = res.get_json()['id']
        assert db.execute("SELECT id FROM persons WHERE name='finn'").fetchone() is not None

        res = auth_client.delete(f'/api/users/{neue_id}', headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
        assert db.execute("SELECT id FROM users WHERE id=?", (neue_id,)).fetchone() is None
        assert db.execute("SELECT id FROM persons WHERE name='finn'").fetchone() is None, \
            'Person wurde beim Loeschen des Kontos nicht mitentfernt'
    finally:
        db.execute("DELETE FROM users WHERE username='finn'")
        db.execute("DELETE FROM persons WHERE name='finn'")
        db.commit()


def test_loeschen_ohne_verknuepfte_person_funktioniert_weiterhin(auth_client, csrf, db):
    """Randfall: ein (nach altem Bestand) unverknuepftes Konto laesst sich
    weiterhin loeschen, ohne dass ein Fehler auftritt, weil keine Person
    zum Mitloeschen existiert."""
    try:
        res = _anlegen(auth_client, csrf, username='greta', person_id=None)
        neue_id = res.get_json()['id']
        db.execute("UPDATE users SET person_id=NULL WHERE id=?", (neue_id,))
        db.execute("DELETE FROM persons WHERE name='greta'")
        db.commit()

        res = auth_client.delete(f'/api/users/{neue_id}', headers={'X-CSRF-Token': csrf})
        assert res.status_code == 200
    finally:
        db.execute("DELETE FROM users WHERE username='greta'")
        db.execute("DELETE FROM persons WHERE name='greta'")
        db.commit()
```

- [ ] **Step 2: Test ausführen, muss fehlschlagen**

Run: `.venv-dev/bin/pytest tests/test_users_api.py -k "automatisch or atomar" -v`
Expected: FAIL — `test_anlegen_ohne_person_id_erzeugt_person_automatisch` und `test_loeschen_entfernt_verknuepfte_person_atomar` schlagen fehl (keine Person wird angelegt bzw. mitgelöscht).

- [ ] **Step 3: `api_users_create` erweitern**

In `app/routes/main.py`, Funktion `api_users_create` (Zeile 1653), zwischen der `person_id`-Auflösung und der Vorab-Prüfung einfügen:

```python
    username = (d.get('username') or '').strip()
    role = d.get('role') if d.get('role') in ('admin', 'member') else 'member'
    person_id = int(d['person_id']) if d.get('person_id') else None
    if not username:
        return jsonify(success=False, error=t('srv.missing_data')), 400

    conn = get_db()
    # Zwingende 1:1-Kopplung (Nutzerentscheidung): fehlt person_id, entsteht
    # die Person automatisch mit demselben Namen, atomar mit dem Konto.
    # Ein Aufruf MIT person_id bleibt unveraendert moeglich.
    if person_id is None:
        try:
            cur_p = conn.execute('INSERT INTO persons (name) VALUES (?)', (username,))
            person_id = cur_p.lastrowid
        except sqlite3.IntegrityError:
            return jsonify(success=False, error=t('srv.name_conflict', names=username)), 409

    if conn.execute('SELECT id FROM users WHERE person_id=?', (person_id,)).fetchone():
        return jsonify(success=False, error=t('srv.person_already_linked')), 409

    passwort = _neues_initialpasswort()
    try:
        cur = conn.execute(
            'INSERT INTO users (username, password_hash, role, is_active,'
            ' must_change_password, created_at, person_id, session_epoch)'
            ' VALUES (?,?,?,1,1,?,?,0)',
            (username, generate_password_hash(passwort), role,
             datetime.now().isoformat(timespec='seconds'), person_id))
    except sqlite3.IntegrityError:
        conn.rollback()
        return jsonify(success=False, error=t('srv.username_taken')), 409
    conn.commit()
    return jsonify(success=True, id=cur.lastrowid, password=passwort)
```

Beachten: `conn_pruef` (bisherige zweite Verbindung für die Vorab-Prüfung) entfällt — dieselbe `conn` wird jetzt für Person-Insert, Prüfung und User-Insert verwendet, damit beides in EINER Transaktion läuft und ein `conn.rollback()` bei einem fehlschlagenden User-Insert auch das zuvor angelegte Person-Insert zurücknimmt. Prüfen, ob `t('srv.name_conflict', names=...)` bereits existiert (wird in `save_persons` für Namenskonflikte verwendet, siehe main.py) — falls der Key eine andere Parameterform erwartet, an die bestehende Signatur anpassen statt einen neuen Key anzulegen.

- [ ] **Step 4: `api_users_delete` erweitern**

In `app/routes/main.py`, Funktion `api_users_delete` (Zeile 1763):

```python
@main_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
@admin_required
def api_users_delete(user_id):
    conn = get_db()
    row = conn.execute('SELECT role, person_id FROM users WHERE id=?', (user_id,)).fetchone()
    if row is None:
        return jsonify(success=False, error=t('srv.user_not_found')), 404
    if user_id == session.get('user_id'):
        return jsonify(success=False, error=t('srv.cannot_delete_self')), 400
    if row['role'] == 'admin' and _anzahl_aktiver_admins(conn, ausser_id=user_id) == 0:
        return jsonify(success=False, error=t('srv.last_admin')), 400
    conn.execute('DELETE FROM users WHERE id=?', (user_id,))
    # Zwingende 1:1-Kopplung (Nutzerentscheidung): die verknuepfte Person
    # verschwindet atomar mit dem Konto. Kein Fehler, falls keine
    # verknuepft ist (Bestand aus der Zeit vor dieser Regel).
    if row['person_id'] is not None:
        conn.execute('DELETE FROM persons WHERE id=?', (row['person_id'],))
    conn.commit()
    return jsonify(success=True)
```

- [ ] **Step 5: Tests ausführen, müssen jetzt passen**

Run: `.venv-dev/bin/pytest tests/test_users_api.py -v`
Expected: PASS, alle Tests inklusive der bereits vorher vorhandenen (keine Regression, insbesondere `test_letzter_admin_nicht_deaktivierbar` und die Selbstschutz-Tests).

- [ ] **Step 6: Volle Suite + Commit**

Run: `.venv-dev/bin/pytest tests/ -q` — muss grün sein.

```bash
git add app/routes/main.py tests/test_users_api.py
git commit -m "Person+Konto atomar anlegen und loeschen (Task 4)"
```

---

### Task 5: Personen/Benutzer zusammenführen — Frontend (Settings-Tabelle)

**Files:**
- Modify: `app/templates/settings.html` (Karte „Personen" wird zur kombinierten Karte, Karte „Benutzer" entfällt, Wizard-Vorschlagsschritt entfällt)
- Modify: `app/translations.py` (ein neuer Key)
- Test: manuelle Browser-Prüfung (dieser Task ist primär UI — die serverseitigen Garantien sind bereits durch Task 4 getestet)

**Interfaces:**
- Consumes: `POST /api/users` und `DELETE /api/users/<id>` aus Task 4 (unverändert in ihrer äußeren Signatur, nur intern erweitert).

- [ ] **Step 1: Bestehenden Stand lesen**

Vor jeder Änderung den kompletten aktuellen Inhalt lesen, um zeilengenaue Ersetzungen vorzunehmen:
```bash
sed -n '1,45p;205,230p;610,660p;824,940p' app/templates/settings.html
```

- [ ] **Step 2: „Benutzer"-Karte entfernen**

Den kompletten Block von `<!-- Benutzer -->` (bzw. den `{% if current_role == 'admin' %}`-umschlossenen Kartenblock um Zeile 205-228, exakte Grenzen mit dem Read aus Schritt 1 bestimmen) löschen.

- [ ] **Step 3: Personen-Zeilenvorlage um admin-only Kontofelder erweitern**

Die Jinja-Schleife für `persons` (um Zeile 19-27) um ein admin-only Fragment erweitern, das leer beginnt und per JS befüllt wird (Daten kommen aus `/api/users`, nicht aus dem Jinja-Kontext, da `persons` selbst keine Kontofelder trägt):

```jinja
          {% for p in persons %}
          <div class="input-group mb-2 person-row" data-id="{{ p.id }}">
            <input type="text" class="form-control person-name" value="{{ p.name }}" placeholder="Name">
            <input type="month" class="form-control person-wallet-date"
                   value="{{ p.wallet_start_date or '' }}" title="Bargeld-Startdatum" style="max-width:160px">
            <input type="number" step="0.01" class="form-control person-wallet-amount"
                   value="{{ p.wallet_start_amount or '' }}" placeholder="{{ t('settings.wallet_start_amount_placeholder') }}" style="max-width:130px">
            {% if current_role == 'admin' %}
            <span class="input-group-text person-account-slot bg-white" style="min-width:0;"></span>
            <button class="btn btn-outline-danger remove-person-btn" type="button"><i class="bi bi-trash"></i></button>
            {% endif %}
          </div>
          {% endfor %}
        </div>
        {% if current_role == 'admin' %}
        <button class="btn btn-outline-success btn-sm mt-1" id="add-person-btn">
          <i class="bi bi-plus-circle me-1"></i>{{ t('settings.add_person') }}
        </button>
        {% endif %}
```

`remove-person-btn` ist jetzt admin-only (vorher für alle sichtbar) — konsistent mit der Nutzerentscheidung „Löschen wird admin-only". `.person-account-slot` ist der Einhängepunkt, den `benutzerFelderEinfuegen()` (Schritt 5) pro Zeile befüllt.

- [ ] **Step 4: `add-person-btn` — Verhalten grundlegend ändern**

Den bestehenden Klick-Handler (um Zeile 615) ersetzen: statt einer leeren, später per Bulk-Save zu speichernden Zeile wird jetzt sofort (admin-only) ein Konto+Person atomar angelegt, exakt wie der bisherige `add-user-btn`:

```javascript
document.getElementById('add-person-btn')?.addEventListener('click', async () => {
  const name = prompt(t('settings.person_add_prompt'));
  if (!name) return;
  const d = await (await fetch('/api/users', {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken},
    body: JSON.stringify({username: name.trim(), role: 'member', person_id: null})
  })).json();
  if (!d.success) { showToast(d.error, 'danger'); return; }
  zeigeInitialpasswort(name.trim(), d.password);
  personenNeuLaden();
});
```

`personenNeuLaden()` ist eine neue Hilfsfunktion (Schritt 6), die die komplette Personenliste (inkl. der gerade neu angelegten Zeile) neu vom Server holt und rendert — einfacher und robuster als die neue Zeile per Hand ins bestehende `#persons-list`-DOM einzufügen, da Name **und** Wallet-Felder **und** Kontofelder gleichzeitig konsistent dargestellt werden müssen.

- [ ] **Step 5: `persons-list`-Klick-Handler um Admin-Gate ergänzen**

Der bestehende Handler (um Zeile 623) funktioniert unverändert, da `remove-person-btn` jetzt nur noch im admin-only-Zweig existiert — keine Code-Änderung nötig, nur sicherstellen (manueller Test), dass ein Mitglied ohne Admin-Rechte keinen Löschen-Knopf sieht.

- [ ] **Step 6: Kontofelder pro Zeile einfügen (ersetzt `benutzerZeile`/`benutzerLaden`)**

Die bestehenden Funktionen `benutzerZeile(u)` und `benutzerLaden()` (Zeilen 833-897) komplett ersetzen durch:

```javascript
function kontoFelderEinfuegen(u) {
  const zeile = document.querySelector(`.person-row[data-id="${u.person_id}"]`);
  const slot = zeile?.querySelector('.person-account-slot');
  if (!slot) return;  // Zeile (noch) nicht im DOM, z.B. gerade erst angelegt
  slot.innerHTML = '';

  const badge = document.createElement('span');
  badge.className = 'small text-muted me-1';
  badge.textContent = u.username;
  slot.appendChild(badge);

  const sel = document.createElement('select');
  sel.className = 'form-select form-select-sm d-inline-block w-auto me-1';
  [['member', t('settings.role_member')], ['admin', t('settings.role_admin')]].forEach(([wert, label]) => {
    const o = document.createElement('option');
    o.value = wert; o.textContent = label;
    if (u.role === wert) o.selected = true;
    sel.appendChild(o);
  });
  sel.addEventListener('change', () => benutzerAendern(u.id, {role: sel.value}));
  slot.appendChild(sel);

  const cb = document.createElement('input');
  cb.type = 'checkbox'; cb.className = 'form-check-input me-1'; cb.checked = !!u.is_active;
  cb.title = t('settings.user_active');
  cb.addEventListener('change', () => benutzerAendern(u.id, {is_active: cb.checked ? 1 : 0}));
  slot.appendChild(cb);

  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'btn btn-sm btn-outline-secondary me-1';
  reset.textContent = t('settings.user_reset_pw');
  reset.addEventListener('click', () => passwortZuruecksetzen(u.id, u.username));
  slot.appendChild(reset);

  const del = document.createElement('button');
  del.type = 'button';
  del.className = 'btn btn-sm btn-outline-danger';
  del.innerHTML = '<i class="bi bi-trash"></i>';
  del.title = t('settings.user_delete_confirm', {name: u.username});
  del.addEventListener('click', () => benutzerLoeschen(u.id, u.username));
  slot.appendChild(del);
}

async function personenNeuLaden() {
  // Ersetzt sowohl die alte benutzerLaden() als auch das manuelle
  // Nachfuehren einzelner Zeilen: einmal komplett neu vom Server holen und
  // rendern, damit Name-, Wallet- und Kontofelder immer konsistent sind.
  const [personenRes, benutzerRes] = await Promise.all([
    fetch('/api/persons'), fetch('/api/users')
  ]);
  // Falls es keinen dedizierten GET /api/persons Endpunkt gibt (Personen
  // kommen bisher nur ueber den initialen Jinja-Kontext), stattdessen die
  // Seite neu laden: window.location.reload(). Vor der Umsetzung mit
  // `grep -n "route('/api/persons'" app/routes/main.py` pruefen, ob ein
  // GET-Endpunkt existiert - falls nicht, diese Funktion auf
  // `window.location.reload()` vereinfachen, das ist fuer eine
  // seltene Admin-Aktion (neue Person anlegen) angemessen einfach.
  const benutzer = (await benutzerRes.json()).users || [];
  if (current_role === 'admin') benutzer.forEach(kontoFelderEinfuegen);
}

async function benutzerAendern(id, felder) {
  const d = await (await fetch(`/api/users/${id}`, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken},
    body: JSON.stringify(felder)
  })).json();
  showToast(d.success ? t('settings.user_saved') : d.error, d.success ? 'success' : 'danger');
  personenNeuLaden();
}

async function passwortZuruecksetzen(id, name) {
  if (!confirm(t('settings.user_reset_confirm', {name}))) return;
  const d = await (await fetch(`/api/users/${id}/reset-password`, {
    method: 'POST', headers: {'X-CSRF-Token': csrfToken}
  })).json();
  if (d.success) zeigeInitialpasswort(name, d.password);
  else showToast(d.error, 'danger');
}

async function benutzerLoeschen(id, name) {
  if (!confirm(t('settings.user_delete_confirm', {name}))) return;
  const d = await (await fetch(`/api/users/${id}`, {
    method: 'DELETE', headers: {'X-CSRF-Token': csrfToken}
  })).json();
  showToast(d.success ? t('settings.user_deleted') : d.error, d.success ? 'success' : 'danger');
  personenNeuLaden();
}

if (typeof current_role !== 'undefined' && current_role === 'admin') personenNeuLaden();
```

**Wichtiger Hinweis für den Umsetzer:** Bevor `personenNeuLaden()` wie oben skizziert implementiert wird, mit `grep -n "route('/api/persons'\|GET.*persons" app/routes/main.py` prüfen, ob ein `GET /api/persons`-Endpunkt existiert. Falls nicht (wahrscheinlich, da Personen bisher rein über den Jinja-Kontext beim Seitenaufbau kommen), die Funktion vereinfachen: nach einer erfolgreichen Erstellung/Änderung/Löschung genügt `window.location.reload()` — das ist für diese seltenen Admin-Aktionen angemessen und vermeidet einen unnötigen neuen Endpunkt nur für diesen Zweck. `zeigeInitialpasswort` (bestehende Funktion, Zeile 924) muss dabei ihren Anker `document.getElementById('users-tbody').closest('.card-body')` auf die Personen-Karte ändern, z. B. `document.getElementById('persons-list').closest('.card-body')`, da `users-tbody` nach Schritt 2 nicht mehr existiert.

`PERSONEN_FUER_USER`, `current_role` (JS-globale, aus dem Jinja-Kontext gerendert) — mit `grep -n "PERSONEN_FUER_USER\|const current_role" app/templates/settings.html` die exakte bestehende Deklaration prüfen und wiederverwenden, nicht neu erfinden.

- [ ] **Step 7: Einrichtungsassistent — Vorschlagsschritt entfernen**

`_wizardVorschlaege` und das zugehörige UI-Fragment (Vorschlagsliste für unverknüpfte Personen) entfernen. `wizardLaden()` prüft aktuell `if (!_wizardVorschlaege.length && d.me && d.me.person_id) return;` — nach Entfernen der Vorschlagsliste vereinfacht sich das zu: der Assistent wird nur noch angezeigt, wenn `!d.me || !d.me.person_id` (die eigene Zuordnung fehlt). Schritt 1 des Assistenten (`wizardSchritt1Aufbauen`, `/api/setup/profiles/self`) bleibt vollständig unverändert bestehen.

- [ ] **Step 8: Übersetzung ergänzen**

`app/translations.py`, `de`-Block:
```python
        'settings.person_add_prompt': 'Name der neuen Person (wird auch der Anmeldename)',
```
`en`-Block:
```python
        'settings.person_add_prompt': 'Name of the new person (also used as the login name)',
```

- [ ] **Step 9: Manuelle Browser-Prüfung**

Gegen eine Wegwerf-Datenbank im Scratchpad-Verzeichnis, mit einem Admin-Konto:
1. Neue Person über „+ Person hinzufügen" anlegen → Einmalpasswort erscheint, Zeile zeigt danach Name, Wallet-Felder UND Kontofelder (Benutzername, Rolle, Aktiv, Zurücksetzen, Löschen) in einer Zeile.
2. Als `member`-Konto (nicht Admin) dieselbe Seite öffnen → nur Name + Wallet-Felder sichtbar, kein „+ Person hinzufügen"-Knopf, kein Löschen-Knopf, keine Kontofelder.
3. Eine Zeile löschen (als Admin) → Person **und** Konto verschwinden gemeinsam aus der Liste.
4. Konsole auf Fehler prüfen (insbesondere `t is not defined`, `csrfToken is not defined`, `current_role is not defined`) — falls eine dieser globalen Variablen anders heißt als im Plan angenommen, an die tatsächliche Deklaration in `settings.html`/`base.html` anpassen.

- [ ] **Step 10: Volle Suite + Commit**

Run: `.venv-dev/bin/pytest tests/ -q` — muss grün sein (dieser Task ändert kein Backend-Verhalten, das Bestandstests bricht — Task 4 hat das bereits abgesichert).

```bash
git add app/templates/settings.html app/translations.py
git commit -m "Personen- und Benutzertabelle in den Einstellungen zusammengefuehrt (Task 5)"
```

---

## Selbstprüfung des Plans

- **Spec-Abdeckung:** Alle fünf Teile der Spec (`_overview_daten`, Profil-Seite, Navigation, Zusammenführung, Wer-Feld) sind auf Tasks abgebildet — Teil 2+3 der Spec wurden zu Task 2 zusammengelegt (bilden zusammen ein prüfbares Ergebnis: eine erreichbare Profil-Seite), Teil 4 der Spec wurde in Task 4 (Backend) und Task 5 (Frontend) aufgeteilt.
- **Platzhalter-Scan:** Zwei Stellen im Plan markieren bewusst eine Vorab-Prüfung durch den Umsetzer statt eines geratenen Werts (`csrfToken`-Variablenname in Task 2 Schritt 4, `GET /api/persons`-Endpunkt in Task 5 Schritt 6) — das sind keine Platzhalter für fehlende Spezifikation, sondern Anweisungen, einen im aktuellen Code bereits vorhandenen, aber vom Planersteller nicht abschließend verifizierten Namen zu bestätigen, mit einem konkret spezifizierten Fallback für den Fall, dass er fehlt.
- **Typ-Konsistenz:** `_overview_daten()`s Rückgabeschlüssel werden in Task 1 einmal definiert und in Task 2 identisch wieder verwendet (`daten['structured']`, `daten['person_totals']`, etc.) — keine abweichende Benennung zwischen den Tasks.
- **Reihenfolge:** Task 1 vor Task 2 (Abhängigkeit), Task 4 vor Task 5 (Abhängigkeit). Task 3 ist unabhängig und könnte auch vor Task 2 laufen, bleibt aber an Position 3, da sie inhaltlich am ehesten zu Task 2 passt (beide betreffen „wer bin ich"-bezogene Änderungen an `month.html`/Personen-Anzeige).
