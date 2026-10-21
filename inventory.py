"""Inventory, lending, QR credential, and staff administration workflows."""
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
import re
import secrets
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for
import qrcode


inventory_bp = Blueprint('inventory', __name__)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value):
    return value.isoformat().replace('+00:00', 'Z')


def init_inventory_schema(connection, seed_demo_data=True):
    """Create additive prototype tables without replacing existing user data."""
    user_columns = {row['name'] for row in connection.execute('PRAGMA table_info(users)')}
    if 'active' not in user_columns:
        connection.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            sku TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('tool','component','consumable')),
            description TEXT NOT NULL DEFAULT '',
            storage_location TEXT NOT NULL,
            quantity_total INTEGER NOT NULL CHECK(quantity_total >= 0),
            quantity_available INTEGER NOT NULL CHECK(quantity_available >= 0),
            low_stock_threshold INTEGER NOT NULL DEFAULT 1 CHECK(low_stock_threshold >= 0),
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            CHECK(quantity_available <= quantity_total)
        );
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            item_id INTEGER NOT NULL REFERENCES items(id),
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            status TEXT NOT NULL CHECK(status IN ('checked_out','returned')),
            checked_out_at TEXT NOT NULL,
            due_at TEXT NOT NULL,
            returned_at TEXT,
            pickup_token TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY,
            loan_id INTEGER NOT NULL REFERENCES loans(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            item_id INTEGER NOT NULL REFERENCES items(id),
            action TEXT NOT NULL CHECK(action IN ('checkout','return')),
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
        CREATE INDEX IF NOT EXISTS idx_loans_user_status ON loans(user_id, status);
        CREATE INDEX IF NOT EXISTS idx_loans_due ON loans(status, due_at);
        CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at);
    ''')
    if seed_demo_data and connection.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 0:
        created = iso(utc_now())
        connection.executemany('''
            INSERT INTO items
            (sku,name,category,description,storage_location,quantity_total,
             quantity_available,low_stock_threshold,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', [
            ('TOOL-DRILL-01', 'Cordless Drill Kit', 'tool',
             '18V drill, two batteries, charger, and hard case.', 'Main Storage · Shelf A1', 4, 4, 1, created),
            ('TOOL-METER-01', 'Digital Multimeter', 'tool',
             'Auto-ranging meter with probes for electronics troubleshooting.', 'Main Storage · Shelf A2', 8, 8, 2, created),
            ('TOOL-CALIPER-01', 'Digital Caliper', 'tool',
             'Stainless steel 150 mm caliper with metric and inch modes.', 'Main Storage · Drawer B3', 6, 6, 2, created),
            ('COMP-ARDUINO-01', 'Arduino Uno R4 WiFi', 'component',
             'Microcontroller development board with Wi-Fi and Bluetooth.', 'Main Storage · Bin C4', 10, 10, 2, created),
            ('COMP-RPI-01', 'Raspberry Pi 5 Kit', 'component',
             '8 GB board, power supply, case, microSD card, and HDMI cable.', 'Main Storage · Cabinet D2', 3, 3, 1, created),
            ('CONS-JUMPER-01', 'Jumper Wire Pack', 'consumable',
             'Mixed male/female Dupont jumper wires, 40 pieces per pack.', 'Main Storage · Bin E1', 20, 20, 5, created),
        ])
    connection.commit()


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.user:
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapper


