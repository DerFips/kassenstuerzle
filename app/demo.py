"""Demo-Umgebung: Startdaten (`python -m app.demo`) und taeglicher Reset.

Das Modul ist ohne Nebenwirkungen importierbar; create_app() importiert es
erst im Demo-Modus, um einen Zirkelimport zu vermeiden. Alles, was die
Anwendung (app.routes.main) braucht, wird deshalb erst in den Funktionen
importiert.
"""
import calendar
import logging
import os
import random
import signal
from datetime import date, datetime

logger = logging.getLogger(__name__)

DEMO_PASSWORT_STANDARD = 'demo1234'


def demo_reset() -> None:
    """Beendet den Gunicorn-Master, damit Docker den Container neu startet.

    Nur im Container (KASSENSTUERZLE_IM_CONTAINER=1, steht im Dockerfile) -
    bewusst nicht ueber /.dockerenv, damit ein Dev-Container nie getroffen
    wird. Sonst nur eine Warnung: der Job darf nie die Shell eines
    Entwicklers beenden.
    """
    if os.environ.get('KASSENSTUERZLE_IM_CONTAINER') != '1':
        logger.warning('Demo-Reset uebersprungen: Prozess laeuft nicht im '
                       'Container (KASSENSTUERZLE_IM_CONTAINER != 1).')
        return
    master = os.getppid()
    logger.info('Demo-Reset: sende SIGTERM an den Gunicorn-Master (%s).', master)
    os.kill(master, signal.SIGTERM)


def schedule_demo_reset(scheduler, hour: int, minute: int) -> None:
    """Traegt den taeglichen Reset-Job ein (lokale Zeitzone, siehe TZ)."""
    scheduler.add_job(
        demo_reset, 'cron', hour=hour, minute=minute,
        id='demo_reset', replace_existing=True,
    )


# ── Startdaten ───────────────────────────────────────────────────────────────

def _monat_plus(jahr: int, monat: int, delta: int) -> tuple:
    """(Jahr, Monat), `delta` Kalendermonate nach (negativ: vor) dem Monat."""
    index = jahr * 12 + (monat - 1) + delta
    return index // 12, index % 12 + 1


def _letzter_tag(jahr: int, monat: int) -> int:
    return calendar.monthrange(jahr, monat)[1]


# Kategorien: (Name, Farbe, geteilt). Die Anteile der geteilten Kategorien
# stehen in _ANTEILE (Gewichte, keine Prozente - 55/45 ist die Miete).
_KATEGORIEN = [
    ('Miete', '#e76f51', 1),
    ('Lebensmittel', '#2a9d8f', 1),
    ('Strom', '#e9c46a', 1),
    ('Internet', '#4361ee', 1),
    ('Restaurant', '#f4a261', 0),
    ('Mobilität', '#264653', 0),
    ('Freizeit', '#9b5de5', 0),
    ('Versicherungen', '#577590', 0),
    ('Gesundheit', '#e63946', 0),
    ('Kleidung', '#f15bb5', 0),
    ('Handy & Abos', '#00b4d8', 0),
    ('Haushalt & Wohnen', '#8d6e63', 0),
    ('Gehalt', '#198754', 0),
    ('Nebenverdienst', '#20c997', 0),
    ('Erstattung', '#0dcaf0', 0),
]
_ANTEILE = {'Miete': (55, 45), 'Lebensmittel': (50, 50),
            'Strom': (50, 50), 'Internet': (50, 50)}

