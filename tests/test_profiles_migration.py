from app.routes import main as m


def _spalten(db, tabelle):
    return {r[1] for r in db.execute(f'PRAGMA table_info({tabelle})')}


def test_users_hat_neue_spalten(db):
    spalten = _spalten(db, 'users')
    for name in ('person_id', 'role', 'is_active', 'must_change_password',
                 'created_at', 'last_login', 'session_epoch'):
        assert name in spalten, f'Spalte {name} fehlt'


def test_app_meta_existiert(db):
    assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_meta'").fetchone()


def test_bestehender_admin_behaelt_adminrolle(db):
    """Der ALTER-Default ist 'member' - ohne die Kaskade waere der einzige
    Benutzer degradiert und niemand koennte mehr Benutzer verwalten."""
    row = db.execute("SELECT role, is_active FROM users WHERE username='admin'").fetchone()
    assert row['role'] == 'admin'
    assert row['is_active'] == 1


def test_kaskade_befoerdert_aeltesten_benutzer(app, db):
    """Kein Benutzer heisst 'admin' und keiner ist Admin -> der aelteste wird es."""
    db.execute("UPDATE users SET role='member'")
    db.execute("UPDATE users SET username='philipp' WHERE username='admin'")
    db.commit()
    with app.app_context():
        m.init_db()
    row = db.execute("SELECT username, role FROM users ORDER BY id LIMIT 1").fetchone()
    assert row['role'] == 'admin'
    db.execute("UPDATE users SET username='admin' WHERE username='philipp'")
    db.commit()


def test_kaskade_ist_idempotent(app, db):
    with app.app_context():
        m.init_db()
        m.init_db()
    anzahl = db.execute("SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1").fetchone()['c']
    assert anzahl == 1


def test_autolink_nur_bei_eindeutigkeit(app, db):
    """Genau ein Benutzer und genau eine Person -> verknuepfen."""
    db.execute("UPDATE users SET person_id=NULL")
    cur = db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    pid = cur.lastrowid
    db.commit()
    with app.app_context():
        m.init_db()
    assert db.execute('SELECT person_id FROM users').fetchone()['person_id'] == pid


def test_kein_autolink_bei_mehreren_personen(app, db):
    db.execute("UPDATE users SET person_id=NULL")
    db.execute("INSERT INTO persons (name) VALUES ('Alice')")
    db.execute("INSERT INTO persons (name) VALUES ('Bob')")
    db.commit()
    with app.app_context():
        m.init_db()
    assert db.execute('SELECT person_id FROM users').fetchone()['person_id'] is None


def test_person_kann_nur_einmal_verknuepft_werden(db):
    cur = db.execute("INSERT INTO persons (name) VALUES ('Carla')")
    pid = cur.lastrowid
    db.execute("UPDATE users SET person_id=? WHERE username='admin'", (pid,))
    db.execute("INSERT INTO users (username, password_hash, person_id) VALUES ('zweiter','x',NULL)")
    db.commit()
    import pytest
    with pytest.raises(Exception):
        db.execute("UPDATE users SET person_id=? WHERE username='zweiter'", (pid,))
        db.commit()
    db.rollback()
