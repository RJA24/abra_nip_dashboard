# Abra NIP Dashboard v5.20.2 — QA Admin Read-Only Mode

## What changed

A new **QA Admin** role can inspect the same Administration area as a System Admin without being allowed to change production data.

QA Admin can open:

- MR SIA
- SBI
- Administration → Overview
- Operations / System Health / RHU Rollout / Feedback / Backup
- Data Sync and VaccTrack validation preview
- Import Management
- Map Labels
- RHU Accounts
- Admin Accounts
- Login History
- Audit Log

Production-changing controls are disabled for QA Admin, including target sync, VaccTrack import, import deletion, account creation/reset/status changes, map-label saves/resets, Google Sheet fallback changes, and feedback status updates. A read-only Supabase wrapper is also applied inside Administration as a second safety layer.

Downloads and read-only exports remain available. VaccTrack files may be uploaded for parsing/validation preview, but the Import button is disabled.

## Create a QA account

Sign in as **System Admin**, then open:

`Administration → Admin Accounts → QA Admin Accounts`

Use **Create QA Admin** and give the tester the temporary password. The tester is required to change that temporary password on first login.

## Deployment

No new SQL migration is required. The existing `user_accounts.role` text field is used for the new `QA Admin` role.

Replace the files in this package using the same folder paths. This package is cumulative from v5.20.1.1.

## Validation

- Python files were compiled with `py_compile`.
- QA Admin routing was checked so Administration is available to `System Admin` and `QA Admin`, while other roles remain blocked.
- Production-changing Administration controls have read-only guards, and the QA Admin Administration session uses a write-blocking Supabase proxy as an additional safeguard.