def staff_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.user:
            return redirect(url_for('login'))
        if g.user['role'] != 'ta':
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def get_dashboard_data(connection, user):
    if user['role'] == 'ta':
        return {
            'active_items': connection.execute('SELECT COUNT(*) FROM items WHERE active=1').fetchone()[0],
            'units_out': connection.execute("SELECT COALESCE(SUM(quantity),0) FROM loans WHERE status='checked_out'").fetchone()[0],
            'low_stock': connection.execute('SELECT COUNT(*) FROM items WHERE active=1 AND quantity_available<=low_stock_threshold').fetchone()[0],
            'overdue': connection.execute("SELECT COUNT(*) FROM loans WHERE status='checked_out' AND due_at<?", (iso(utc_now()),)).fetchone()[0],
            'recent_transactions': connection.execute('''
                SELECT t.*, i.name AS item_name, u.name AS user_name
                FROM transactions t JOIN items i ON i.id=t.item_id JOIN users u ON u.id=t.user_id
                ORDER BY t.created_at DESC LIMIT 5
            ''').fetchall(),
        }
    return {
        'active_loans': connection.execute('''
            SELECT l.*, i.name AS item_name, i.storage_location
            FROM loans l JOIN items i ON i.id=l.item_id
            WHERE l.user_id=? AND l.status='checked_out' ORDER BY l.due_at
        ''', (user['id'],)).fetchall(),
        'recent_items': connection.execute('''
            SELECT * FROM items WHERE active=1 ORDER BY created_at DESC, name LIMIT 3
        ''').fetchall(),
    }


@inventory_bp.get('/catalog')
@login_required
def catalog():
    query = request.args.get('q', '').strip()[:80]
    category = request.args.get('category', '')
    availability = request.args.get('availability', '')
    clauses = ['active=1']
    parameters = []
    if query:
        clauses.append('(name LIKE ? OR sku LIKE ? OR description LIKE ? OR storage_location LIKE ?)')
        term = f'%{query}%'
        parameters.extend([term] * 4)
    if category in {'tool', 'component', 'consumable'}:
        clauses.append('category=?')
        parameters.append(category)
    else:
        category = ''
    if availability == 'available':
        clauses.append('quantity_available>0')
    elif availability == 'unavailable':
        clauses.append('quantity_available=0')
    else:
        availability = ''
    items = g.db.execute(
        f"SELECT * FROM items WHERE {' AND '.join(clauses)} ORDER BY name", parameters
    ).fetchall()
    return render_template('catalog.html', items=items, query=query,
                           category=category, availability=availability)


@inventory_bp.get('/items/<int:item_id>')
@login_required
def item_detail(item_id):
    item = g.db.execute('SELECT * FROM items WHERE id=? AND active=1', (item_id,)).fetchone()
    if not item:
        abort(404)
    active_loan = g.db.execute('''
        SELECT * FROM loans WHERE user_id=? AND item_id=? AND status='checked_out'
        ORDER BY checked_out_at DESC LIMIT 1
    ''', (g.user['id'], item_id)).fetchone()
    next_return = g.db.execute('''
        SELECT MIN(due_at) FROM loans WHERE item_id=? AND status='checked_out'
    ''', (item_id,)).fetchone()[0]
    return render_template('item_detail.html', item=item, active_loan=active_loan,
                           next_return=next_return)


@inventory_bp.post('/items/<int:item_id>/checkout')
@login_required
def checkout_item(item_id):
    try:
        quantity = int(request.form.get('quantity', '1'))
    except ValueError:
        quantity = 0
    if quantity < 1 or quantity > 20:
        flash('Choose a quantity between 1 and 20.', 'error')
        return redirect(url_for('inventory.item_detail', item_id=item_id))
    connection = g.db
    try:
        connection.execute('BEGIN IMMEDIATE')
        item = connection.execute('SELECT * FROM items WHERE id=? AND active=1', (item_id,)).fetchone()
        if not item:
            connection.rollback()
            abort(404)
        if item['quantity_available'] < quantity:
            connection.rollback()
            flash('That quantity is no longer available. Refresh and try again.', 'error')
            return redirect(url_for('inventory.item_detail', item_id=item_id))
        now = utc_now()
        due = now + timedelta(days=7)
        token = secrets.token_urlsafe(24)
        cursor = connection.execute('''
            INSERT INTO loans(user_id,item_id,quantity,status,checked_out_at,due_at,pickup_token)
            VALUES (?,?,?,'checked_out',?,?,?)
        ''', (g.user['id'], item_id, quantity, iso(now), iso(due), token))
        loan_id = cursor.lastrowid
        connection.execute('UPDATE items SET quantity_available=quantity_available-? WHERE id=?',
                           (quantity, item_id))
        connection.execute('''
            INSERT INTO transactions(loan_id,user_id,item_id,action,quantity,created_at)
            VALUES (?,?,?,'checkout',?,?)
        ''', (loan_id, g.user['id'], item_id, quantity, iso(now)))
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    flash('Checkout confirmed. Your pickup QR credential is ready.', 'success')
    return redirect(url_for('inventory.loan_detail', loan_id=loan_id), code=303)


