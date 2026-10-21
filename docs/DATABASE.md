# Database plan

## Prototype database

SmartStorage currently uses SQLite because the prototype must run on one laptop
without a separate database server. The database is created at
`instance/smartstorage.sqlite`, which is excluded from Git.

The normalized application tables are:

- `users`: authentication identity, hashed local password, role, and active state
- `items`: inventory details, location, total quantity, available quantity, and alert threshold
- `loans`: one checkout lifecycle, due date, status, and opaque pickup token
- `transactions`: immutable checkout and return audit records
- `sessions`: revocable server-side application sessions

Checkout and return use `BEGIN IMMEDIATE` so quantity validation and updates occur
inside one write transaction. A repeated return does not increment inventory twice.

## MariaDB migration

The linked `mariadb` Python package is a database driver. Installing it does not
convert SQLite SQL or create a server. Move to MariaDB only when the project has a
managed server, credentials, backup policy, and a deployment environment.

Required adapter work:

1. Move connection creation behind a database module and choose the backend from
   a server-side `DATABASE_URL` or equivalent settings.
2. Replace SQLite `?` parameters with the driver's parameter convention.
3. Replace SQLite-only statements such as `PRAGMA`, `BEGIN IMMEDIATE`, and
   `INSERT ... ON CONFLICT` with MariaDB equivalents.
4. Create versioned schema migrations rather than running additive startup SQL.
5. Implement checkout and return with a transaction plus `SELECT ... FOR UPDATE`
   on the item and loan rows.
6. Store database credentials only in deployment secrets, never in Git or `.env.example`.
7. Test concurrent checkout, deadlock retries, connection loss, backups, restores,
   and migration rollback before a pilot.

Keep SQLite for classroom demos until those operational requirements exist. The
business tables and foreign-key relationships are already shaped for migration.
