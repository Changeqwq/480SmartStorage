# 480SmartStorage

Purdue ENGT 480 equipment lending and smart locker student project.
This prototype implements English, responsive username/password authentication,
student equipment browsing and lending, QR pickup credentials, inventory updates,
transaction history, and a TA/Admin workspace. An optional Purdue SAML SSO module
remains available for future integration after campus approval.

Each account receives a unique numeric User ID from the database. Locker QR codes
use `P-ITEM_ID-USER_ID` for pickup and `R-ITEM_ID-USER_ID` for return. After a
student requests checkout, a TA must confirm locker drop-off before the pickup QR
is activated. The seven-day due period begins when the locker accepts the pickup.

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
Six demonstration inventory items are created when the database is empty.

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

To enable the TA/Admin prototype after registering an account:

```powershell
.\.venv\Scripts\python.exe -m flask --app 'app:create_app' set-role local:YOUR_USERNAME ta
```

Sign out and sign in again. The same command with `student` removes staff access.

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
| `GET /catalog` | Search and filter active inventory |
| `GET /items/<id>` | Item details and availability |
| `POST /items/<id>/checkout` | Reserve inventory and request TA drop-off |
| `GET /loans` | Current and historical user loans |
| `GET /loans/<id>/pickup-qr.png` | Pickup QR after TA drop-off |
| `GET /loans/<id>/return-qr.png` | Separate return QR after pickup |
| `POST /api/locker/scan` | Authenticated locker pickup/return event |
| `GET /admin` | TA/Admin inventory summary |
| `POST /admin/loans/<id>/confirm-dropoff` | TA locker drop-off confirmation |
| `GET /admin/items` | Add, edit, restock, or deactivate items |
| `GET /admin/transactions` | Filter the lending audit trail |
| `GET /admin/alerts` | Low-stock and overdue alerts |
| `GET /admin/users` | Manage roles and account status |
| `POST /logout` | Revoke this app's session; Purdue's SSO session remains active |

Checkout and return operations use immediate database transactions so concurrent
requests cannot silently create negative inventory or double-return an item.
QR codes contain the requested action, numeric Item ID, and numeric User ID. They
never contain a password. The code alone is not authorization: the locker gateway
must authenticate with `LOCKER_API_KEY`, and the backend verifies the current loan
state before accepting a pickup or return. Use a long random value in deployment.

## Test the locker QR workflow

A phone camera or ordinary QR scanner only decodes the QR image into text such as
`P-3-12`. It does not contact SmartStorage or change the loan. Until physical locker
hardware is connected, use PowerShell or Postman to simulate the locker gateway
sending that scanned code to the backend.

### Double-click launcher on Windows

Windows does not normally execute `.ps1` files when they are double-clicked. After
configuring `.env` and starting `app.py`, double-click `Run-Locker-Scan.cmd` in the
repository root. The launcher opens PowerShell, asks for the exact code printed
below the QR, sends it to the locker API, and keeps the result window open.

The `.cmd` launcher applies `ExecutionPolicy Bypass` only to that one PowerShell
process. It does not change the computer's global execution policy. Its underlying
script is `scripts/simulate-locker-scan.ps1` and can also be run directly:

```powershell
.\scripts\simulate-locker-scan.ps1
```

First, add a local prototype key to `.env`:

```env
LOCKER_API_KEY=prototype-locker-test-key-2026
```

Restart `app.py` after changing `.env`; environment settings are loaded at startup.
Complete checkout as a student, then sign in as a TA and confirm the locker drop-off.
The loan page will display a pickup code in this format:

```text
P-ITEM_ID-USER_ID
```

From a second PowerShell window, enter the exact pickup code printed below the QR.
Do not copy an Item ID or User ID from this README:

```powershell
$lockerHeaders = @{
    "X-Locker-Key" = "prototype-locker-test-key-2026"
}

$pickupCode = Read-Host "Enter the pickup code shown below the QR"

$lockerBody = @{
    code = $pickupCode
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://127.0.0.1:5000/api/locker/scan" `
    -Method Post `
    -Headers $lockerHeaders `
    -ContentType "application/json" `
    -Body $lockerBody
```

The response should identify the loan and report `checked_out`. Refresh the loan
page to see a separate return QR in the format `R-ITEM_ID-USER_ID`. Enter the exact
new return code printed below that QR and send it with the same command:

```powershell
$returnCode = Read-Host "Enter the return code shown below the QR"

$lockerBody = @{
    code = $returnCode
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://127.0.0.1:5000/api/locker/scan" `
    -Method Post `
    -Headers $lockerHeaders `
    -ContentType "application/json" `
    -Body $lockerBody
```

The response should report `returned`. Refresh the page to see **Return complete**
and confirm that inventory increased once. Repeating the return scan produces a
`409` response and does not increase inventory again.

The same request can be sent with Postman: use `POST`, select raw JSON for the body,
send `{"code":"PASTE_THE_EXACT_QR_CODE_HERE"}`, and add an `X-Locker-Key` header
matching `.env`.

Common responses:

- `401`: the `X-Locker-Key` header is missing or does not match `.env`.
- `409`: the code is invalid or the loan is not at the required pickup/return stage.
- `503`: `LOCKER_API_KEY` is blank; configure it and restart the app.

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

SQLite is the default database for the local prototype. The schema separates users,
items, loans, and immutable transaction records so it can later move to MariaDB.
The current code uses SQLite transactions and SQL syntax and must not be pointed at
MariaDB without the adapter work described in [Database migration](docs/DATABASE.md).
Installing the `mariadb` driver alone does not make the application compatible.

The hardware boundary and proposed locker gateway contract are documented in
[Hardware integration](docs/HARDWARE_INTEGRATION.md). The web backend will authorize
commands and issue opaque identifiers; a future gateway will own lock-board and
door-sensor communication.

Purdue IT must confirm signature algorithms and certificate requirements before
activation. This is an integration MVP, not a completed campus deployment.
Real Purdue MFA, attribute release, certificate rollover and campus interoperability
must be tested with the approved non-production IdP application.

## Project structure

```text
app.py                     Authentication, sessions, and application setup
inventory.py               Catalog, lending, QR, alerts, and staff workflows
templates/                 Responsive student and TA/Admin pages
static/styles.css          Responsive interface; no external assets
saml/settings.example.json Deployment configuration template
docs/PURDUE_SSO_SETUP.md    Purdue IT request and acceptance checklist
docs/DATABASE.md            MariaDB migration boundary
docs/HARDWARE_INTEGRATION.md Future locker gateway contract
tests/                     Authentication, authorization, and lending tests
```
