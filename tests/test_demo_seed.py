"""Demo-Umgebung: Startdaten (`python -m app.demo`, `seed_demo`).

seed_demo braucht ein echtes Schema und darf die gemeinsame Test-Datenbank
nicht verschmutzen. Deshalb laeuft jeder Aufruf in einem eigenen Prozess mit
einem DATABASE_PATH im tmp_path (Muster:
tests/test_backup_restore_sicherheit.py::
test_erlaubte_objekte_bleiben_mit_init_db_im_gleichlauf). Die Tests lesen die
erzeugte Datenbank danach mit sqlite3.
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash

REPO = Path(__file__).resolve().parents[1]
HEUTE = date(2026, 10, 24)
PASSWORT = 'demo1234'

# Das Skript legt die App an, befuellt die Datenbank mit seed_demo und rechnet
# danach ueber allen Demo-Daten (Abrechnung, Vermoegen, Uebersicht), damit ein
# Fehler in den Rechenfunktionen im Unterprozess auffaellt.
_SKRIPT = '''
import json, os
from datetime import date
from app import create_app
from app.routes import main as m
from app.demo import seed_demo

app = create_app()
with app.app_context():
    conn = m.get_db()
    zusammenfassung = seed_demo(conn, today=date.fromisoformat(os.environ['SEED_TODAY']))
    monate = [(r['year'], r['month']) for r in conn.execute(
        'SELECT DISTINCT year, month FROM expenses ORDER BY year, month')]
    abrechnung = {}
    for jahr, monat in monate:
        s = m.calculate_settlement(jahr, monat, conn)
        abrechnung['%d-%02d' % (jahr, monat)] = {
            'is_settled': s['is_settled'],
            'balances': s['balances'],
        }
    vermoegen = {}
    for r in conn.execute('SELECT id, name FROM persons ORDER BY name').fetchall():
        w = m._wealth_data(conn, r['id'])
        vermoegen[r['name']] = {'net_worth': w['net_worth'], 'series': len(w['series']),
                                'accounts': len(w['accounts'])}
    uebersicht = m._overview_daten(conn)
    conn.close()
print(json.dumps({'summary': zusammenfassung, 'settlement': abrechnung,
                  'wealth': vermoegen, 'overview_keys': len(uebersicht)}))
'''


def _umgebung(db_path):
    umgebung = dict(os.environ)
    umgebung.update(DATABASE_PATH=str(db_path), ADMIN_PASSWORD='egal',
                    DISABLE_SCHEDULER='1', DISABLE_RATE_LIMIT='1',
                    ALLOW_UNENCRYPTED='1')
    umgebung.pop('DB_ENCRYPTION_KEY', None)
    umgebung.pop('DEMO_PASSWORD', None)
    umgebung.pop('DEMO_MODE', None)
    return umgebung


def _seed(tmp_path, today, name='seed'):
    """Legt eine frische Demo-Datenbank an: (db_path, Ausgabe des Skripts)."""
    ziel = tmp_path / name / 'k.db'
    ziel.parent.mkdir(parents=True, exist_ok=True)
    umgebung = _umgebung(ziel)
    umgebung['SEED_TODAY'] = today.isoformat()
    lauf = subprocess.run([sys.executable, '-c', _SKRIPT], env=umgebung,
                          cwd=str(REPO), capture_output=True, text=True)
    assert lauf.returncode == 0, lauf.stderr
    return ziel, json.loads(lauf.stdout.strip().splitlines()[-1])


def _abfrage(db_path, sql, args=()):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def _monat_vor(heute, delta):
    """(Jahr, Monat), `delta` Kalendermonate vor `heute`."""
    index = heute.year * 12 + heute.month - 1 - delta
    return index // 12, index % 12 + 1


def _datum(zeile):
    return date(zeile['year'], zeile['month'], zeile['day'])


@pytest.fixture(scope='module')
def demo(tmp_path_factory):
    """Eine gemeinsame Demo-Datenbank (today=2026-10-24) fuer die Tests, die
    kein anderes Datum brauchen."""
    db_path, ausgabe = _seed(tmp_path_factory.mktemp('demo'), HEUTE)
    return {'db': db_path, 'out': ausgabe}


def test_zwei_konten_mit_rollen_und_verknuepfung(demo):
    benutzer = {r['username']: r for r in _abfrage(
        demo['db'], 'SELECT * FROM users ORDER BY username')}
    assert set(benutzer) == {'anna', 'ben'}
    assert benutzer['anna']['role'] == 'admin'
    assert benutzer['ben']['role'] == 'member'
    personen = {r['id']: r['name'] for r in _abfrage(demo['db'], 'SELECT * FROM persons')}
    assert len(personen) == 2
    verknuepft = {benutzer['anna']['person_id'], benutzer['ben']['person_id']}
    assert verknuepft == set(personen), 'jedes Konto braucht seine eigene Person'
    for b in benutzer.values():
        assert b['must_change_password'] == 0
        assert b['is_active'] == 1
        assert check_password_hash(b['password_hash'], PASSWORT)
    meta = _abfrage(demo['db'], "SELECT value FROM app_meta WHERE key='profiles_setup_done'")
    assert meta and meta[0]['value'] == '1'
    s = demo['out']['summary']
    assert (s['persons'], s['users']) == (2, 2)


def test_passwort_aus_parameter(tmp_path):
    """Das Passwort laesst sich ueberschreiben (DEMO_PASSWORD bzw. Parameter)."""
    ziel = tmp_path / 'pw' / 'k.db'
    ziel.parent.mkdir(parents=True)
    umgebung = _umgebung(ziel)
    umgebung['SEED_TODAY'] = HEUTE.isoformat()
    quelle = _SKRIPT.replace("today=date.fromisoformat(os.environ['SEED_TODAY'])",
                             "today=date.fromisoformat(os.environ['SEED_TODAY']),"
                             " password='anderes-pw-7'")
    lauf = subprocess.run([sys.executable, '-c', quelle], env=umgebung,
                          cwd=str(REPO), capture_output=True, text=True)
    assert lauf.returncode == 0, lauf.stderr
    for r in _abfrage(ziel, 'SELECT password_hash FROM users'):
        assert check_password_hash(r['password_hash'], 'anderes-pw-7')
        assert not check_password_hash(r['password_hash'], PASSWORT)


def test_zeitraum_sechs_monate_ohne_zukunft(demo):
    db = demo['db']
    ausgaben = _abfrage(db, 'SELECT * FROM expenses')
    einnahmen = _abfrage(db, 'SELECT * FROM income')
    assert ausgaben and einnahmen
    start = _monat_vor(HEUTE, 5)
    assert start == (2026, 5)
    for tabelle in (ausgaben, einnahmen):
        assert min((z['year'], z['month']) for z in tabelle) == start
        assert max(_datum(z) for z in tabelle) <= HEUTE
    salden = _abfrage(db, 'SELECT balance_date FROM wealth_balances')
    assert salden
    assert max(s['balance_date'] for s in salden) <= HEUTE.isoformat()
    assert min(s['balance_date'] for s in salden) >= '2026-05-01'


def test_betraege_im_plausiblen_rahmen(demo):
    db = demo['db']
    ausgaben = _abfrage(db, 'SELECT * FROM expenses')
    einnahmen = _abfrage(db, 'SELECT * FROM income')
    assert all(a['amount'] <= 1200 for a in ausgaben)
    negativ = [a for a in ausgaben if a['amount'] < 0]
    assert len(negativ) == 1, 'genau eine Rueckbuchung'
    assert -50 <= negativ[0]['amount'] < 0
    assert all(0 < e['amount'] <= 3100 for e in einnahmen)

    aktuell = (HEUTE.year, HEUTE.month)
    for delta in range(1, 6):          # die fuenf vollen Monate vor dem aktuellen
        jahr, monat = _monat_vor(HEUTE, delta)
        a = sum(z['amount'] for z in ausgaben if (z['year'], z['month']) == (jahr, monat))
        e = sum(z['amount'] for z in einnahmen if (z['year'], z['month']) == (jahr, monat))
        assert 3000 <= a <= 4200, (jahr, monat, a)
        assert 5000 <= e <= 6500, (jahr, monat, e)

    # mindestens vier Ausgaben je Person und Monat (auch im aktuellen Monat)
    personen = [r['id'] for r in _abfrage(db, 'SELECT id FROM persons')]
    for delta in range(0, 6):
        jahr, monat = _monat_vor(HEUTE, delta)
        for pid in personen:
            n = sum(1 for z in ausgaben if (z['year'], z['month'], z['person_id'])
                    == (jahr, monat, pid))
            assert n >= 4, (jahr, monat, pid, n)
    assert aktuell == _monat_vor(HEUTE, 0)


def test_kategorien_splits_und_abrechnung(demo):
    db = demo['db']
    kategorien = {r['name']: r for r in _abfrage(db, 'SELECT * FROM categories')}
    geteilt = ['Miete', 'Lebensmittel', 'Strom', 'Internet']
    privat = ['Restaurant', 'Mobilität', 'Freizeit', 'Versicherungen',
              'Gesundheit', 'Kleidung']
    for name in geteilt:
        assert kategorien[name]['is_split'] == 1, name
        splits = _abfrage(db, 'SELECT * FROM category_splits WHERE category_id=?',
                          (kategorien[name]['id'],))
        assert len(splits) == 2, name
    for name in privat + ['Gehalt', 'Nebenverdienst', 'Erstattung']:
        assert name in kategorien, name
    for name in privat:
        assert kategorien[name]['is_split'] == 0, name
    miete = {r['person_id']: r['share'] for r in _abfrage(
        db, 'SELECT * FROM category_splits WHERE category_id=?', (kategorien['Miete']['id'],))}
    assert sorted(miete.values()) == [45, 55]
    anna = _abfrage(db, "SELECT person_id FROM users WHERE username='anna'")[0]['person_id']
    assert miete[anna] == 55

    # genau die Monate aelter als die letzten beiden sind abgerechnet
    erwartet = {_monat_vor(HEUTE, d) for d in range(2, 6)}
    status = _abfrage(db, 'SELECT * FROM month_status')
    abgerechnet = {(r['year'], r['month']) for r in status if r['settled'] == 1}
    assert abgerechnet == erwartet
    for r in status:
        if r['settled'] == 1:
            assert r['settled_at'] and len(r['settled_at']) == len('01.01.2026 12:00')
    out = demo['out']
    assert out['summary']['months_settled'] == 4
    ist = {k: v['is_settled'] for k, v in out['settlement'].items()}
    assert sorted(k for k, v in ist.items() if v) == sorted(
        '%d-%02d' % mon for mon in erwartet)
    assert out['overview_keys'] > 0

    # Kategorien passen zur zahlenden Person: Handy, Miete, Strom ...
    zahler = {r['description']: r['person'] for r in _abfrage(
        db, 'SELECT DISTINCT e.description, p.name person FROM expenses e'
            ' JOIN persons p ON p.id=e.person_id')}
    assert zahler['Miete'] == 'Anna'
    assert zahler['Deutschlandticket'] == 'Ben'


def test_wiederkehrende_vorlagen(demo):
    for r in _abfrage(demo['db'], 'SELECT person_id, COUNT(*) n FROM recurring_expenses'
                                  ' GROUP BY person_id'):
        assert 3 <= r['n'] <= 5
    assert len(_abfrage(demo['db'], 'SELECT DISTINCT person_id FROM recurring_expenses')) == 2


def test_vermoegen_je_person(demo):
    db = demo['db']
    typen = {'checking': 'asset', 'savings': 'asset', 'securities': 'asset',
             'pension': 'asset', 'loan': 'debt', 'credit_card': 'debt'}
    for r in _abfrage(db, 'SELECT id, name FROM persons'):
        konten = _abfrage(db, 'SELECT * FROM wealth_accounts WHERE person_id=?', (r['id'],))
        assert len(konten) >= 3, r['name']
        assert any(typen.get(k['type']) == 'debt' for k in konten), 'jede Person hat eine Schuld'
        for k in konten:
            salden = _abfrage(db, 'SELECT * FROM wealth_balances WHERE account_id=?'
                                  ' ORDER BY balance_date', (k['id'],))
            assert len(salden) >= 6, (r['name'], k['name'], len(salden))
            assert salden[-1]['balance_date'] <= HEUTE.isoformat()
            assert all(s['balance'] > 0 for s in salden), (
                'Schulden und Guthaben sind positive Betraege', k['name'])
    wealth = demo['out']['wealth']
    assert set(wealth) == {'Anna', 'Ben'}
    for name, w in wealth.items():
        assert w['net_worth'] > 0, name
        assert w['series'] >= 6, name


def test_studienkredit_sinkt_um_etwa_250_pro_monat(demo):
    ben = _abfrage(demo['db'], "SELECT person_id FROM users WHERE username='ben'")[0]['person_id']
    konto = _abfrage(demo['db'], "SELECT id FROM wealth_accounts WHERE person_id=?"
                                 " AND type='loan'", (ben,))
    assert len(konto) == 1
    salden = [s['balance'] for s in _abfrage(
        demo['db'], 'SELECT balance FROM wealth_balances WHERE account_id=?'
                    ' ORDER BY balance_date', (konto[0]['id'],))]
    schritte = [a - b for a, b in zip(salden, salden[1:])]
    assert all(240 <= s <= 260 for s in schritte), schritte


def test_vermoegen_waechst_nicht_sprunghaft(demo):
    """Anlagekonten (Tagesgeld, Depot, Altersvorsorge) aendern sich von
    Monatsende zu Monatsende um weniger als 8 Prozent."""
    for k in _abfrage(demo['db'], "SELECT id, name FROM wealth_accounts"
                                  " WHERE type IN ('savings','securities','pension')"):
        salden = [s['balance'] for s in _abfrage(
            demo['db'], 'SELECT balance FROM wealth_balances WHERE account_id=?'
                        ' ORDER BY balance_date', (k['id'],))]
        for a, b in zip(salden, salden[1:]):
            assert abs(b - a) / a < 0.08, (k['name'], salden)


def test_geldboerse_leer(demo):
    for r in _abfrage(demo['db'], 'SELECT wallet_start_date, wallet_start_amount FROM persons'):
        assert not r['wallet_start_date']
        assert not r['wallet_start_amount']
    assert _abfrage(demo['db'], 'SELECT COUNT(*) n FROM expenses WHERE is_cash=1')[0]['n'] == 0


@pytest.mark.parametrize('heute', [date(2026, 3, 1), date(2026, 1, 31),
                                   date(2026, 2, 28), date(2028, 2, 29),
                                   date(2027, 1, 1)])
def test_grenzdaten(tmp_path, heute):
    db, ausgabe = _seed(tmp_path, heute, name=heute.isoformat())
    start = _monat_vor(heute, 5)
    ausgaben = _abfrage(db, 'SELECT * FROM expenses')
    einnahmen = _abfrage(db, 'SELECT * FROM income')
    for tabelle in (ausgaben, einnahmen):
        assert tabelle
        for z in tabelle:
            assert _datum(z) <= heute, z       # wirft bei ungueltigem Datum selbst
        monate = {(z['year'], z['month']) for z in tabelle}
        assert min(monate) == start
        assert max(monate) <= (heute.year, heute.month)
    # sechs Kalendermonate: jeder Monat des Zeitraums hat Ausgaben
    erwartet = {_monat_vor(heute, d) for d in range(0, 6)}
    assert {(z['year'], z['month']) for z in ausgaben} == erwartet
    salden = _abfrage(db, 'SELECT balance_date FROM wealth_balances')
    assert max(s['balance_date'] for s in salden) <= heute.isoformat()
    assert min(s['balance_date'] for s in salden) >= '%04d-%02d-01' % start
    assert all(a['amount'] <= 1200 for a in ausgaben)
    assert ausgabe['summary']['months_settled'] == 4


def test_reproduzierbar(tmp_path):
    erste, _ = _seed(tmp_path, HEUTE, name='eins')
    zweite, _ = _seed(tmp_path, HEUTE, name='zwei')
    for sql in ('SELECT * FROM expenses ORDER BY id', 'SELECT * FROM income ORDER BY id',
                'SELECT * FROM wealth_balances ORDER BY account_id, balance_date',
                'SELECT * FROM recurring_expenses ORDER BY id',
                'SELECT * FROM month_status ORDER BY year, month'):
        assert _abfrage(erste, sql) == _abfrage(zweite, sql), sql


def test_seed_verweigert_befuellte_datenbank(tmp_path):
    """Schutz vor versehentlichem Aufruf gegen echte Daten."""
    ziel = tmp_path / 'doppelt' / 'k.db'
    ziel.parent.mkdir(parents=True)
    umgebung = _umgebung(ziel)
    quelle = (
        'from datetime import date\n'
        'from app import create_app\n'
        'from app.routes import main as m\n'
        'from app.demo import seed_demo\n'
        'app = create_app()\n'
        'with app.app_context():\n'
        '    conn = m.get_db()\n'
        '    seed_demo(conn, today=date(2026, 10, 24))\n'
        '    try:\n'
        '        seed_demo(conn, today=date(2026, 10, 24))\n'
        '    except RuntimeError as exc:\n'
        "        print('REFUSED', exc)\n"
    )
    lauf = subprocess.run([sys.executable, '-c', quelle], env=umgebung,
                          cwd=str(REPO), capture_output=True, text=True)
    assert lauf.returncode == 0, lauf.stderr
    assert 'REFUSED' in lauf.stdout
    assert _abfrage(ziel, 'SELECT COUNT(*) n FROM persons')[0]['n'] == 2


def _cli(db_path, **extra):
    umgebung = _umgebung(db_path)
    umgebung.update(extra)
    umgebung.pop('ADMIN_PASSWORD', None)    # main() muss selbst vorsorgen
    return subprocess.run([sys.executable, '-m', 'app.demo'], env=umgebung,
                          cwd=str(REPO), capture_output=True, text=True)


def _zaehler(db_path):
    return {t: _abfrage(db_path, 'SELECT COUNT(*) n FROM %s' % t)[0]['n']
            for t in ('users', 'persons', 'expenses', 'income', 'month_status',
                      'recurring_expenses', 'wealth_accounts', 'wealth_balances',
                      'categories', 'category_splits')}


def test_zweimal_anlegen_ist_wiederholbar(tmp_path):
    """main() zweimal gegen dasselbe DATABASE_PATH: der zweite Lauf geht ueber
    vorhandene Dateien. Das Passwort steht nie im Log."""
    ziel = tmp_path / 'cli' / 'k.db'
    ziel.parent.mkdir(parents=True)
    erster = _cli(ziel)
    assert erster.returncode == 0, erster.stderr
    erste_zahlen = _zaehler(ziel)
    assert erste_zahlen['users'] == 2
    assert erste_zahlen['expenses'] > 100
    # Zwischen den Laeufen kommt eine Besucher-Eingabe dazu - sie muss weg sein.
    con = sqlite3.connect(str(ziel))
    con.execute("INSERT INTO expenses (year, month, day, description, amount)"
                " VALUES (2020, 1, 1, 'Besucher', 1)")
    con.commit()
    con.close()
    zweiter = _cli(ziel)
    assert zweiter.returncode == 0, zweiter.stderr
    assert _zaehler(ziel) == erste_zahlen
    assert _abfrage(ziel, "SELECT COUNT(*) n FROM expenses WHERE description='Besucher'"
                    )[0]['n'] == 0
    assert _abfrage(ziel, 'SELECT COUNT(*) n FROM users')[0]['n'] == 2
    for lauf in (erster, zweiter):
        assert PASSWORT not in lauf.stdout + lauf.stderr
        assert 'Einmal-Passwort' not in lauf.stdout + lauf.stderr
    benutzer = _abfrage(ziel, "SELECT password_hash, must_change_password FROM users"
                              " WHERE username='anna'")[0]
    assert benutzer['must_change_password'] == 0
    assert check_password_hash(benutzer['password_hash'], PASSWORT)


def test_cli_nimmt_demo_passwort_aus_der_umgebung(tmp_path):
    ziel = tmp_path / 'clipw' / 'k.db'
    ziel.parent.mkdir(parents=True)
    lauf = _cli(ziel, DEMO_PASSWORD='Geheim-Demo-9')
    assert lauf.returncode == 0, lauf.stderr
    assert 'Geheim-Demo-9' not in lauf.stdout + lauf.stderr
    for r in _abfrage(ziel, 'SELECT password_hash FROM users'):
        assert check_password_hash(r['password_hash'], 'Geheim-Demo-9')


def test_cli_ohne_database_path_bricht_ab(tmp_path):
    """Ohne ausdrueckliches DATABASE_PATH wuerde main() die Standard-Datenbank
    loeschen (data/) - das darf nie passieren."""
    umgebung = _umgebung(tmp_path / 'x.db')
    umgebung.pop('DATABASE_PATH')
    umgebung['PYTHONPATH'] = str(REPO)
    lauf = subprocess.run([sys.executable, '-m', 'app.demo'], env=umgebung,
                          cwd=str(tmp_path), capture_output=True, text=True)
    assert lauf.returncode != 0
    assert 'DATABASE_PATH' in lauf.stderr + lauf.stdout
