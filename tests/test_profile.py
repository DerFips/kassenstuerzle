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
        # Nicht per blossem '99' pruefen: base.html enthaelt bereits die
        # (unabhaengige) CSS-Regel "z-index: 9999" fuer den Toast-Container,
        # die sonst jeden Test hier spuriously scheitern liesse.
        assert 'Bens Ausgabe' not in html
        assert '99.00' not in html
    finally:
        db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
        db.commit()


def test_profil_verlinkt_von_der_navigation(auth_client):
    # '/' leitet auf '/overview' weiter (main.index) - ohne follow_redirects
    # liefert get() nur die Redirect-Stub-Seite ohne Navigation.
    html = auth_client.get('/', follow_redirects=True).get_data(as_text=True)
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


def test_detaillierte_monatsuebersicht_ist_von_uebersicht_ins_profil_umgezogen(auth_client, db):
    """Der Monat-fuer-Monat-Ausblick mit Abrechnungsstatus (frueher Teil von
    /overview's Personen-Uebersicht) gehoert jetzt exklusiv der eigenen
    Profilseite - /overview zeigt nur noch den schlichten Vergleich aller
    Personen nebeneinander, ohne Monatsdetail."""
    db.execute("INSERT INTO persons (name) VALUES ('Anna')")
    anna = db.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()['id']
    db.execute("INSERT INTO categories (name, color) VALUES ('Essen', '#111111')")
    kat = db.execute("SELECT id FROM categories WHERE name='Essen'").fetchone()['id']
    db.execute("INSERT INTO expenses (year,month,day,description,person_id,category_id,amount)"
               " VALUES (2026,3,1,'Testausgabe',?,?,25)", (anna, kat))
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (anna,))
    db.commit()
    try:
        profil_html = auth_client.get('/profile').get_data(as_text=True)
        assert 'href="/month/2026/3"' in profil_html, (
            'Die detaillierte Monatsuebersicht mit Verlinkung ist auf /profile '
            'nicht vorhanden')
        assert 'id="profileYearAccordion"' in profil_html

        uebersicht_html = auth_client.get('/overview').get_data(as_text=True)
        assert 'id="profileYearAccordion"' not in uebersicht_html
        # base.html bettet das komplette Uebersetzungswoerterbuch als
        # window.I18N in JEDE Seite ein - ein reiner Text-Vergleich auf den
        # uebersetzten Spaltentitel waere deshalb auf jeder Seite ein
        # Fehlalarm. Die verbleibende haushaltsweite Jahresuebersicht (AP5)
        # verlinkt selbst legitim auf /month/... (ohne Abrechnungsspalte),
        # daher zaehlen statt nur auf Anwesenheit zu pruefen: vorher waeren
        # es zwei Verlinkungen (AP5 + der jetzt umgezogene AP6-Block),
        # jetzt darf nur noch die aus AP5 uebrig sein.
        assert uebersicht_html.count('href="/month/2026/3"') == 1, (
            'Auf /overview verlinkt nicht mehr nur die haushaltsweite '
            'Jahresuebersicht auf den Monat - der umgezogene Block scheint '
            'noch (teilweise) vorhanden zu sein')
        assert 'Anna' in uebersicht_html, (
            'Die schlichte Personen-Vergleichstabelle muss auf /overview bleiben')
    finally:
        db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
        db.commit()


def test_kategorie_monats_matrix_ist_von_uebersicht_ins_profil_umgezogen(auth_client, db):
    """Die einklappbare Kategorie-Monats-Matrix mit den drei Reitern
    (Ausgaben/Einnahmen/Gesamt) gehoert nach Nutzerwunsch nun ausschliesslich
    der eigenen Profilseite, nicht mehr /overview."""
    db.execute("INSERT INTO persons (name) VALUES ('Anna')")
    anna = db.execute("SELECT id FROM persons WHERE name='Anna'").fetchone()['id']
    db.execute("INSERT INTO categories (name, color) VALUES ('Essen', '#111111')")
    kat = db.execute("SELECT id FROM categories WHERE name='Essen'").fetchone()['id']
    db.execute("INSERT INTO expenses (year,month,day,description,person_id,category_id,amount)"
               " VALUES (2026,3,1,'Testausgabe',?,?,25)", (anna, kat))
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (anna,))
    db.commit()
    try:
        profil_html = auth_client.get('/profile').get_data(as_text=True)
        assert 'id="profileDetailsSection"' in profil_html, (
            'Die Kategorie-Monats-Matrix ist auf /profile nicht vorhanden')
        assert 'id="profileOvTabs"' in profil_html

        uebersicht_html = auth_client.get('/overview').get_data(as_text=True)
        assert 'id="detailsSection"' not in uebersicht_html, (
            'Die Kategorie-Monats-Matrix ist auf /overview immer noch vorhanden - '
            'sie sollte ausschliesslich auf /profile stehen')
        assert 'id="ovTabs"' not in uebersicht_html
    finally:
        db.execute("UPDATE users SET person_id=NULL WHERE username='admin'")
        db.commit()
