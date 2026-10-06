# QA Admin Read-Only Checklist — v5.22.0

Use a QA Admin account and verify:

- Administration is visible.
- System Health, SBI Control, RHU Submissions, Data Quality, VaccTrack Monitor, RHU Workbooks, Production Readiness, RHU Rollout, Feedback, Backup, Data Sync, Import Management, account/history/audit screens can be inspected as intended.
- Campaign status/dates/announcement cannot be changed.
- RHU submissions cannot be finalized/reopened/restored/cleared from QA Admin.
- Feedback status changes and other Admin writes are disabled.
- Account creation/update/deletion and password resets are unavailable.
- Supabase insert/update/delete/upsert protection remains active inside Admin read-only mode.
- QA Admin can still change its own password through the normal account workflow if applicable.

Any production-changing Admin control that remains usable by QA Admin is a release-blocking defect.