# Feste Monatsposten: (Tag, Beschreibung, Person, Kategorie, Betrag).
# Betrag None = schwankt (Strom). Wer zahlt, bestimmt auch die Vorlagen.
_FEST = [
    (1, 'Miete', 'Anna', 'Miete', 1180.00),
    (1, 'Fitnessstudio FitX', 'Anna', 'Freizeit', 29.90),
    (3, 'Mobilfunk Telekom', 'Anna', 'Handy & Abos', 24.99),
    (10, 'Netflix', 'Anna', 'Handy & Abos', 12.99),
    (12, 'Rundfunkbeitrag', 'Anna', 'Handy & Abos', 18.36),
    (15, 'Kfz-Versicherung HUK', 'Anna', 'Versicherungen', 74.50),
    (15, 'Haftpflicht und Hausrat', 'Anna', 'Versicherungen', 21.80),
    (20, 'Risikolebensversicherung', 'Anna', 'Versicherungen', 19.90),
    (20, 'Zusatzkrankenversicherung', 'Anna', 'Versicherungen', 49.90),
    (1, 'Garage Stellplatz', 'Anna', 'Mobilität', 45.00),
    (1, 'Deutschlandticket', 'Ben', 'Mobilität', 49.00),
    (3, 'Mobilfunk Vodafone', 'Ben', 'Handy & Abos', 19.99),
    (5, 'Stadtwerke Strom', 'Ben', 'Strom', None),
    (8, 'Internet Telekom', 'Ben', 'Internet', 39.99),
    (20, 'Berufsunfähigkeitsversicherung', 'Ben', 'Versicherungen', 38.40),
    (20, 'Zahnzusatzversicherung', 'Ben', 'Versicherungen', 14.90),
    (14, 'Spotify', 'Ben', 'Handy & Abos', 10.99),
]

# Wiederkehrende Vorlagen: Auswahl aus _FEST (Beschreibungen).
_VORLAGEN = {
    'Anna': ['Miete', 'Fitnessstudio FitX', 'Netflix', 'Kfz-Versicherung HUK'],
    'Ben': ['Deutschlandticket', 'Mobilfunk Vodafone', 'Internet Telekom',
            'Berufsunfähigkeitsversicherung'],
}

_SUPERMAERKTE = ['REWE Markt', 'EDEKA', 'Lidl', 'Aldi Süd', 'Kaufland']
_DROGERIEN = ['dm Drogeriemarkt', 'Rossmann']
_RESTAURANTS = ['Pizzeria Napoli', 'Café Central', 'Brauhaus Zur Linde',
                'Sushi Bar Tokyo', 'Restaurant Il Gabbiano', 'Burgerladen']
_TANKSTELLEN = ['Tanken Aral', 'Tanken Shell', 'Tanken Jet']
_KLEIDUNG = ['H&M', 'Zalando', 'C&A', 'Decathlon', 'Zara']
_FREIZEIT = ['Kino CinemaxX', 'Konzertkarten', 'Buchhandlung Thalia',
             'Schwimmbad Eintritt', 'Steam', 'Escape Room', 'Bowling']
_HAUSHALT = ['Baumarkt Bauhaus', 'IKEA', 'Tedi Haushaltswaren']


def _betrag(rng, unten: float, oben: float, haeufigster=None) -> float:
    """Betrag zwischen `unten` und `oben`; mit `haeufigster` dreieckig
    verteilt (Einkaeufe liegen meist in der Mitte, selten an den Raendern)."""
    if haeufigster is None:
        return round(rng.uniform(unten, oben), 2)
    return round(rng.triangular(unten, oben, haeufigster), 2)


