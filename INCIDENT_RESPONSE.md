# Incident response runbook

Use this runbook for security incidents, payment inconsistencies, data loss, or a sustained production outage. Keep customer data and credentials out of tickets and chat.

## Severity and ownership

- **SEV-1:** confirmed account compromise, incorrect money movement, material data exposure, or total outage. Assign an incident commander immediately and freeze risky operations.
- **SEV-2:** major feature unavailable, delayed payouts/messages, or degraded checkout with a workaround. Assign an owner within 30 minutes.
- **SEV-3:** limited degradation with no data-integrity or financial risk. Track through the normal engineering queue.

The incident commander owns decisions and the timeline. A separate communications owner publishes customer updates. A finance owner must approve any payout or ledger remediation.

## First 15 minutes

1. Record the UTC start time, reporter, affected services, release commit, and correlation IDs.
2. Check Render health, PostgreSQL, Sentry, the scheduled-maintenance job, provider dashboards, and the latest deployment logs.
3. For suspected payment fraud or ledger corruption, disable payment/payout execution and preserve database and provider evidence before changing records.
4. For suspected credential compromise, rotate the affected secret, revoke sessions, preserve audit logs, and review administrator actions.
5. Roll back only to a known-good immutable deployment. Never edit ledger rows or production database records manually.

## Recovery and verification

1. Use `BACKUP_AND_RESTORE.md` for recovery. Restore into an isolated database first and run `python manage.py verify_database_restore`.
2. Reconcile provider transactions, ledger postings, refunds, settlements, and payouts before re-enabling money movement.
3. Verify `/live/`, `/health/`, login, one read-only marketplace request, and one non-financial authenticated flow.
4. Monitor error rate, latency, outbox age, and payment reconciliation for at least 30 minutes after recovery.
5. Publish an all-clear only after the incident commander and relevant domain owner sign off.

## After the incident

Within two business days, document the customer impact, timeline, root cause, detection gap, recovery point/time, and corrective actions with owners and due dates. Preserve the report according to the organisation's retention policy and notify affected users or regulators when legally required.
