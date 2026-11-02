# Future hardware integration

## Boundary

The Flask backend should not send serial or Modbus commands directly to a lock
board. A locker gateway at the pickup site should translate authenticated backend
commands into the selected hardware protocol and report physical state changes.

```text
Web app → Backend authorization → Locker gateway → Lock controller → Lock / sensors
```

The web app generates three action-specific QR formats:

- `D-ITEM_ID-STUDENT_ID-TA_ID` for TA locker drop-off (TA-only page)
- `P-ITEM_ID-USER_ID` for pickup
- `R-ITEM_ID-USER_ID` for return

The numeric User ID is an application identifier, not a Purdue identifier. A code
contains no password, but it is predictable and must not be treated as a secret.
The locker gateway authenticates to the backend with `X-Locker-Key`, then sends the
scanned code to `POST /api/locker/scan`. A D scan verifies that the supplied TA ID
still belongs to an active TA account and automatically confirms locker drop-off.
The backend accepts pickup only after that scan and accepts return only after pickup.
Duplicate scans do not repeat inventory changes.

Manual checkout is a separate web workflow. The TA selects manual checkout and
confirms the face-to-face handoff. It skips D and P scans, starts the seven-day loan,
and still requires the student to use the R code for locker return.

```http
POST /api/locker/scan
X-Locker-Key: <LOCKER_API_KEY>
Content-Type: application/json

{"code":"P-3-12"}
```

A successful response contains the loan ID and new status. Store a long random
`LOCKER_API_KEY` outside source control and rotate it if the gateway is replaced.

## Proposed gateway contract

Each command should contain:

- `commandId`: unique idempotency identifier
- `lockerId` and `compartmentId`: physical target
- `action`: initially `unlock`
- `authorizedLoanId`: backend authorization context
- `expiresAt`: short command lifetime

The gateway should report:

- `command.accepted`, `command.completed`, or `command.failed`
- `door.opened` and `door.closed`
- `lock.secured`
- `device.heartbeat`
- `device.error`

Door closed, lock secured, and item present are separate states. Until an item
presence sensor is selected, the application should treat user/TA confirmation as
the business event and record the missing independent verification.

## Implementation sequence

1. Select the lock board protocol and obtain official documentation.
2. Build a two-door gateway simulator with the event contract above.
3. Add compartment assignment and short-lived unlock authorization to the backend.
4. Connect the simulator, then one control board and two locks.
5. Test expired QR codes, duplicate commands, simultaneous requests, open doors,
   disconnected gateways, restart recovery, and power loss.
6. Add the real hardware adapter behind the same gateway interface.

Do not add vendor-specific serial commands to `app.py` or `inventory.py`. Keep the
hardware adapter isolated so the locker model can change without rewriting the
web and lending workflows.
