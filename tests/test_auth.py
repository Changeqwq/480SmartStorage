import re
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / 'instance'
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.database = str(Path(self.temp.name) / 'test.sqlite')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-secret-only',
                               'DATABASE': self.database, 'SAML_SETTINGS_PATH': '',
                               'LOCAL_AUTH_ENABLED': True})
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def token(self, client=None, path='/login'):
        page = (client or self.client).get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def register(self, **extra):
        data = dict(username='tester', name='Test User', password='prototype-123',
                    confirm_password='prototype-123', csrf_token=self.token(path='/register'))
        data.update(extra)
        return self.client.post('/register', data=data)

    def login(self, **extra):
        data = dict(username='tester', password='prototype-123', csrf_token=self.token())
        data.update(extra)
        return self.client.post('/auth/local/login', data=data)

    def test_registration_login_session_and_logout(self):
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        self.assertEqual(self.register(role='ta').status_code, 303)
        with closing(sqlite3.connect(self.database)) as connection:
            hashed, role = connection.execute('SELECT password_hash,role FROM users').fetchone()
            self.assertNotEqual(hashed, 'prototype-123')
            self.assertTrue(hashed.startswith('scrypt:'))
            self.assertEqual(role, 'student')
        self.assertEqual(self.login(username='TESTER').status_code, 303)
        page = self.client.get('/dashboard')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Test User', page.data)
        self.assertIn(b'STUDENT WORKSPACE', page.data)
        cookie = self.client.get_cookie('smartstorage_session').value
        csrf = self.token(path='/dashboard')
        self.assertEqual(self.client.post('/logout', data={'csrf_token': csrf}).status_code, 303)
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        other = self.app.test_client()
        other.set_cookie('smartstorage_session', cookie)
        self.assertEqual(other.get('/dashboard').status_code, 302)

    def test_validation_and_duplicate_username(self):
        for values in [dict(username='!'), dict(name=''), dict(password='short'),
                       dict(confirm_password='different'), dict(password='x' * 129)]:
            with self.subTest(values=values):
                self.assertEqual(self.register(**values).status_code, 400)
        self.assertEqual(self.register().status_code, 303)
        self.assertEqual(self.register(username='TESTER').status_code, 400)

    def test_invalid_credentials_and_csrf(self):
        self.register()
        self.assertEqual(self.login(password='incorrect-123').status_code, 401)
        self.assertEqual(self.login(username='unknown').status_code, 401)
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        self.assertEqual(self.login(csrf_token='bad').status_code, 400)
        self.assertEqual(self.client.post('/register', data={}).status_code, 400)
        self.assertEqual(self.client.get('/logout').status_code, 405)

    def test_expired_session(self):
        self.register()
        self.login()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('UPDATE sessions SET expires=?', (int(time.time()) - 1,))
            connection.commit()
        self.assertEqual(self.client.get('/dashboard').status_code, 302)

    def test_throttle(self):
        for unused in range(10):
            self.assertEqual(self.login(username='missing').status_code, 401)
        self.assertEqual(self.login(username='missing').status_code, 429)

    def test_local_auth_can_be_disabled(self):
        self.app.config['LOCAL_AUTH_ENABLED'] = False
        self.assertEqual(self.client.get('/register').status_code, 404)
        self.assertEqual(self.login().status_code, 404)

    def test_saml_is_disabled_and_unsolicited_callback_is_rejected(self):
        token = self.token()
        self.assertEqual(self.client.post('/auth/saml/login', data={'csrf_token': token}).status_code, 503)
        self.assertEqual(self.client.post('/auth/saml/acs', data={'SAMLResponse': 'forged'}).status_code, 400)
        self.assertEqual(self.client.get('/dashboard').status_code, 302)

    def test_roles_require_server_access(self):
        self.register()
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=['set-role', 'local:tester', 'ta']).exit_code, 0)
        self.login()
        self.assertIn(b'TA / ADMIN WORKSPACE', self.client.get('/dashboard').data)
        self.assertNotEqual(runner.invoke(args=['set-role', 'local:missing', 'ta']).exit_code, 0)
        self.assertEqual(runner.invoke(args=['set-role', 'local:tester', 'student']).exit_code, 0)
        self.assertNotIn(b'TA / ADMIN WORKSPACE', self.client.get('/dashboard').data)

    def test_profile_escapes_html_and_headers(self):
        self.register(name='<script>alert(1)</script>')
        self.login()
        response = self.client.get('/dashboard')
        self.assertNotIn(b'<script>alert', response.data)
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        cookie = self.client.get_cookie('smartstorage_session')
        self.assertTrue(cookie.http_only)
        self.assertEqual(cookie.same_site, 'Lax')


if __name__ == '__main__':
    unittest.main()
