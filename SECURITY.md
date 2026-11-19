# Security policy

## Scope

This repository currently contains a paper-trading prototype. Do not submit real customer data, identity documents, credentials, API keys, private keys, seed phrases or funds.

## Reporting

If you discover a security issue, do not publish exploit details or sensitive data in a public issue. Contact the repository owner through a private channel and include:
- affected commit or deployment;
- concise impact description;
- safe reproduction steps;
- suggested mitigation, if known.

## Current security boundaries

- Real deposits and withdrawals are disabled in the API.
- Order execution and balances are simulated only.
- KYC submission is a demonstration declaration, not verification.
- Admin endpoints require a server-side role; do not expose admin credentials.
- `APP_SECRET` must be set in each deployment environment and kept out of source control.
- SQLite and the current authentication implementation are not approved for production financial services.

## Before production

A production threat model, secure development review, independent penetration test, dependency and secret scanning, incident response process, backup/recovery exercise and qualified legal/compliance review are mandatory.