def _buchungen_fuer_monat(rng, jahr: int, monat: int, bis_tag: int,
                          nr: int) -> tuple:
    """Ausgaben und Einnahmen eines Monats als Listen von Tupeln
    (Tag, Beschreibung, Person, Kategorie, Betrag), nie nach `bis_tag`.

    `nr` ist die Nummer des Monats im Zeitraum (0 = aeltester, 5 = aktueller).
    Zaehlt ein Posten pro Monat, wird er im angebrochenen aktuellen Monat
    anteilig gekuerzt, damit dort nicht ein ganzer Monat auf wenige Tage
    faellt. Der Zufall wird immer gezogen, ob der Posten stattfindet oder
    nicht - so bleibt die Folge der Zufallszahlen vom Datum unabhaengig.
    """
    letzter = _letzter_tag(jahr, monat)
    anteil = bis_tag / letzter
    ausgaben, einnahmen = [], []

    def tag():
        return rng.randint(1, bis_tag)

    def anzahl(unten, oben):
        return round(rng.randint(unten, oben) * anteil)

    def wuerfel(wahrscheinlichkeit):
        return rng.random() < wahrscheinlichkeit * anteil

    for t, text, person, kategorie, betrag in _FEST:
        if betrag is None:
            betrag = _betrag(rng, 78, 92)
        if t <= bis_tag:
            ausgaben.append((t, text, person, kategorie, betrag))

    for _ in range(anzahl(9, 10)):
        person = 'Ben' if rng.random() < 0.55 else 'Anna'
        ausgaben.append((tag(), rng.choice(_SUPERMAERKTE), person,
                         'Lebensmittel', _betrag(rng, 25, 115, 68)))
    for _ in range(anzahl(1, 2)):
        person = rng.choice(['Anna', 'Ben'])
        ausgaben.append((tag(), rng.choice(_DROGERIEN), person,
                         'Lebensmittel', _betrag(rng, 12, 38)))
    if wuerfel(0.8):
        person = rng.choice(['Anna', 'Ben'])
        ausgaben.append((tag(), 'Fressnapf Tierbedarf', person, 'Lebensmittel',
                         _betrag(rng, 24, 46)))
    for person in ('Anna', 'Ben'):
        for _ in range(anzahl(2, 3)):
            ausgaben.append((tag(), rng.choice(_RESTAURANTS), person,
                             'Restaurant', _betrag(rng, 22, 68, 42)))
    for _ in range(anzahl(3, 4)):
        ausgaben.append((tag(), rng.choice(_TANKSTELLEN), 'Anna', 'Mobilität',
                         _betrag(rng, 48, 72)))
    for person in ('Anna', 'Ben'):
        for _ in range(anzahl(2, 4)):
            ausgaben.append((tag(), rng.choice(_FREIZEIT), person, 'Freizeit',
                             _betrag(rng, 12, 45)))
        if wuerfel(0.6):
            ausgaben.append((tag(), rng.choice(_KLEIDUNG), person, 'Kleidung',
                             _betrag(rng, 35, 120)))
    if wuerfel(0.4):
        ausgaben.append((tag(), 'Geschenk Geburtstag', rng.choice(['Anna', 'Ben']),
                         'Freizeit', _betrag(rng, 20, 60)))
    if wuerfel(0.3):
        ausgaben.append((tag(), 'Bahn Fernverkehr', 'Ben', 'Mobilität',
                         _betrag(rng, 29, 59)))
    if wuerfel(0.15):
        ausgaben.append((tag(), 'Autowerkstatt', 'Anna', 'Mobilität',
                         _betrag(rng, 80, 150)))
    ausgaben.append((tag(), 'Apotheke', 'Anna', 'Gesundheit', _betrag(rng, 8, 25)))
    if wuerfel(0.6):
        ausgaben.append((tag(), 'Apotheke', 'Ben', 'Gesundheit', _betrag(rng, 8, 25)))
    if wuerfel(0.7):
        ausgaben.append((tag(), 'Friseur', 'Anna', 'Gesundheit', _betrag(rng, 28, 45)))
    if wuerfel(0.15):
        ausgaben.append((tag(), 'Zahnarzt Zuzahlung', 'Anna', 'Gesundheit',
                         _betrag(rng, 40, 90)))
    if wuerfel(0.5):
        ausgaben.append((tag(), rng.choice(_HAUSHALT), rng.choice(['Anna', 'Ben']),
                         'Haushalt & Wohnen', _betrag(rng, 15, 60)))

    # Einmalige Posten (nur in vollen Monaten, nie im aktuellen).
    if nr == 2:
        ausgaben.append((12, 'Ferienwohnung Ostsee', 'Anna', 'Freizeit', 640.00))
    if nr == 0:
        ausgaben.append((9, 'IKEA Sideboard', 'Ben', 'Haushalt & Wohnen', 420.00))
    if nr == 4:
        ausgaben.append((8, 'Retoure Zalando', 'Anna', 'Kleidung', -24.95))

    if 28 <= bis_tag:
        einnahmen.append((28, 'Gehalt Anna', 'Anna', 'Gehalt', 3050.00))
        einnahmen.append((28, 'Gehalt Ben', 'Ben', 'Gehalt', 2680.00))
    if wuerfel(0.4):
        einnahmen.append((rng.randint(min(5, bis_tag), bis_tag), 'Nachhilfe', 'Ben',
                          'Nebenverdienst', _betrag(rng, 120, 280)))
    if nr == 1:
        einnahmen.append((15, 'Steuererstattung', 'Anna', 'Erstattung', 310.00))

    ausgaben = [a for a in ausgaben if a[0] <= bis_tag]
    einnahmen = [e for e in einnahmen if e[0] <= bis_tag]
    ausgaben.sort(key=lambda a: a[0])
    einnahmen.sort(key=lambda e: e[0])
    return ausgaben, einnahmen


