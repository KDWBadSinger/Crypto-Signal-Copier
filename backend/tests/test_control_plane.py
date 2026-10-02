import sqlite3

from fastapi.testclient import TestClient
from control_plane import create_app, Credentials


def test_register_login_device_limit_expiry_revoke_and_admin(tmp_path):
    app = create_app(tmp_path/'accounts.sqlite3')
    app.state.create_user(Credentials(username='admin', password='test-admin-password'), admin=True)
    with TestClient(app) as client:
        credentials = {'username':'alice','password':'test-user-password'}
        assert client.post('/register', json=credentials).status_code == 200
        assert client.post('/register', json=credentials).status_code == 409
        assert client.post('/login', json={**credentials,'password':'wrong-password-123'}).status_code == 401
        token = client.post('/login',json=credentials).json()['token']
        headers = {'Authorization':f'Bearer {token}'}
        assert client.get('/admin/users',headers=headers).status_code == 403
        for device in ['device-000000000001','device-000000000002']:
            assert client.post('/license/check',json={'device_id':device},headers=headers).status_code == 200
        assert client.post('/license/check',json={'device_id':'device-000000000003'},headers=headers).status_code == 403
        admin_token=client.post('/login',json={'username':'admin','password':'test-admin-password'}).json()['token']
        admin={'Authorization':f'Bearer {admin_token}'}
        users=client.get('/admin/users',headers=admin).json()
        assert all('password' not in user and 'salt' not in user for user in users)
        assert client.post('/admin/users/alice/grant',json={'days':0},headers=admin).status_code == 200
        assert client.post('/license/check',json={'device_id':device},headers=headers).status_code == 403
        client.post('/admin/users/alice/grant',json={'days':30},headers=admin)
        assert client.post('/license/check',json={'device_id':device},headers=headers).status_code == 200
        client.post('/admin/users/alice/reset-devices',json={},headers=admin)
        assert client.post('/license/check',json={'device_id':device},headers=headers).status_code == 401
        with sqlite3.connect(tmp_path/'accounts.sqlite3') as conn:
            assert credentials['password'] not in str(conn.execute('SELECT * FROM users').fetchall())
            assert token not in str(conn.execute('SELECT * FROM sessions').fetchall())


def test_validation_does_not_echo_secrets_and_rate_limits(tmp_path):
    with TestClient(create_app(tmp_path/'account.sqlite3')) as client:
        response=client.post('/register',json={'username':'bad input','password':'SECRET'})
        assert response.status_code == 422 and 'SECRET' not in response.text
        for _ in range(15):
            client.post('/login',json={'username':'unknown','password':'incorrect-password'})
        assert client.post('/login',json={'username':'unknown','password':'incorrect-password'}).status_code == 429


def test_required_license_blocks_only_new_orders(tmp_path, monkeypatch):
    import asyncio
    from app.service import CopierService
    from test_desktop_workflow import settings
    from test_paper import _signal
    from decimal import Decimal
    monkeypatch.setenv('COPIER_REQUIRE_LICENSE','true')
    async def run():
        service=CopierService(settings(tmp_path))
        try:
            service.paper.reset(Decimal('1000'),10,Decimal('0'),['test'],'licensed-test')
            await service.ingest_signal(_signal())
            assert not service.paper.snapshot().trades
            assert service.store.audit_for(_signal().id)[-1]['event']=='license_blocked'
            assert (await service.stop_paper()).lifecycle=='stopped'
        finally:
            await service.bitget.close()
    asyncio.run(run())
