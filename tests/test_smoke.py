def test_login_page_reachable(client):
    res = client.get('/login')
    assert res.status_code == 200


def test_login_and_overview(auth_client):
    res = auth_client.get('/overview')
    assert res.status_code == 200


def test_csrf_token_endpoint(auth_client):
    token = auth_client.get('/api/csrf-token').get_json()['csrf_token']
    assert isinstance(token, str) and len(token) == 64
