# Release and deployment checklist

## Current prototype release

- [ ] Review the diff on `feature/account-auth-foundation`.
- [ ] Confirm GitHub Actions frontend build and backend tests are green for the exact commit.
- [ ] Confirm `APP_SECRET` is supplied as a strong deployment secret; never rely on the development fallback.
- [ ] Confirm `CORS_ORIGINS` contains only the exact frontend origins.
- [ ] Confirm the backend uses a persistent writable disk for the SQLite demo database if persistence is required.
- [ ] Confirm no local database, credentials, access tokens, identity documents or private keys are committed.
- [ ] Confirm the frontend's `VITE_API_BASE_URL` points to the intended paper-trading API.
- [ ] Confirm health endpoint reports `PAPER_ONLY` and all real-money flags false.
- [ ] Confirm deposit and withdrawal endpoints return HTTP 403.
- [ ] Confirm the public site clearly labels all balances and fills as simulated.
- [ ] Confirm the compliance page does not claim an unverified licence, audit or approval.

## Production exchange launch — blocked

Every item below is a launch gate, not a suggestion. The prototype is not ready for real-money use.

### Legal and compliance
- [ ] Written legal analysis for each launch jurisdiction, entity, product and customer type.
- [ ] All required registrations, licences, permissions and regulator notifications verified against primary sources.
- [ ] AML/KYC, sanctions, PEP, transaction-monitoring, reporting, record-retention and complaint-handling obligations implemented and tested.
- [ ] Privacy notices, data protection impact assessments, retention/deletion rules and breach procedures approved.
- [ ] Customer terms, risk disclosures, asset ownership and insolvency treatment reviewed.

### Financial operations
- [ ] Qualified custody and banking arrangements selected and independently reviewed.
- [ ] Client asset segregation, proof/reconciliation controls and withdrawal governance documented.
- [ ] Production double-entry ledger independently reviewed; immutable audit evidence and external reconciliation implemented.
- [ ] Market data and execution rights documented.
- [ ] Order lifecycle, matching, settlement, fees, limits, error handling and market-integrity controls tested.
- [ ] No customer funds accepted before the legal and operational gates are signed off.

### Security and resilience
- [ ] MFA/passkeys and hardened session lifecycle implemented.
- [ ] Rate limiting, bot controls, account recovery and privileged-access controls implemented.
- [ ] Secrets manager, key management, encryption, security headers and dependency scanning configured.
- [ ] Threat model, secure code review and independent penetration test completed.
- [ ] Centralized monitoring, alerting, incident response and security escalation tested.
- [ ] Backups, restore tests, disaster recovery and business continuity exercises completed.
- [ ] Independent financial, custody and technology controls assessed.

### Deployment
- [ ] Separate development, staging and production environments.
- [ ] Reproducible dependency lockfiles and reviewed deployment artifacts.
- [ ] Database migrations and rollback plan tested.
- [ ] Production observability, uptime and on-call ownership assigned.
- [ ] Explicit approvals recorded for each jurisdiction, asset and feature.
- [ ] Live-money features remain default-deny until all launch gates are approved.

## Release decision

A green CI run confirms only the automated tests and build steps in that workflow. It does not mean the platform is licensed, audited, secure for customer funds, or approved to operate.
