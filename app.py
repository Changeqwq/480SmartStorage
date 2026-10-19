"""SmartStorage prototype accounts with optional Purdue SAML authentication."""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import qrcode
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

import click
from dotenv import load_dotenv
from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent


def create_app(test_config=None):
    load_dotenv(ROOT / '.env')
    app = Flask(__name__, instance_path=str(ROOT / 'instance'))
    Path(app.instance_path).mkdir(exist_ok=True)
    secret = os.getenv('SECRET_KEY')
    if not secret:
        secret_file = Path(app.instance_path) / 'secret.key'
        try:
            with secret_file.open('x', encoding='utf-8') as file:
                file.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        secret = secret_file.read_text(encoding='utf-8').strip()
    app.config.update(
        SECRET_KEY=secret,
        DATABASE=str(Path(app.instance_path) / 'smartstorage.sqlite'),
        PUBLIC_BASE_URL=os.getenv('PUBLIC_BASE_URL', 'http://127.0.0.1:5000').rstrip('/'),
        SAML_SETTINGS_PATH=os.getenv('SAML_SETTINGS_PATH', ''),
        ALLOWED_PRINCIPALS=os.getenv('ALLOWED_PRINCIPALS', ''),
        LOCAL_AUTH_ENABLED=os.getenv('LOCAL_AUTH_ENABLED', 'true').lower() == 'true',
        MAX_CONTENT_LENGTH=1024 * 1024,
        SESSION_COOKIE_NAME='smartstorage_session',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=False,
        PERMANENT_SESSION_LIFETIME=8 * 3600,
    )
    if test_config:
        app.config.update(test_config)
    saml_settings = None
    if app.config['SAML_SETTINGS_PATH']:
        path = Path(app.config['SAML_SETTINGS_PATH'])
        if not path.is_absolute():
            path = ROOT / path
        saml_settings = json.loads(path.read_text(encoding='utf-8'))
        origin = urlsplit(app.config['PUBLIC_BASE_URL'])
        if origin.scheme != 'https' or not origin.netloc or origin.path or origin.query or origin.fragment:
            raise ValueError('SSO requires PUBLIC_BASE_URL to be an HTTPS origin without a path.')
        if saml_settings['sp']['assertionConsumerService']['url'] != app.config['PUBLIC_BASE_URL'] + '/auth/saml/acs':
            raise ValueError('The ACS URL must match PUBLIC_BASE_URL + /auth/saml/acs.')
        # Enforce verification even if an operator accidentally weakens the file.
        saml_settings['strict'] = True
        saml_settings['debug'] = False
        saml_settings.setdefault('security', {}).update(
            wantAssertionsSigned=True, rejectUnsolicitedResponsesWithInResponseTo=True,
            rejectDeprecatedAlgorithm=True, wantXMLValidation=True,
        )
        OneLogin_Saml2_Settings(saml_settings)
        # SAML callbacks use a cross-site POST: Lax cookies would lose correlation.
        app.config.update(SESSION_COOKIE_SAMESITE='None', SESSION_COOKIE_SECURE=True)
    app.config['SAML_SETTINGS'] = saml_settings

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'], timeout=15)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
        return g.db

    @app.teardown_appcontext
    def close_db(error=None):
        connection = g.pop('db', None)
        if connection:
            connection.close()

    with app.app_context():
        db().executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, principal TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, email TEXT NOT NULL,
                password_hash TEXT,
                role TEXT NOT NULL DEFAULT 'student' CHECK(role IN ('student','ta'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id),
                csrf TEXT NOT NULL, request_id TEXT, relay TEXT, expires INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS consumed_assertions (
                assertion_id TEXT PRIMARY KEY, expires INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
                key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires INTEGER NOT NULL
            );
        ''')
        if 'password_hash' not in {row['name'] for row in db().execute('PRAGMA table_info(users)')}:
            db().execute('ALTER TABLE users ADD COLUMN password_hash TEXT')
        db().commit()

    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def new_session(user_id=None, lifetime=600):
        old = session.get('sid')
        if old:
            db().execute('DELETE FROM sessions WHERE token=?', (token_hash(old),))
        session.clear()
        sid = secrets.token_urlsafe(32)
        session['sid'] = sid
        db().execute('INSERT INTO sessions(token,user_id,csrf,expires) VALUES (?,?,?,?)',
                     (token_hash(sid), user_id, secrets.token_urlsafe(32), int(time.time()) + lifetime))
        db().commit()
        g.auth_session = db().execute('SELECT * FROM sessions WHERE token=?', (token_hash(sid),)).fetchone()

    @app.before_request
    def load_session():
        g.user = None
        g.auth_session = None
        if request.endpoint == 'static':
            return
        sid = session.get('sid')
        if isinstance(sid, str):
            g.auth_session = db().execute('SELECT * FROM sessions WHERE token=? AND expires>?',
                                         (token_hash(sid), int(time.time()))).fetchone()
        if g.auth_session and g.auth_session['user_id']:
            g.user = db().execute('SELECT * FROM users WHERE id=?', (g.auth_session['user_id'],)).fetchone()
        if not g.auth_session:
            if request.endpoint == 'acs':
                return  # Missing correlation must fail; never create one here.
            new_session()
        if request.method == 'POST' and request.endpoint != 'acs':
            supplied = request.form.get('csrf_token', '')
            if not secrets.compare_digest(supplied, g.auth_session['csrf']):
                abort(400, description='This form expired. Refresh the page and try again.')

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if saml_settings:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.context_processor
    def context():
        return dict(sso_ready=bool(saml_settings), local_auth_enabled=app.config['LOCAL_AUTH_ENABLED'],
                    csrf_token=g.auth_session['csrf'] if g.auth_session else '')

    def protected(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not g.user:
                return redirect(url_for('login'))
            return view(*args, **kwargs)
        return wrapper

    def saml_auth():
        # Canonical registered origin, not untrusted Host/X-Forwarded-* headers.
        origin = urlsplit(app.config['PUBLIC_BASE_URL'])
        return OneLogin_Saml2_Auth({
            'https': 'on', 'http_host': origin.netloc,
            'server_port': str(origin.port or 443), 'script_name': request.path,
            'get_data': request.args.to_dict(), 'post_data': request.form.to_dict(),
        }, old_settings=saml_settings)

    @app.get('/')
    def index():
        return redirect(url_for('dashboard' if g.user else 'login'))

    @app.get('/login')
    def login():
        if g.user:
            return redirect(url_for('dashboard'))
        return render_template('login.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if not app.config['LOCAL_AUTH_ENABLED']:
            abort(404)
        if g.user:
            return redirect(url_for('dashboard'))
        error = None
        if request.method == 'POST':
            username = request.form.get('username', '').strip().lower()
            name = request.form.get('name', '').strip()
            password = request.form.get('password', '')
            confirmation = request.form.get('confirm_password', '')
            if not re.fullmatch(r'[a-z0-9_]{3,32}', username):
                error = 'Use 3–32 letters, numbers or underscores for your username.'
            elif not 1 <= len(name) <= 80:
                error = 'Enter a display name between 1 and 80 characters.'
            elif not 8 <= len(password) <= 128:
                error = 'Use a password between 8 and 128 characters.'
            elif password != confirmation:
                error = 'Your passwords do not match.'
            else:
                try:
                    db().execute('INSERT INTO users(principal,name,email,password_hash) VALUES (?,?,?,?)',
                                 ('local:' + username, name, '', generate_password_hash(password)))
                    db().commit()
                    return redirect(url_for('login', registered='1'), code=303)
                except sqlite3.IntegrityError:
                    db().rollback()
                    error = 'That username is already taken. Choose another one.'
        return render_template('register.html', error=error), 400 if error else 200

    @app.post('/auth/local/login')
    def local_login():
        if not app.config['LOCAL_AUTH_ENABLED']:
            abort(404)
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        # A small persistent per-IP throttle for this local prototype.
        key = hashlib.sha256((request.remote_addr or 'unknown').encode()).hexdigest()
        now = int(time.time())
        connection = db()
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('DELETE FROM login_attempts WHERE expires<=?', (now,))
        attempt = connection.execute('SELECT attempts FROM login_attempts WHERE key=?', (key,)).fetchone()
        if attempt and attempt['attempts'] >= 10:
            connection.commit()
            return render_template('login.html', error='Too many login attempts. Try again in 10 minutes.'), 429
        connection.execute('''INSERT INTO login_attempts VALUES (?,1,?)
            ON CONFLICT(key) DO UPDATE SET attempts=attempts+1''', (key, now + 600))
        connection.commit()
        user = connection.execute('SELECT * FROM users WHERE principal=?', ('local:' + username,)).fetchone()
        valid = (re.fullmatch(r'[a-z0-9_]{3,32}', username) and 8 <= len(password) <= 128
                 and user and user['password_hash'] and check_password_hash(user['password_hash'], password))
        if not valid:
            return render_template('login.html', error='Incorrect username or password.'), 401
        connection.execute('DELETE FROM login_attempts WHERE key=?', (key,))
        connection.commit()
        new_session(user['id'], lifetime=8 * 3600)
        return redirect(url_for('dashboard'), code=303)

    @app.post('/auth/saml/login')
    def start_sso():
        if not saml_settings:
            return render_template('login.html', error='Purdue SSO is not connected yet. The project team must complete registration with Purdue IT.'), 503
        new_session()
        auth = saml_auth()
        relay = secrets.token_urlsafe(32)
        target = auth.login(return_to=relay)
        db().execute('UPDATE sessions SET request_id=?,relay=? WHERE token=?',
                     (auth.get_last_request_id(), relay, g.auth_session['token']))
        db().commit()
        return redirect(target)

    @app.post('/auth/saml/acs')
    def acs():
        def denied():
            if g.auth_session:
                db().execute('DELETE FROM sessions WHERE token=?', (g.auth_session['token'],))
                db().commit()
            session.clear()
            g.auth_session = None
            return render_template('login.html', error='Sign-in could not be verified. Start again from the login page, or contact the project team.'), 400
        if not saml_settings or not g.auth_session or not g.auth_session['request_id']:
            return denied()
        if not secrets.compare_digest(request.form.get('RelayState', ''), g.auth_session['relay'] or ''):
            return denied()
        try:
            auth = saml_auth()
            auth.process_response(request_id=g.auth_session['request_id'])
            if auth.get_errors() or not auth.is_authenticated():
                return denied()
            attrs = auth.get_attributes()
            def attr(oid, friendly):
                values = attrs.get(oid, attrs.get(friendly, []))
                return values[0].strip() if len(values) == 1 and isinstance(values[0], str) else ''
            principal = attr('urn:oid:1.3.6.1.4.1.5923.1.1.1.6', 'eduPersonPrincipalName').lower()
            # Identity comes from the signed assertion, never a form field or mail suffix alone.
            if principal.count('@') != 1 or principal.split('@')[1] != 'purdue.edu' or not principal.split('@')[0]:
                return denied()
            allowed = {p.strip().lower() for p in app.config['ALLOWED_PRINCIPALS'].split(',') if p.strip()}
            if allowed and principal not in allowed:
                return denied()
            name = attr('urn:oid:2.16.840.1.113730.3.1.241', 'displayName') or principal.split('@')[0]
            email = attr('urn:oid:0.9.2342.19200300.100.1.3', 'mail') or principal
            assertion_id = auth.get_last_assertion_id()
            if not assertion_id:
                return denied()
            # Consume request and assertion atomically, including concurrent callbacks.
            connection = db()
            connection.execute('BEGIN IMMEDIATE')
            active = connection.execute('SELECT request_id,expires FROM sessions WHERE token=?',
                                         (g.auth_session['token'],)).fetchone()
            if not active or active['request_id'] != g.auth_session['request_id'] or active['expires'] <= time.time():
                connection.rollback()
                return denied()
            connection.execute('DELETE FROM consumed_assertions WHERE expires<?', (int(time.time()),))
            connection.execute('INSERT INTO consumed_assertions VALUES (?,?)', (assertion_id, int(time.time()) + 86400))
            connection.execute('''INSERT INTO users(principal,name,email) VALUES (?,?,?)
                ON CONFLICT(principal) DO UPDATE SET name=excluded.name,email=excluded.email''', (principal, name, email))
            user_id = connection.execute('SELECT id FROM users WHERE principal=?', (principal,)).fetchone()['id']
            connection.execute('UPDATE sessions SET request_id=NULL,relay=NULL WHERE token=?', (g.auth_session['token'],))
            connection.commit()
            new_session(user_id, lifetime=8 * 3600)
            return redirect(url_for('dashboard'), code=303)
        except Exception:
            # Do not expose or log assertion XML, personal attributes or keys.
            db().rollback()
            app.logger.warning('SAML authentication rejected or unavailable.')
            return denied()

    @app.get('/auth/saml/metadata')
    def metadata():
        if not saml_settings:
            abort(503, description='SSO metadata is available after the project SAML settings are configured.')
        settings = OneLogin_Saml2_Settings(saml_settings)
        xml = settings.get_sp_metadata()
        if settings.validate_metadata(xml):
            abort(503, description='SSO metadata configuration needs attention.')
        return app.response_class(xml, mimetype='application/samlmetadata+xml')

    @app.get('/dashboard')
    @protected
    def dashboard():
        return render_template('dashboard.html')

    @app.post('/logout')
    def logout():
        new_session()
        return redirect(url_for('login', signed_out='1'), code=303)

    @app.errorhandler(400)
    @app.errorhandler(503)
    def error_page(error):
        return render_template('login.html', error=error.description), error.code

    @app.cli.command('set-role')
    @click.argument('principal')
    @click.argument('role', type=click.Choice(['student', 'ta']))
    def set_role(principal, role):
        """Set a role for an existing local:username or SSO ePPN identity."""
        result = db().execute('UPDATE users SET role=? WHERE principal=?', (role, principal.strip().lower()))
        db().commit()
        if not result.rowcount:
            raise click.ClickException('User not found. Register a prototype account or complete SSO sign-in first.')
        click.echo('Role updated.')

    @app.cli.command('cleanup-sessions')
    def cleanup_sessions():
        """Remove expired sessions and consumed assertion records."""
        for table in ('sessions', 'consumed_assertions', 'login_attempts'):
            db().execute(f'DELETE FROM {table} WHERE expires<?', (int(time.time()),))
        db().commit()
        click.echo('Expired records removed.')

    return app


if __name__ == '__main__':
    from waitress import serve
    app = create_app()
    port = int(os.getenv('PORT', '5000'))
    print(f'SmartStorage running at http://127.0.0.1:{port}', flush=True)
    serve(app, host='127.0.0.1', port=port)