# Vermoegen: (Person, Name, Typ). Die Reihenfolge ist die Anlage-Reihenfolge.
_KONTEN = [
    ('Anna', 'Girokonto', 'checking'),
    ('Anna', 'Tagesgeld', 'savings'),
    ('Anna', 'ETF-Depot', 'securities'),
    ('Anna', 'Altersvorsorge', 'pension'),
    ('Anna', 'Kreditkarte', 'credit_card'),
    ('Ben', 'Girokonto', 'checking'),
    ('Ben', 'Tagesgeld', 'savings'),
    ('Ben', 'Aktiendepot', 'securities'),
    ('Ben', 'Studienkredit', 'loan'),
]


def _salden_reihe(rng, person: str, typ: str) -> list:
    """Stand je Monatsende des Zeitraums (sechs Werte; der letzte ist der
    Stand zum Ende des aktuellen Monats, den die Anwendung nie zeigt)."""
    anna = person == 'Anna'
    werte = []
    if typ == 'checking':
        basis = 2300 if anna else 1850
        werte = [basis + rng.uniform(-350, 550) for _ in range(6)]
    elif typ == 'savings':
        start, schritt = (8650, 280) if anna else (4320, 160)
        werte = [start + schritt * k + rng.uniform(-35, 35) for k in range(6)]
    elif typ == 'securities':
        stand, einzahlung = (15200, 150) if anna else (6900, 100)
        for _ in range(6):
            stand = stand * (1 + rng.uniform(-0.01, 0.03)) + einzahlung
            werte.append(stand)
    elif typ == 'pension':
        werte = [21450 + 190 * k + rng.uniform(-30, 30) for k in range(6)]
    elif typ == 'credit_card':
        werte = [rng.uniform(180, 720) for _ in range(6)]
    elif typ == 'loan':
        werte = [9500 - 250 * k for k in range(6)]   # Rate am Monatsersten
    return [round(w, 2) for w in werte]