@inventory_bp.get('/loans')
@login_required
def loans():
    records = g.db.execute('''
        SELECT l.*, i.name AS item_name, i.sku, i.storage_location
        FROM loans l JOIN items i ON i.id=l.item_id
        WHERE l.user_id=? ORDER BY l.checked_out_at DESC
    ''', (g.user['id'],)).fetchall()
    return render_template('loans.html', loans=records, now=iso(utc_now()))


def permitted_loan(loan_id):
    loan = g.db.execute('''
        SELECT l.*, i.name AS item_name, i.sku, i.storage_location,
               u.name AS user_name, u.principal
        FROM loans l JOIN items i ON i.id=l.item_id JOIN users u ON u.id=l.user_id
        WHERE l.id=?
    ''', (loan_id,)).fetchone()
    if not loan:
        abort(404)
    if loan['user_id'] != g.user['id'] and g.user['role'] != 'ta':
        abort(403)
    return loan


@inventory_bp.get('/loans/<int:loan_id>')
@login_required
def loan_detail(loan_id):
    return render_template('loan_detail.html', loan=permitted_loan(loan_id), now=iso(utc_now()))


@inventory_bp.get('/pickup/<token>')
@login_required
def pickup_token(token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,64}', token):
        abort(404)
    loan = g.db.execute('SELECT id FROM loans WHERE pickup_token=?', (token,)).fetchone()
    if not loan:
        abort(404)
    return redirect(url_for('inventory.loan_detail', loan_id=loan['id']))


@inventory_bp.get('/loans/<int:loan_id>/qr.png')
@login_required
def loan_qr(loan_id):
    loan = permitted_loan(loan_id)
    payload = request.url_root.rstrip('/') + url_for('inventory.pickup_token', token=loan['pickup_token'])
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=8, border=3)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color='#20211e', back_color='#fafaf7')
    output = BytesIO()
    image.save(output, format='PNG')
    output.seek(0)
    return send_file(output, mimetype='image/png', download_name=f'smartstorage-loan-{loan_id}.png')


@inventory_bp.post('/loans/<int:loan_id>/return')
@login_required
def return_item(loan_id):
    connection = g.db
    try:
        connection.execute('BEGIN IMMEDIATE')
        loan = connection.execute('SELECT * FROM loans WHERE id=?', (loan_id,)).fetchone()
        if not loan:
            connection.rollback()
            abort(404)
        if loan['user_id'] != g.user['id'] and g.user['role'] != 'ta':
            connection.rollback()
            abort(403)
        if loan['status'] != 'checked_out':
            connection.rollback()
            flash('This item has already been returned.', 'error')
            return redirect(url_for('inventory.loan_detail', loan_id=loan_id))
        now = iso(utc_now())
        connection.execute("UPDATE loans SET status='returned',returned_at=? WHERE id=?", (now, loan_id))
        connection.execute('UPDATE items SET quantity_available=quantity_available+? WHERE id=?',
                           (loan['quantity'], loan['item_id']))
        connection.execute('''
            INSERT INTO transactions(loan_id,user_id,item_id,action,quantity,created_at)
            VALUES (?,?,?,'return',?,?)
        ''', (loan_id, loan['user_id'], loan['item_id'], loan['quantity'], now))
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    flash('Return confirmed and inventory updated.', 'success')
    return redirect(url_for('inventory.loan_detail', loan_id=loan_id), code=303)


