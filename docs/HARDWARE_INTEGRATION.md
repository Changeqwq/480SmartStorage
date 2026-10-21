# Future hardware integration

## Boundary

The Flask backend should not send serial or Modbus commands directly to a lock
board. A locker gateway at the pickup site should translate authenticated backend
commands into the selected hardware protocol and report physical state changes.

```text
Web app → Backend authorization → Locker gateway → Lock controller → Lock / sensors
```

The current QR credential contains an opaque loan URL. It exposes no password,
inventory record, or user details. It is suitable for prototype scanning, but it
does not unlock hardware. A production unlock flow must check the signed-in user,
loan status, assigned compartment, token expiry, and gateway state on the server.

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
3. Add compartment assignment and expiring unlock authorization to the backend.
4. Connect the simulator, then one control board and two locks.
5. Test expired QR codes, duplicate commands, simultaneous requests, open doors,
   disconnected gateways, restart recovery, and power loss.
6. Add the real hardware adapter behind the same gateway interface.

Do not add vendor-specific serial commands to `app.py` or `inventory.py`. Keep the
hardware adapter isolated so the locker model can change without rewriting the
web and lending workflows.