def seed_demo(conn, today: date | None = None, password: str | None = None) -> dict:
    """Legt zwei Benutzer und sechs Monate Beispieldaten in einer frischen
    Datenbank an.

    `conn` gehoert zu einer Datenbank, die create_app() gerade frisch
    angelegt hat (genau ein Benutzer: der Bootstrap-Admin). Dieser wird zu
    'anna' umgewidmet, 'ben' kommt dazu. Eine Datenbank mit Inhalt wird
    verweigert (RuntimeError), damit der Aufruf nie echte Daten ueberschreibt.

    Zeitraum: die fuenf vollen Monate vor dem aktuellen plus der aktuelle bis
    einschliesslich `today`; kein Datum liegt nach `today`. Mit festem
    Zufallsstart, also bei gleichem `today` immer dieselben Daten.
    Liefert die Anzahlen der angelegten Zeilen.
    """
    from werkzeug.security import generate_password_hash

    heute = today or date.today()
    passwort = password or os.environ.get('DEMO_PASSWORD') or DEMO_PASSWORT_STANDARD

    benutzer = conn.execute('SELECT id, username FROM users').fetchall()
    belegt = sum(conn.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0]
                 for t in ('persons', 'categories', 'expenses', 'income'))
    if belegt or len(benutzer) != 1 or benutzer[0]['username'] != 'admin':
        raise RuntimeError(
            'seed_demo braucht eine frische Datenbank (genau ein Benutzer '
            "'admin', keine Personen, Kategorien oder Buchungen).")
    admin_id = benutzer[0]['id']

    rng = random.Random(42)
    jetzt = datetime.now().isoformat(timespec='seconds')
    monate = [_monat_plus(heute.year, heute.month, nr - 5) for nr in range(6)]

    try:
        # Personen und Konten (1:1)
        person_ids = {}
        for name in ('Anna', 'Ben'):
            person_ids[name] = conn.execute(
                'INSERT INTO persons (name) VALUES (?)', (name,)).lastrowid
        conn.execute(
            'UPDATE users SET username=?, password_hash=?, role=?, is_active=1,'
            ' must_change_password=0, created_at=?, person_id=? WHERE id=?',
            ('anna', generate_password_hash(passwort), 'admin', jetzt,
             person_ids['Anna'], admin_id))
        conn.execute(
            'INSERT INTO users (username, password_hash, role, is_active,'
            ' must_change_password, created_at, person_id, session_epoch)'
            ' VALUES (?,?,?,1,0,?,?,0)',
            ('ben', generate_password_hash(passwort), 'member', jetzt,
             person_ids['Ben']))
        conn.execute("INSERT INTO app_meta (key, value) VALUES ('profiles_setup_done','1')"
                     " ON CONFLICT(key) DO UPDATE SET value='1'")

        # Kategorien und Aufteilung
        kat = {}
        for name, farbe, geteilt in _KATEGORIEN:
            kat[name] = conn.execute(
                'INSERT INTO categories (name, color, is_split) VALUES (?,?,?)',
                (name, farbe, geteilt)).lastrowid
        for name, (anna, ben) in _ANTEILE.items():
            conn.executemany(
                'INSERT INTO category_splits (category_id, person_id, share) VALUES (?,?,?)',
                [(kat[name], person_ids['Anna'], anna), (kat[name], person_ids['Ben'], ben)])

        # Buchungen
        n_ausgaben = n_einnahmen = 0
        for nr, (jahr, monat) in enumerate(monate):
            bis_tag = heute.day if nr == 5 else _letzter_tag(jahr, monat)
            ausgaben, einnahmen = _buchungen_fuer_monat(rng, jahr, monat, bis_tag, nr)
            conn.executemany(
                'INSERT INTO expenses (year, month, day, description, person_id,'
                ' category_id, amount) VALUES (?,?,?,?,?,?,?)',
                [(jahr, monat, t, text, person_ids[p], kat[k], b)
                 for t, text, p, k, b in ausgaben])
            conn.executemany(
                'INSERT INTO income (year, month, day, description, person_id,'
                ' category_id, amount) VALUES (?,?,?,?,?,?,?)',
                [(jahr, monat, t, text, person_ids[p], kat[k], b)
                 for t, text, p, k, b in einnahmen])
            n_ausgaben += len(ausgaben)
            n_einnahmen += len(einnahmen)

        # Abrechnung: alle Monate ausser den letzten beiden. Das Format von
        # settled_at ist das von settle_month(); der Zeitpunkt liegt in den
        # ersten Tagen des Folgemonats (also nie in der Zukunft).
        abgerechnet = monate[:4]
        for jahr, monat in abgerechnet:
            fy, fm = _monat_plus(jahr, monat, 1)
            conn.execute(
                'INSERT INTO month_status (year, month, settled, settled_at)'
                ' VALUES (?,?,1,?)',
                (jahr, monat, '%02d.%02d.%d %02d:%02d' % (
                    rng.randint(1, 4), fm, fy, rng.randint(17, 21), rng.randint(0, 59))))

        # Wiederkehrende Vorlagen
        fest = {(p, text): (t, k, b) for t, text, p, k, b in _FEST}
        for person, texte in _VORLAGEN.items():
            for text in texte:
                t, k, b = fest[(person, text)]
                conn.execute(
                    'INSERT INTO recurring_expenses (day, description, person_id,'
                    " category_id, amount, frequency, start_month, active)"
                    " VALUES (?,?,?,?,?,'monthly',1,1)",
                    (t, text, person_ids[person], kat[k], b))

        # Vermoegen: Stand zu jedem Monatsende der fuenf vollen Monate plus
        # ein Stand fuer heute (das Ende des aktuellen Monats liegt in der
        # Zukunft). Schulden stehen als positive Betraege da.
        anteil_heute = heute.day / _letzter_tag(heute.year, heute.month)
        n_konten = n_salden = 0
        for person, name, typ in _KONTEN:
            konto_id = conn.execute(
                'INSERT INTO wealth_accounts (person_id, name, type) VALUES (?,?,?)',
                (person_ids[person], name, typ)).lastrowid
            reihe = _salden_reihe(rng, person, typ)
            eintraege = [
                ('%04d-%02d-%02d' % (j, m, _letzter_tag(j, m)), reihe[nr])
                for nr, (j, m) in enumerate(monate[:5])]
            if typ in ('savings', 'securities', 'pension'):
                # waechst stetig: Stand heute liegt anteilig zwischen den
                # Monatsenden
                stand = reihe[4] + (reihe[5] - reihe[4]) * anteil_heute
            else:
                stand = reihe[5]
            eintraege.append((heute.isoformat(), round(stand, 2)))
            conn.executemany(
                'INSERT INTO wealth_balances (account_id, balance_date, balance)'
                ' VALUES (?,?,?)', [(konto_id, d, b) for d, b in eintraege])
            n_konten += 1
            n_salden += len(eintraege)

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {'persons': 2, 'users': 2, 'expenses': n_ausgaben,
            'income': n_einnahmen, 'months_settled': len(abgerechnet),
            'accounts': n_konten, 'balances': n_salden}