@inventory_bp.get('/admin')
@staff_required
def admin_dashboard():
    summary = get_dashboard_data(g.db, g.user)
    return render_template('admin/dashboard.html', **summary)


@inventory_bp.get('/admin/items')
@staff_required
def admin_items():
    items = g.db.execute('SELECT * FROM items ORDER BY active DESC, name').fetchall()
    return render_template('admin/items.html', items=items)


def item_form_values():
    values = {
        'sku': request.form.get('sku', '').strip().upper(),
        'name': request.form.get('name', '').strip(),
        'category': request.form.get('category', ''),
        'description': request.form.get('description', '').strip(),
        'storage_location': request.form.get('storage_location', '').strip(),
        'active': 1 if request.form.get('active') == '1' else 0,
    }
    try:
        values['quantity_total'] = int(request.form.get('quantity_total', ''))
        values['quantity_available'] = int(request.form.get('quantity_available', ''))
        values['low_stock_threshold'] = int(request.form.get('low_stock_threshold', ''))
    except ValueError:
        return values, 'Quantities and thresholds must be whole numbers.'
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{2,39}', values['sku']):
        return values, 'SKU must contain 3–40 letters, numbers, hyphens, or underscores.'
    if not 2 <= len(values['name']) <= 100:
        return values, 'Item name must contain 2–100 characters.'
    if values['category'] not in {'tool', 'component', 'consumable'}:
        return values, 'Choose a valid category.'
    if not 2 <= len(values['storage_location']) <= 120:
        return values, 'Storage location must contain 2–120 characters.'
    if not 0 <= values['quantity_available'] <= values['quantity_total'] <= 100000:
        return values, 'Available quantity must be between zero and total quantity.'
    if not 0 <= values['low_stock_threshold'] <= 100000:
        return values, 'Low-stock threshold must be zero or greater.'
    return values, None


@inventory_bp.route('/admin/items/new', methods=['GET', 'POST'])
@staff_required
def admin_item_new():
    if request.method == 'GET':
        return render_template('admin/item_form.html', item=None)
    values, error = item_form_values()
    if error:
        return render_template('admin/item_form.html', item=values, error=error), 400
    try:
        g.db.execute('''
            INSERT INTO items(sku,name,category,description,storage_location,quantity_total,
                              quantity_available,low_stock_threshold,active,created_at)
            VALUES (:sku,:name,:category,:description,:storage_location,:quantity_total,
                    :quantity_available,:low_stock_threshold,:active,:created_at)
        ''', {**values, 'created_at': iso(utc_now())})
        g.db.commit()
    except sqlite3.IntegrityError:
        g.db.rollback()
        return render_template('admin/item_form.html', item=values,
                               error='That SKU is already in use.'), 400
    flash('Item added to inventory.', 'success')
    return redirect(url_for('inventory.admin_items'), code=303)


@inventory_bp.route('/admin/items/<int:item_id>/edit', methods=['GET', 'POST'])
@staff_required
def admin_item_edit(item_id):
    item = g.db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
    if not item:
        abort(404)
    if request.method == 'GET':
        return render_template('admin/item_form.html', item=item)
    values, error = item_form_values()
    outstanding = g.db.execute("SELECT COALESCE(SUM(quantity),0) FROM loans WHERE item_id=? AND status='checked_out'",
                               (item_id,)).fetchone()[0]
    if not error and values.get('quantity_total', -1) - values.get('quantity_available', -1) < outstanding:
        error = f'Inventory must account for {outstanding} unit(s) currently checked out.'
    if error:
        return render_template('admin/item_form.html', item={**values, 'id': item_id}, error=error), 400
    try:
        g.db.execute('''
            UPDATE items SET sku=:sku,name=:name,category=:category,description=:description,
                storage_location=:storage_location,quantity_total=:quantity_total,
                quantity_available=:quantity_available,low_stock_threshold=:low_stock_threshold,
                active=:active WHERE id=:id
        ''', {**values, 'id': item_id})
        g.db.commit()
    except sqlite3.IntegrityError:
        g.db.rollback()
        return render_template('admin/item_form.html', item={**values, 'id': item_id},
                               error='That SKU is already in use.'), 400
    flash('Inventory item updated.', 'success')
    return redirect(url_for('inventory.admin_items'), code=303)


