from flask import Flask, render_template, request, jsonify, Response, redirect, url_for
import sqlite3, os
from datetime import datetime

app = Flask(__name__)
DB_PATH = os.environ.get('DATABASE_PATH',
          os.path.join(os.path.dirname(__file__), 'data', 'kassenstuerzle.db'))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
MONTH_NAMES = ['Januar','Februar','März','April','Mai','Juni',
               'Juli','August','September','Oktober','November','Dezember']

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS persons (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        wallet_start_date TEXT DEFAULT NULL, wallet_start_amount REAL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        color TEXT NOT NULL DEFAULT '#6c757d', is_split INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, year INTEGER NOT NULL,
        month INTEGER NOT NULL, day INTEGER NOT NULL, description TEXT DEFAULT '',
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        amount REAL NOT NULL DEFAULT 0, is_cash INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS month_status (
        year INTEGER NOT NULL, month INTEGER NOT NULL,
        settled INTEGER NOT NULL DEFAULT 0, settled_at TEXT,
        PRIMARY KEY (year, month))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS recurring_expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day INTEGER NOT NULL DEFAULT 1, description TEXT NOT NULL DEFAULT '',
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        amount REAL NOT NULL DEFAULT 0, frequency TEXT NOT NULL DEFAULT 'monthly',
        start_month INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS income (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER NOT NULL, month INTEGER NOT NULL,
        day INTEGER NOT NULL DEFAULT 1, description TEXT DEFAULT '',
        person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        amount REAL NOT NULL DEFAULT 0)""")
    for sql in [
        'ALTER TABLE categories ADD COLUMN is_split INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE expenses ADD COLUMN is_cash INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE persons ADD COLUMN wallet_start_date TEXT DEFAULT NULL',
        'ALTER TABLE persons ADD COLUMN wallet_start_amount REAL DEFAULT 0',
    ]:
        try: conn.execute(sql)
        except: pass
    conn.commit(); conn.close()

def build_ov(conn, year, month):
    rows = conn.execute("""
        SELECT p.name as pname, c.name as cname, c.color, SUM(e.amount) as total
        FROM expenses e LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id
        WHERE e.year=? AND e.month=? GROUP BY e.person_id, e.category_id""",
        (year, month)).fetchall()
    pm, cs = {}, {}
    for r in rows:
        pn = r['pname'] or 'Unbekannt'; cn = r['cname'] or 'Sonstige'
        cs[cn] = r['color'] or '#6c757d'
        pm.setdefault(pn, {})[cn] = r['total']
    return pm, sorted(cs.items())

def calculate_settlement(year, month, conn):
    persons = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]
    status = conn.execute('SELECT settled,settled_at FROM month_status WHERE year=? AND month=?', (year,month)).fetchone()
    is_settled = bool(status and status['settled'])
    settled_at = status['settled_at'] if status else None
    if is_settled:
        return {'persons': persons, 'paid': {p:0 for p in persons}, 'fair_share':0,
                'total':0, 'balances': {p:0 for p in persons}, 'transactions': [],
                'cat_details': [], 'is_settled': True, 'settled_at': settled_at}
    if len(persons) < 2: return None
    rows = conn.execute("""
        SELECT p.name as pname, c.id as cid, c.name as cname, c.color, SUM(e.amount) as total
        FROM expenses e LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id
        WHERE e.year=? AND e.month=? AND c.is_split=1
        GROUP BY e.person_id, e.category_id""", (year, month)).fetchall()
    if not rows:
        return {'persons': persons, 'paid': {p: 0 for p in persons},
                'fair_share': 0, 'total': 0, 'balances': {p: 0 for p in persons},
                'transactions': [], 'cat_details': []}
    cat_data = {}
    for r in rows:
        cid = r['cid']
        if cid not in cat_data:
            cat_data[cid] = {'name': r['cname'], 'color': r['color'] or '#6c757d', 'paid_by': {}}
        pn = r['pname'] or 'Unbekannt'
        cat_data[cid]['paid_by'][pn] = r['total']
    paid = {p: 0.0 for p in persons}
    for cd in cat_data.values():
        for pn, amt in cd['paid_by'].items():
            if pn in paid: paid[pn] += amt
    total = sum(paid.values())
    fair_share = total / len(persons)
    balances = {p: round(paid[p] - fair_share, 2) for p in persons}
    creds = sorted([[n, b] for n, b in balances.items() if b > 0.005], key=lambda x: -x[1])
    debts  = sorted([[n,-b] for n, b in balances.items() if b < -0.005], key=lambda x: -x[1])
    transactions = []
    ci = di = 0
    while ci < len(creds) and di < len(debts):
        transfer = min(creds[ci][1], debts[di][1])
        if transfer > 0.005:
            transactions.append({'from': debts[di][0], 'to': creds[ci][0], 'amount': round(transfer, 2)})
        creds[ci][1] -= transfer; debts[di][1] -= transfer
        if creds[ci][1] < 0.005: ci += 1
        if debts[di][1] < 0.005: di += 1
    return {'persons': persons, 'paid': {p: round(v,2) for p,v in paid.items()},
            'fair_share': round(fair_share, 2), 'total': round(total, 2),
            'balances': balances, 'transactions': transactions,
            'cat_details': list(cat_data.values()), 'is_settled': False, 'settled_at': None}


def calculate_category_settlements(year, month, conn):
    """Per split-category settlement: settlement[person] = paid - fair_share.
    Positive = overpaid = receives. Negative = underpaid = pays."""
    persons = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]
    n = len(persons)
    if n < 2:
        return {}
    rows = conn.execute("""
        SELECT c.name as cname, p.name as pname, SUM(e.amount) as total
        FROM expenses e
        LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id
        WHERE e.year=? AND e.month=? AND c.is_split=1
        GROUP BY c.id, e.person_id""", (year, month)).fetchall()
    if not rows:
        return {}
    cat_paid = {}
    for r in rows:
        cn = r['cname'] or 'Sonstige'; pn = r['pname'] or 'Unbekannt'
        cat_paid.setdefault(cn, {})[pn] = r['total']
    result = {}
    for cn, paid_by in cat_paid.items():
        cat_total = sum(paid_by.values())
        fair_share = cat_total / n
        result[cn] = {p: round(paid_by.get(p, 0) - fair_share, 2) for p in persons}
    return result

def calculate_wallet(year, month, conn):
    persons = conn.execute(
        'SELECT * FROM persons WHERE wallet_start_date IS NOT NULL AND wallet_start_date != "" ORDER BY name'
    ).fetchall()
    result = []
    for p in persons:
        sd = p['wallet_start_date'] or ''
        if not sd: continue
        try: sy, sm = map(int, sd.split('-'))
        except: continue
        if (year, month) < (sy, sm): continue
        sa = float(p['wallet_start_amount'] or 0)
        rows = conn.execute(
            'SELECT year, month, SUM(amount) as total FROM expenses'
            ' WHERE person_id=? AND is_cash=1 GROUP BY year, month', (p['id'],)).fetchall()
        prev_cash = curr_cash = 0.0
        for r in rows:
            ry, rm, rt = r['year'], r['month'], float(r['total'] or 0)
            if (ry, rm) == (year, month): curr_cash = rt
            elif (sy, sm) <= (ry, rm) < (year, month): prev_cash += rt
        start_bal = round(sa - prev_cash, 2)
        end_bal   = round(start_bal - curr_cash, 2)
        result.append({'name': p['name'], 'start_balance': start_bal,
                       'cash_expenses': round(curr_cash, 2), 'end_balance': end_bal})
    return result or None



def get_recurring_for_month(year, month, conn):
    rows = conn.execute('SELECT * FROM recurring_expenses WHERE active=1').fetchall()
    result = []
    for r in rows:
        freq, sm = r['frequency'], r['start_month']
        if freq == 'monthly':
            result.append(dict(r))
        elif freq == 'quarterly':
            applicable = {((sm-1+i*3) % 12)+1 for i in range(4)}
            if month in applicable: result.append(dict(r))
        elif freq == 'yearly':
            if month == sm: result.append(dict(r))
    return result

def apply_settlements_to_ov(ov_p, ov_settlements, all_person_names):
    """Net = paid - settlement = fair_share for every person in settled split categories.
    settlement[p] = paid[p] - fair_share  →  net = paid - settlement = fair_share"""
    result = {p: dict(cats) for p, cats in ov_p.items()}
    for cn, person_sett in ov_settlements.items():
        for p in all_person_names:
            paid = ov_p.get(p, {}).get(cn, 0)
            adj  = person_sett.get(p, 0)   # paid - fair_share
            net  = round(paid - adj, 2)    # = fair_share
            if net > 0.005:
                result.setdefault(p, {})[cn] = net
            elif p in result and cn in result[p]:
                del result[p][cn]
    return result

@app.route('/')
def index():
    n = datetime.now()
    return redirect(url_for('month_view', year=n.year, month=n.month))

@app.route('/month/<int:year>/<int:month>')
def month_view(year, month):
    conn = get_db()
    income = [dict(r) for r in conn.execute(
        'SELECT * FROM income WHERE year=? AND month=? ORDER BY day,id', (year,month))]
    income_total  = round(sum(r['amount'] for r in income), 2)
    expense_total_mv = round(conn.execute('SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE year=? AND month=?',(year,month)).fetchone()['t'], 2)
    savings_mv    = round(income_total - expense_total_mv, 2)
    expenses = [dict(r) for r in conn.execute("""
        SELECT e.id, e.day, e.description, e.person_id, e.category_id, e.amount, e.is_cash
        FROM expenses e WHERE e.year=? AND e.month=? ORDER BY e.day, e.id""",
        (year, month))]
    persons    = [dict(r) for r in conn.execute('SELECT * FROM persons ORDER BY name')]
    categories = [dict(r) for r in conn.execute('SELECT * FROM categories ORDER BY name')]
    ov_p, ov_c = build_ov(conn, year, month)
    settlement  = calculate_settlement(year, month, conn)
    ov_settlements = calculate_category_settlements(year, month, conn)
    wallet      = calculate_wallet(year, month, conn)
    ms = conn.execute('SELECT settled,settled_at FROM month_status WHERE year=? AND month=?', (year,month)).fetchone()
    is_settled = bool(ms and ms['settled']); settled_at = ms['settled_at'] if ms else None
    if is_settled and ov_settlements:
        all_pnames = [p['name'] for p in persons]
        ov_p = apply_settlements_to_ov(ov_p, ov_settlements, all_pnames)
    conn.close()
    pm = month-1 if month>1 else 12; py = year if month>1 else year-1
    nm = month+1 if month<12 else 1; ny = year if month<12 else year+1
    return render_template('month.html', active='month',
        year=year, month=month, month_name=MONTH_NAMES[month-1],
        expenses=expenses, persons=persons, categories=categories,
        ov_p=ov_p, ov_c=ov_c, settlement=settlement,
        income=income, income_total=income_total,
        expense_total=expense_total_mv, savings=savings_mv,
        ov_settlements=ov_settlements, wallet=wallet,
        is_settled=is_settled, settled_at=settled_at,
        prev_year=py, prev_month=pm, next_year=ny, next_month=nm)

@app.route('/api/expenses/save', methods=['POST'])
def save_expenses():
    d = request.json; year, month = d['year'], d['month']
    conn = get_db()
    conn.execute('DELETE FROM expenses WHERE year=? AND month=?', (year, month))
    for e in d.get('expenses', []):
        try: amt = float(e.get('amount') or 0)
        except: amt = 0
        if amt == 0: continue
        pid    = int(e['person_id'])   if e.get('person_id')   else None
        cid    = int(e['category_id']) if e.get('category_id') else None
        is_cash = 1 if e.get('is_cash') else 0
        conn.execute(
            'INSERT INTO expenses (year,month,day,description,person_id,category_id,amount,is_cash)'
            ' VALUES (?,?,?,?,?,?,?,?)',
            (year, month, int(e.get('day') or 1), e.get('description',''), pid, cid, amt, is_cash))
    conn.commit()
    ov_p, ov_c = build_ov(conn, year, month)
    settlement  = calculate_settlement(year, month, conn)
    ov_settlements = calculate_category_settlements(year, month, conn)
    wallet      = calculate_wallet(year, month, conn)
    conn.close()
    return jsonify({'success': True,
        'ov_p': {k: dict(v) for k,v in ov_p.items()}, 'ov_c': ov_c,
        'settlement': settlement, 'wallet': wallet})

@app.route('/api/persons/save', methods=['POST'])
def save_persons():
    data = request.json; conn = get_db()
    existing = {r['id'] for r in conn.execute('SELECT id FROM persons')}
    incoming = {p['id'] for p in data if p.get('id')}
    for did in existing - incoming: conn.execute('DELETE FROM persons WHERE id=?', (did,))
    for p in data:
        n  = (p.get('name') or '').strip()
        wd = p.get('wallet_start_date') or None
        wa = float(p.get('wallet_start_amount') or 0)
        if not n: continue
        if p.get('id'):
            conn.execute('UPDATE persons SET name=?, wallet_start_date=?, wallet_start_amount=? WHERE id=?',
                         (n, wd, wa, p['id']))
        else:
            try: conn.execute('INSERT INTO persons (name, wallet_start_date, wallet_start_amount) VALUES (?,?,?)', (n, wd, wa))
            except: pass
    conn.commit()
    result = [dict(r) for r in conn.execute('SELECT * FROM persons ORDER BY name')]
    conn.close(); return jsonify({'success': True, 'persons': result})

@app.route('/api/categories/save', methods=['POST'])
def save_categories():
    data = request.json; conn = get_db()
    existing = {r['id'] for r in conn.execute('SELECT id FROM categories')}
    incoming = {c['id'] for c in data if c.get('id')}
    for did in existing - incoming: conn.execute('DELETE FROM categories WHERE id=?', (did,))
    for c in data:
        n = (c.get('name') or '').strip(); col = c.get('color','#6c757d')
        sp = 1 if c.get('is_split') else 0
        if not n: continue
        if c.get('id'): conn.execute('UPDATE categories SET name=?, color=?, is_split=? WHERE id=?', (n, col, sp, c['id']))
        else:
            try: conn.execute('INSERT INTO categories (name, color, is_split) VALUES (?,?,?)', (n, col, sp))
            except: pass
    conn.commit()
    result = [dict(r) for r in conn.execute('SELECT * FROM categories ORDER BY name')]
    conn.close(); return jsonify({'success': True, 'categories': result})





@app.route('/api/import/csv', methods=['POST'])
def import_csv():
    import csv, io as _io
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Keine Datei'}), 400
    overwrite = request.form.get('overwrite','false') == 'true'
    try:
        content = request.files['file'].stream.read().decode('utf-8-sig')
    except Exception as e:
        return jsonify({'success': False, 'error': f'Lesefehler: {e}'}), 400
    rows = list(csv.reader(_io.StringIO(content), delimiter=';'))
    if len(rows) < 2:
        return jsonify({'success': False, 'error': 'Datei leer oder kein gültiges Format'}), 400
    conn = get_db()
    persons = {r['name']: r['id'] for r in conn.execute('SELECT id,name FROM persons')}
    cats    = {r['name']: r['id'] for r in conn.execute('SELECT id,name FROM categories')}
    # collect affected months for optional overwrite
    affected = set()
    for row in rows[1:]:
        if len(row) < 4: continue
        try: affected.add((int(row[1]), int(row[2])))
        except: pass
    if overwrite:
        for y,m in affected:
            conn.execute('DELETE FROM expenses WHERE year=? AND month=?',(y,m))
            conn.execute('DELETE FROM income   WHERE year=? AND month=?',(y,m))
    imported = ok = 0; skipped = []
    for i, row in enumerate(rows[1:], 2):
        if len(row) < 8: skipped.append(f'Zeile {i}: zu wenig Spalten'); continue
        try:
            typ  = row[0].strip()
            year,month,day = int(row[1]),int(row[2]),int(row[3])
            person = row[4].strip(); category = row[5].strip()
            desc   = row[6].strip()
            amount = float(row[7].replace(',','.').replace(' ',''))
            pid = persons.get(person)
            if typ == 'Ausgabe':
                is_cash = len(row)>8 and row[8].strip()=='Ja'
                cid = cats.get(category)
                conn.execute('INSERT INTO expenses (year,month,day,description,person_id,category_id,amount,is_cash) VALUES (?,?,?,?,?,?,?,?)',
                             (year,month,day,desc,pid,cid,amount,1 if is_cash else 0))
                imported += 1
            elif typ == 'Einnahme':
                conn.execute('INSERT INTO income (year,month,day,description,person_id,amount) VALUES (?,?,?,?,?,?)',
                             (year,month,day,desc,pid,amount))
                imported += 1
            else: skipped.append(f'Zeile {i}: unbekannter Typ "{typ}"')
        except Exception as e: skipped.append(f'Zeile {i}: {e}')
    conn.commit(); conn.close()
    month_links = sorted([{'year':y,'month':m} for y,m in affected], key=lambda x:(x['year'],x['month']))
    return jsonify({'success': True, 'imported': imported,
                    'months': len(affected), 'month_links': month_links,
                    'skipped': len(skipped),
                    'skipped_details': skipped[:10], 'overwrite': overwrite})

@app.route('/api/export/csv')
def export_csv():
    import csv, io
    year  = request.args.get('year',  type=int)
    month = request.args.get('month', type=int)
    conn  = get_db()
    q = """SELECT e.year, e.month, e.day,
               COALESCE(p.name,'')  as person,
               COALESCE(c.name,'Sonstige') as category,
               e.description, e.amount, e.is_cash
        FROM expenses e
        LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id"""
    params = []
    if year and month:   q += ' WHERE e.year=? AND e.month=?'; params=[year,month]
    elif year:           q += ' WHERE e.year=?';               params=[year]
    q += ' ORDER BY e.year,e.month,e.day'
    rows = conn.execute(q, params).fetchall()
    inc_q = 'SELECT i.year,i.month,i.day, COALESCE(p.name,\'\') as person, i.description, i.amount FROM income i LEFT JOIN persons p ON i.person_id=p.id'
    inc_params = []
    if year and month:   inc_q += ' WHERE i.year=? AND i.month=?'; inc_params=[year,month]
    elif year:           inc_q += ' WHERE i.year=?';               inc_params=[year]
    inc_rows = conn.execute(inc_q, inc_params).fetchall()
    conn.close()
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['Typ','Jahr','Monat','Tag','Person','Kategorie','Beschreibung','Betrag (€)','Barzahlung'])
    for r in rows:
        w.writerow(['Ausgabe',r['year'],r['month'],r['day'],r['person'],r['category'],
                    r['description'],str(r['amount']).replace('.',','),'Ja' if r['is_cash'] else 'Nein'])
    for r in inc_rows:
        w.writerow(['Einnahme',r['year'],r['month'],r['day'],r['person'],'',
                    r['description'],str(r['amount']).replace('.',','),''])
    out.seek(0)
    if year and month: fname=f'kassenstuerzle{year}_{month:02d}.csv'
    elif year:         fname=f'kassenstuerzle_{year}.csv'
    else:              fname='kassenstuerzle_export.csv'
    return Response(out.getvalue().encode('utf-8-sig'), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment;filename={fname}'})

@app.route('/api/export/years')
def export_years():
    conn = get_db()
    years = [r['year'] for r in conn.execute('SELECT DISTINCT year FROM expenses ORDER BY year DESC')]
    conn.close()
    return jsonify({'years': years})

@app.route('/api/recurring/save', methods=['POST'])
def save_recurring():
    data = request.json; conn = get_db()
    conn.execute('DELETE FROM recurring_expenses')
    for r in data:
        try: amt = float(r.get('amount') or 0)
        except: amt = 0
        desc = (r.get('description') or '').strip()
        if not desc and amt == 0: continue
        pid  = int(r['person_id'])   if r.get('person_id')   else None
        cid  = int(r['category_id']) if r.get('category_id') else None
        freq = r.get('frequency','monthly')
        sm   = int(r.get('start_month',1)) if freq != 'monthly' else 1
        conn.execute('INSERT INTO recurring_expenses (day,description,person_id,category_id,amount,frequency,start_month,active) VALUES (?,?,?,?,?,?,?,1)',
                     (int(r.get('day') or 1), desc, pid, cid, amt, freq, sm))
    conn.commit()
    result = [dict(r) for r in conn.execute('SELECT * FROM recurring_expenses ORDER BY id')]
    conn.close()
    return jsonify({'success': True, 'recurring': result})

@app.route('/api/recurring/for-month/<int:year>/<int:month>')
def recurring_for_month(year, month):
    conn = get_db()
    result = get_recurring_for_month(year, month, conn)
    conn.close()
    return jsonify({'recurring': result})

@app.route('/api/income/save', methods=['POST'])
def save_income():
    d = request.json; year, month = d['year'], d['month']
    conn = get_db()
    conn.execute('DELETE FROM income WHERE year=? AND month=?', (year, month))
    for e in d.get('income', []):
        try: amt = float(e.get('amount') or 0)
        except: amt = 0
        if amt == 0: continue
        pid = int(e['person_id']) if e.get('person_id') else None
        conn.execute('INSERT INTO income (year,month,day,description,person_id,amount) VALUES (?,?,?,?,?,?)',
                     (year, month, int(e.get('day') or 1), e.get('description',''), pid, amt))
    conn.commit()
    inc_sum = conn.execute('SELECT COALESCE(SUM(amount),0) as t FROM income   WHERE year=? AND month=?',(year,month)).fetchone()['t']
    exp_sum = conn.execute('SELECT COALESCE(SUM(amount),0) as t FROM expenses WHERE year=? AND month=?',(year,month)).fetchone()['t']
    rows    = [dict(r) for r in conn.execute('SELECT * FROM income WHERE year=? AND month=? ORDER BY day,id',(year,month))]
    conn.close()
    return jsonify({'success': True, 'income': rows,
                    'income_total': round(inc_sum,2), 'expense_total': round(exp_sum,2),
                    'savings': round(inc_sum-exp_sum,2)})

@app.route('/api/month/settle', methods=['POST'])
def settle_month():
    d = request.json; year, month = d['year'], d['month']
    settled = 1 if d.get('settled') else 0
    from datetime import datetime as dt
    settled_at = dt.now().strftime('%d.%m.%Y %H:%M') if settled else None
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO month_status (year,month,settled,settled_at) VALUES (?,?,?,?)",
                 (year, month, settled, settled_at))
    conn.commit()
    ov_p, ov_c = build_ov(conn, year, month)
    settlement     = calculate_settlement(year, month, conn)
    ov_settlements = calculate_category_settlements(year, month, conn)
    if settled and ov_settlements:
        all_pnames = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]
        ov_p = apply_settlements_to_ov(ov_p, ov_settlements, all_pnames)
    conn.close()
    return jsonify({'success': True, 'settled': bool(settled), 'settled_at': settled_at,
        'ov_p': {k: dict(v) for k,v in ov_p.items()}, 'ov_c': ov_c,
        'settlement': settlement, 'ov_settlements': ov_settlements})

@app.route('/settings')
def settings():
    conn = get_db()
    persons   = [dict(r) for r in conn.execute('SELECT * FROM persons ORDER BY name')]
    cats      = [dict(r) for r in conn.execute('SELECT * FROM categories ORDER BY name')]
    recurring = [dict(r) for r in conn.execute('SELECT * FROM recurring_expenses ORDER BY id')]
    conn.close()
    return render_template('settings.html', active='settings', persons=persons, categories=cats, recurring=recurring)



def compute_chart_data(conn):
    rows = conn.execute("""
        SELECT e.year, e.month, e.day,
               COALESCE(p.name,'Unbekannt') as person,
               COALESCE(c.name,'Sonstige')  as category,
               COALESCE(c.color,'#6c757d')  as color,
               e.amount
        FROM expenses e
        LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id
        ORDER BY e.year, e.month, e.day""").fetchall()
    persons = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]
    cats    = [{'name':r['name'],'color':r['color']}
               for r in conn.execute('SELECT name,color FROM categories ORDER BY name')]
    months  = [(r['year'],r['month'])
               for r in conn.execute('SELECT DISTINCT year,month FROM expenses ORDER BY year,month')]
    settled_months = {(r['year'],r['month'])
               for r in conn.execute('SELECT year,month FROM month_status WHERE settled=1')}
    month_sett = {}
    for (y,m) in settled_months:
        month_sett[(y,m)] = calculate_category_settlements(y, m, conn)
    from collections import defaultdict
    agg  = defaultdict(float); meta = {}
    for r in rows:
        k = (r['year'],r['month'],r['person'],r['category'])
        agg[k] += r['amount']
        meta[(r['year'],r['month'],r['category'])] = r['color']
    adjusted = []
    for (y,m,p,cn), total in agg.items():
        net = total
        if (y,m) in month_sett:
            adj = month_sett[(y,m)].get(cn,{}).get(p,0)
            net = round(total - adj, 2)
        if net > 0.005:
            adjusted.append({'year':y,'month':m,'day':1,'person':p,'category':cn,
                              'color':meta.get((y,m,cn),'#6c757d'),'amount':net})
    return {'rows':adjusted,'persons':persons,'categories':cats,
            'months':[list(m) for m in months],'month_names':MONTH_NAMES,
            'settled_months':[list(m) for m in settled_months]}

@app.route('/api/chart-data')
def chart_data():
    conn = get_db()
    data = compute_chart_data(conn)
    conn.close()
    return jsonify(data)
@app.route('/overview')
def overview():
    conn = get_db()
    mk = [(r['year'],r['month']) for r in conn.execute(
        'SELECT DISTINCT year,month FROM expenses ORDER BY year DESC, month DESC')]
    data = conn.execute("""
        SELECT e.year, e.month, p.name as pname, c.name as cname, c.color, SUM(e.amount) as total
        FROM expenses e LEFT JOIN persons p ON e.person_id=p.id
        LEFT JOIN categories c ON e.category_id=c.id
        GROUP BY e.year,e.month,e.person_id,e.category_id""").fetchall()
    settled_months = {(r['year'],r['month'])
        for r in conn.execute('SELECT year,month FROM month_status WHERE settled=1')}
    all_pnames = [r['name'] for r in conn.execute('SELECT name FROM persons ORDER BY name')]
    structured, cats_map = {}, {}
    for r in data:
        pn = r['pname'] or 'Unbekannt'; cn = r['cname'] or 'Sonstige'
        cats_map[cn] = r['color'] or '#6c757d'
        structured.setdefault(pn, {}).setdefault((r['year'],r['month']), {})[cn] = r['total']
    # Apply settlement adjustments for settled months
    for (y, m) in settled_months:
        ovs = calculate_category_settlements(y, m, conn)
        if not ovs: continue
        for p in all_pnames:
            for cn, person_sett in ovs.items():
                paid = structured.get(p, {}).get((y,m), {}).get(cn, 0)
                adj  = person_sett.get(p, 0)
                net  = round(paid - adj, 2)
                if net > 0.005:
                    structured.setdefault(p, {}).setdefault((y,m), {})[cn] = net
                elif cn in structured.get(p, {}).get((y,m), {}):
                    del structured[p][(y,m)][cn]
    # Income per month: {(year,month): {person: total}}
    inc_rows = conn.execute("""
        SELECT i.year,i.month, COALESCE(p.name,'?') as pname, SUM(i.amount) as total
        FROM income i LEFT JOIN persons p ON i.person_id=p.id
        GROUP BY i.year,i.month,i.person_id""").fetchall()
    income_map = {}  # {(y,m): {person: total}}
    for r in inc_rows:
        income_map.setdefault((r['year'],r['month']),{})[r['pname']] = round(r['total'],2)
    # expense totals per month
    exp_totals = {}
    for (y,m), pcats in [(k,v) for p,months in structured.items() for k,v in months.items()]:
        pass
    exp_totals_by_month = {}
    for pn, months in structured.items():
        for (y,m), cats in months.items():
            exp_totals_by_month[(y,m)] = exp_totals_by_month.get((y,m),0) + sum(cats.values())
    chart_data_obj = compute_chart_data(conn)
    conn.close()
    return render_template('overview.html', active='overview',
        structured=structured, all_cats=sorted(cats_map.items()),
        month_keys=mk, month_names=MONTH_NAMES, persons_list=sorted(structured.keys()),
        settled_months=[(y,m) for y,m in settled_months],
        income_map={(f'{k[0]}-{k[1]}'):v for k,v in income_map.items()},
        exp_totals_by_month={(f'{k[0]}-{k[1]}'):round(v,2) for k,v in exp_totals_by_month.items()},
        chart_data=chart_data_obj)

if __name__ == '__main__':
    init_db()
    app.run(debug=False, host='0.0.0.0', port=5000)