def main() -> None:
    """`python -m app.demo`: frische Demo-Datenbank anlegen.

    Laeuft bei jedem Container-Start vor Gunicorn. Ein Fehler beendet den
    Prozess mit Exit-Code != 0, damit der Container nicht mit leerer oder
    halber Datenbank hochkommt.
    """
    pfad = os.environ.get('DATABASE_PATH', '').strip()
    if not pfad:
        # Ohne ausdruecklichen Pfad waere es die Standard-Datenbank (data/) -
        # und die wird hier geloescht.
        raise SystemExit('DATABASE_PATH ist nicht gesetzt: app.demo loescht die '
                         'Datenbank an diesem Pfad und startet nie auf der '
                         'Standard-Datenbank.')
    pfad = os.path.abspath(pfad)

    os.environ['DISABLE_SCHEDULER'] = '1'
    # Der Bootstrap-Admin bekommt sonst ein Zufallspasswort, das im Log
    # landet. seed_demo ersetzt es ohnehin gleich durch das Demo-Passwort.
    if not os.environ.get('ADMIN_PASSWORD'):
        os.environ['ADMIN_PASSWORD'] = (os.environ.get('DEMO_PASSWORD')
                                        or DEMO_PASSWORT_STANDARD)

    for endung in ('', '-wal', '-shm'):
        try:
            os.remove(pfad + endung)
        except FileNotFoundError:
            pass

    from . import create_app
    from .routes.main import get_db

    app = create_app()
    with app.app_context():
        conn = get_db()
        try:
            zusammenfassung = seed_demo(conn)
        finally:
            conn.close()
    print('Demo-Daten angelegt: ' + ', '.join(
        '%s=%s' % (k, v) for k, v in zusammenfassung.items()))


if __name__ == '__main__':
    main()
