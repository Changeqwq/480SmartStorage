import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app


class InventoryWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / 'instance'
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.database = str(Path(self.temp.name) / 'inventory.sqlite')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-secret-only',
                               'DATABASE': self.database, 'SAML_SETTINGS_PATH': '',
                               'LOCAL_AUTH_ENABLED': True, 'SEED_DEMO_DATA': True})
        self.client = self.app.test_client()
        self.register_and_login('student', 'Student User')

    def tearDown(self):
        self.temp.cleanup()

    def csrf(self, path='/dashboard'):
        page = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def register_and_login(self, username, name):
        token = self.csrf('/register')
        self.client.post('/register', data={'csrf_token': token, 'username': username,
                         'name': name, 'password': 'prototype-123',
                         'confirm_password': 'prototype-123'})
        token = self.csrf('/login')
        self.client.post('/auth/local/login', data={'csrf_token': token,
                         'username': username, 'password': 'prototype-123'})

    def logout(self):
        self.client.post('/logout', data={'csrf_token': self.csrf()})

    def item(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute('SELECT * FROM items ORDER BY id LIMIT 1').fetchone()

    def test_catalog_search_and_item_details(self):
        self.assertIn(b'Cordless Drill Kit', self.client.get('/catalog?q=drill').data)
        self.assertNotIn(b'Raspberry Pi 5 Kit', self.client.get('/catalog?q=drill').data)
        item = self.item()
        response = self.client.get(f"/items/{item['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(item['name'].encode(), response.data)

    def test_checkout_qr_and_return_update_inventory_once(self):
        item = self.item()
        response = self.client.post(f"/items/{item['id']}/checkout",
                                    data={'csrf_token': self.csrf(f"/items/{item['id']}"), 'quantity': '2'})
        self.assertEqual(response.status_code, 303)
        loan_url = response.headers['Location']
        loan_id = int(loan_url.rsplit('/', 1)[1])
        with closing(sqlite3.connect(self.database)) as connection:
            available = connection.execute('SELECT quantity_available FROM items WHERE id=?',
                                           (item['id'],)).fetchone()[0]
            self.assertEqual(available, item['quantity_available'] - 2)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 1)
        qr = self.client.get(f'/loans/{loan_id}/qr.png')
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr.mimetype, 'image/png')
        self.assertGreater(len(qr.data), 500)
        token = self.csrf(loan_url)
        self.assertEqual(self.client.post(f'/loans/{loan_id}/return', data={'csrf_token': token}).status_code, 303)
        token = self.csrf(loan_url)
        self.client.post(f'/loans/{loan_id}/return', data={'csrf_token': token})
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute('SELECT quantity_available FROM items WHERE id=?',
                                                (item['id'],)).fetchone()[0], item['quantity_available'])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 2)

    def test_cannot_checkout_more_than_available(self):
        item = self.item()
        response = self.client.post(f"/items/{item['id']}/checkout",
                                    data={'csrf_token': self.csrf(f"/items/{item['id']}"),
                                          'quantity': str(item['quantity_available'] + 1)})
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM loans').fetchone()[0], 0)

    def test_student_cannot_access_admin_or_other_users_loan(self):
        self.assertEqual(self.client.get('/admin').status_code, 403)
        item = self.item()
        with closing(sqlite3.connect(self.database)) as connection:
            other_id = connection.execute("INSERT INTO users(principal,name,email,password_hash) VALUES ('local:other','Other User','',NULL)").lastrowid
            loan_id = connection.execute('''INSERT INTO loans(user_id,item_id,quantity,status,checked_out_at,due_at,pickup_token)
                VALUES (?, ?, 1, 'checked_out', '2026-01-01T00:00:00Z', '2026-01-08T00:00:00Z', 'abcdefghijklmnopqrstuvwxyz')''',
                (other_id, item['id'])).lastrowid
            connection.commit()
        self.assertEqual(self.client.get(f'/loans/{loan_id}').status_code, 403)
        self.assertEqual(self.client.get(f'/loans/{loan_id}/qr.png').status_code, 403)

    def test_ta_inventory_and_user_management(self):
        self.logout()
        self.register_and_login('staff', 'Staff User')
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=['set-role', 'local:staff', 'ta']).exit_code, 0)
        self.logout()
        token = self.csrf('/login')
        self.client.post('/auth/local/login', data={'csrf_token': token,
                         'username': 'staff', 'password': 'prototype-123'})
        self.assertEqual(self.client.get('/admin').status_code, 200)
        token = self.csrf('/admin/items/new')
        response = self.client.post('/admin/items/new', data={
            'csrf_token': token, 'sku': 'TOOL-TEST-01', 'name': 'Test Fixture',
            'category': 'tool', 'description': 'A test inventory fixture.',
            'storage_location': 'Lab Shelf T1', 'quantity_total': '2',
            'quantity_available': '2', 'low_stock_threshold': '1', 'active': '1'})
        self.assertEqual(response.status_code, 303)
        self.assertIn(b'Test Fixture', self.client.get('/admin/items').data)
        with closing(sqlite3.connect(self.database)) as connection:
            student_id = connection.execute("SELECT id FROM users WHERE principal='local:student'").fetchone()[0]
        token = self.csrf('/admin/users')
        response = self.client.post(f'/admin/users/{student_id}', data={
            'csrf_token': token, 'role': 'student'})
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.database)) as connection:
            active = connection.execute('SELECT active FROM users WHERE id=?', (student_id,)).fetchone()[0]
            self.assertEqual(active, 0)


if __name__ == '__main__':
    unittest.main()
