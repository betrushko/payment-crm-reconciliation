# Payment and CRM reconciliation

Read-only reconciliation across BigQuery, payment providers and Salesforce. Incremental extraction, retry normalization, cross-source matching and review workbooks.

**Stack:** Python, SQL, BigQuery, Salesforce, PowerShell.

## What this demonstrates

Reusable implementation patterns for data integration, validation and maintainable automation. This is a curated, generalized portfolio edition. It contains no production exports, deployment history, internal access inventory or credentials. API names and source labels have been generalized where needed; configure the matching schema in your own sandbox before use.

## Getting started

Install Python dependencies with `python -m pip install -r requirements.txt`. Copy `config.example.json` to ignored `config.json`, configure your own project and sandbox, and adapt the example SQL dataset names. Credential paths must be supplied explicitly through configuration or `GOOGLE_APPLICATION_CREDENTIALS`. 

## Source map

- `README.md`
- `config.example.json`
- `requirements.txt`
- `scripts/reconcile_mexico_orders.py`
- `scripts/run_bigquery_extracts.py`
- `scripts/run_salesforce_extract.py`
- `scripts/run_weekly_reconciliation.ps1`
- `sql/bigquery_source_summary.sql`
- `sql/paymenthub_mexico_2026.sql`
- `sql/paymenthub_subscriptions_mexico.sql`
- `sql/paypal_mexico_2026.sql`
- `sql/salesforce_contacts_email_unique.soql`
- `sql/salesforce_opportunities_2026.soql`
- `sql/salesforce_recurring_donations_regular.soql`
- `sql/stripe_balance_transactions_mexico_2026.sql`
- `sql/stripe_charges_mexico_2026.sql`
- `sql/stripe_mexico_2026.sql`

## Validation

Python sources and JSON/XML configuration are checked locally. Apex test classes are retained where present; executing them requires a compatible sandbox. No Salesforce query, deployment, backfill or record mutation was performed during preparation. See [SECURITY.md](SECURITY.md) for data handling.
