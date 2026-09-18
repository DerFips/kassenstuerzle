"""Pytest-Fixtures für Kassenstürzle.

WICHTIG: DATABASE_PATH muss gesetzt sein, BEVOR `app` importiert wird —
main.py wertet den Pfad beim Modul-Import aus (main.py:27).
"""
import os
import pathlib
import sys
import tempfile

import pytest

_TMPDIR = tempfile.mkdtemp(prefix='kassenstuerzle-tests-')
os.environ['DATABASE_PATH'] = os.path.join(_TMPDIR, 'test.db')
os.environ['ADMIN_PASSWORD'] = 'test-password-123'
os.environ['DISABLE_SCHEDULER'] = '1'
os.environ.pop('DB_ENCRYPTION_KEY', None)   # Tests laufen unverschlüsselt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import create_app                      # noqa: E402
from app.routes import main as main_module       # noqa: E402

_CLEANUP_TABLES = ('expenses', 'income', 'persons', 'categories',
                   'recurring_expenses', 'category_splits', 'month_status',
                   'activity', 'presence')


@pytest.fixture(scope='session')
def app():
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(app):
    c = app.test_client()
    res = c.post('/login', data={'username': 'admin', 'password': 'test-password-123'})
    assert res.status_code == 302, 'Login fehlgeschlagen'
    return c


@pytest.fixture()
def csrf(auth_client):
    return auth_client.get('/api/csrf-token').get_json()['csrf_token']


@pytest.fixture()
def db(app):
    """Direkte DB-Verbindung; raeumt nach jedem Test alle Nutzdaten weg."""
    conn = main_module._open_db(main_module.DB_PATH)
    conn.row_factory = main_module.sqlite3.Row
    yield conn
    for table in _CLEANUP_TABLES:
        try:
            conn.execute(f'DELETE FROM {table}')
        except Exception:
            pass
    conn.commit()
    conn.close()
