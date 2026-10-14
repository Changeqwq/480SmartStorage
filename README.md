# 480SmartStorage

Purdue ENGT 480 equipment lending and smart locker student project.
This first milestone implements English, responsive **prototype registration and
password login**, a protected dashboard, SQLite user profiles, Student/TA roles
and sign-out. An optional Purdue SAML SSO module is ready for future integration.
Storage and outdoor pickup are separate future workflows; no locker controls exist yet.

## Current status

The app runs locally now. Create a test account with a username, display name
and password, then sign in. Do not reuse your Purdue or personal passwords.
Usernames are case-insensitive and accept 3–32 letters, digits or underscores.
Passwords must contain 8–128 characters. Passwords are hashed with Werkzeug's
scrypt implementation; no plaintext password is stored.

Real Purdue sign-in requires **Purdue IT registration**,
an approved HTTPS host, attribute release and verified IdP metadata. Until those
are supplied, only prototype accounts can authenticate. There is no fake Purdue
login and no collection of Purdue credentials.

Protocol: SAML 2.0, using `python3-saml` (Shibboleth-compatible SP).
See [Purdue SSO setup and application request](docs/PURDUE_SSO_SETUP.md).

## Run on Windows

If the prepared `.venv` is already present on this computer, start directly with:

```powershell
cd 'D:\00 Edu\2026 Fall\ENGT 480\Repo\480SmartStorage'
.\.venv\Scripts\python.exe app.py
```

For a fresh clone:

Use Python 3.12 (recommended for the tested Windows XML security wheels):

```powershell
cd 'D:\00 Edu\2026 Fall\ENGT 480\Repo\480SmartStorage'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5000. Do not open the HTML templates directly.
If `.env` already exists, keep it instead of copying over it.

The initial development secret is generated once in ignored `instance/secret.key`.
In deployment, set a strong `SECRET_KEY` through server environment variables.
Database and session data are in ignored `instance/smartstorage.sqlite`.

## Developer commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m flask --app 'app:create_app' set-role local:tester ta
.\.venv\Scripts\python.exe -m flask --app 'app:create_app' set-role local:tester student
.\.venv\Scripts\python.exe -m flask --app 'app:create_app' cleanup-sessions
```

Role changes require server access and an existing user. Local identities use
`local:username`; future SSO identities use their released ePPN. Neither
email input nor a claimed TA/employee attribute grants TA permissions.
The Student role currently means the application's regular user role, **not**
proof of active student enrollment. Pilot membership can be restricted with
`ALLOWED_PRINCIPALS` and the IdP's application access policy.

## Routes

| Route | Behavior |
| --- | --- |
| `GET /login` | Login page |
| `GET/POST /register` | Create a prototype account |
| `POST /auth/local/login` | CSRF-protected username/password login |
| `POST /auth/saml/login` | CSRF-protected SP-initiated SSO |
| `POST /auth/saml/acs` | Validated SAML callback |
| `GET /auth/saml/metadata` | SP metadata after configuration |
| `GET /dashboard` | Authenticated user workspace |
| `POST /logout` | Revoke this app's session; Purdue's SSO session remains active |

## Deployment boundary

`app.py` runs Waitress on loopback. Deploy behind an HTTPS reverse proxy forwarding
to `127.0.0.1:5000`. Set `PUBLIC_BASE_URL` to the exact registered HTTPS origin.
The SAML validator uses that canonical origin instead of trusting forwarded headers.
The app must be hosted at the root of that origin, not a URL subpath. In SSO mode,
session cookies use `Secure`, `HttpOnly`, and `SameSite=None` to support the IdP's
cross-site POST. Login/logout use CSRF tokens; the callback uses the stored
AuthnRequest ID and a random RelayState. Authentication sessions expire after
8 hours and pending requests after 10 minutes. Signed assertions are verified
in strict mode, request correlation is required, and consumed assertion IDs
are retained for 24 hours against replay.

Local login allows 10 attempts per IP within a 10-minute window before throttling.
This is intended for a small prototype; production needs shared rate limits and
operational monitoring. There is no email verification, password recovery or
campus enrollment verification. Do not expose the prototype to the public Internet.
Local accounts have no access to future SSO accounts, even with matching names.
Set `LOCAL_AUTH_ENABLED=false` to disable registration and local login once you
switch to the approved SSO integration.

Purdue IT must confirm signature algorithms and certificate requirements before
activation. This is an integration MVP, not a completed campus deployment.
Real Purdue MFA, attribute release, certificate rollover and campus interoperability
must be tested with the approved non-production IdP application.

## Project structure

```text
app.py                     Flask routes, SAML validation, server-side sessions
templates/                 Login and protected dashboard
static/styles.css          Responsive interface; no external assets
saml/settings.example.json Deployment configuration template
docs/PURDUE_SSO_SETUP.md    Purdue IT request and acceptance checklist
tests/                     Authentication and route tests
```
