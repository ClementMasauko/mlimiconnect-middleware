# Backup and restore runbook

Production must use a paid PostgreSQL compute plan with point-in-time recovery enabled. Free database instances are not acceptable because they do not include recovery. Keep encrypted logical exports outside the primary hosting account according to the organisation's approved retention policy.

## Operating schedule

- Confirm point-in-time recovery is active after every database plan or workspace change.
- Create and export a logical backup at least weekly and before a destructive migration.
- Record the backup timestamp, encryption location, operator, checksum, and expiry in the operations register. Never commit a backup or connection URL.
- Perform a restore drill into an isolated database at least quarterly.

## Restore drill

1. Create an isolated recovery database from point-in-time recovery or restore an exported archive with `pg_restore`.
2. Point a non-production checkout of this backend at the recovered database.
3. Run `python manage.py verify_database_restore`.
4. Run the backend test suite against a separate test database, then manually sample users, listings, orders, ledger postings, settlements, and audit logs.
5. Record recovery point objective, elapsed recovery time, evidence, and any corrective action. Delete the isolated recovery instance only after sign-off.

Do not overwrite the active production database during a drill. A production cutover requires an incident commander, a written rollback point, maintenance communication, and verification before DNS or service connection strings are changed.