@inventory_bp.get('/admin/transactions')
@staff_required
def admin_transactions():
    clauses = ['1=1']
    parameters = []
    action = request.args.get('action', '')
    query = request.args.get('q', '').strip()[:80]
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    if action in {'checkout', 'return'}:
        clauses.append('t.action=?')
        parameters.append(action)
    else:
        action = ''
    if query:
        clauses.append('(i.name LIKE ? OR i.sku LIKE ? OR u.name LIKE ? OR u.principal LIKE ?)')
        parameters.extend([f'%{query}%'] * 4)
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_from):
        clauses.append('t.created_at>=?')
        parameters.append(date_from + 'T00:00:00Z')
    else:
        date_from = ''
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_to):
        clauses.append('t.created_at<?')
        parameters.append(date_to + 'T23:59:59Z')
    else:
        date_to = ''
    transactions = g.db.execute(f'''
        SELECT t.*,l.status,l.due_at,l.returned_at,i.name AS item_name,i.sku,
               u.name AS user_name,u.principal
        FROM transactions t JOIN loans l ON l.id=t.loan_id
        JOIN items i ON i.id=t.item_id JOIN users u ON u.id=t.user_id
        WHERE {' AND '.join(clauses)} ORDER BY t.created_at DESC LIMIT 250
    ''', parameters).fetchall()
    return render_template('admin/transactions.html', transactions=transactions,
                           action=action, query=query, date_from=date_from, date_to=date_to)


@inventory_bp.get('/admin/alerts')
@staff_required
def admin_alerts():
    low_stock = g.db.execute('''
        SELECT * FROM items WHERE active=1 AND quantity_available<=low_stock_threshold
        ORDER BY quantity_available, name
    ''').fetchall()
    overdue = g.db.execute('''
        SELECT l.*,i.name AS item_name,i.sku,u.name AS user_name,u.principal
        FROM loans l JOIN items i ON i.id=l.item_id JOIN users u ON u.id=l.user_id
        WHERE l.status='checked_out' AND l.due_at<? ORDER BY l.due_at
    ''', (iso(utc_now()),)).fetchall()
    return render_template('admin/alerts.html', low_stock=low_stock, overdue=overdue,
                           now=iso(utc_now()))


@inventory_bp.get('/admin/users')
@staff_required
def admin_users():
    users = g.db.execute('''
        SELECT u.*,COUNT(CASE WHEN l.status='checked_out' THEN 1 END) AS active_loans
        FROM users u LEFT JOIN loans l ON l.user_id=u.id
        GROUP BY u.id ORDER BY u.active DESC,u.name
    ''').fetchall()
    return render_template('admin/users.html', users=users)


@inventory_bp.post('/admin/users/<int:user_id>')
@staff_required
def admin_user_update(user_id):
    target = g.db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    if not target:
        abort(404)
    role = request.form.get('role', '')
    active = 1 if request.form.get('active') == '1' else 0
    if role not in {'student', 'ta'}:
        abort(400, description='Choose a valid role.')
    if target['id'] == g.user['id'] and (role != 'ta' or not active):
        flash('You cannot remove your own staff access or deactivate your account.', 'error')
        return redirect(url_for('inventory.admin_users'))
    if target['role'] == 'ta' and (role != 'ta' or not active):
        active_staff = g.db.execute("SELECT COUNT(*) FROM users WHERE role='ta' AND active=1").fetchone()[0]
        if active_staff <= 1:
            flash('At least one active staff account is required.', 'error')
            return redirect(url_for('inventory.admin_users'))
    g.db.execute('UPDATE users SET role=?,active=? WHERE id=?', (role, active, user_id))
    if not active:
        g.db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
    g.db.commit()
    flash('User access updated.', 'success')
    return redirect(url_for('inventory.admin_users'), code=303)
