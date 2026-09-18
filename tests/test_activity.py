def test_activity_starts_at_zero(auth_client, csrf, db):
    res = auth_client.post('/api/activity', json={'scope': 'month:2026-09'},
                           headers={'X-CSRF-Token': csrf})
    assert res.status_code == 200
    body = res.get_json()
    assert body['success'] is True
    assert body['version'] == 0
    assert body['others'] == []


def test_activity_requires_scope(auth_client, csrf):
    res = auth_client.post('/api/activity', json={}, headers={'X-CSRF-Token': csrf})
    assert res.status_code == 400


def test_bump_activity_increments_and_records_user(app, db):
    from app.routes import main as m
    with app.test_request_context():
        from flask import session
        session['username'] = 'alice'
        first = m._bump_activity(db, 'settings')
        second = m._bump_activity(db, 'settings')
        db.commit()
    assert (first, second) == (1, 2)
    row = db.execute("SELECT version, updated_by FROM activity WHERE scope='settings'").fetchone()
    assert row['version'] == 2
    assert row['updated_by'] == 'alice'


def test_presence_heartbeat_written(auth_client, csrf, db):
    auth_client.post('/api/activity', json={'scope': 'month:2026-09'},
                     headers={'X-CSRF-Token': csrf})
    rows = db.execute("SELECT * FROM presence WHERE scope='month:2026-09'").fetchall()
    assert len(rows) == 1


def test_require_v2_rejects_legacy_payload(app):
    from app.routes import main as m
    with app.test_request_context():
        assert m._require_v2({'v': 2, 'rows': []}) is None
        rejected = m._require_v2({'year': 2026, 'expenses': []})
        assert rejected is not None
        assert rejected[1] == 409
