# Abra NIP Monitoring Information System

Production baseline: **v5.22.4**  
Programs: **MR Supplemental Immunization Activity (MR SIA)** and **School-Based Immunization (SBI)**  
Primary deployment: Streamlit Community Cloud + Supabase + Google Sheets/VaccTrack sources.

## Purpose

The Abra NIP Monitoring Information System provides one provincial monitoring workspace for National Immunization Program activities in Abra. It combines campaign dashboards, target monitoring, RHU reporting, VaccTrack reconciliation, administrative controls, account management, audit history, feedback, and backups.

VaccTrack remains the official/final national SBI reporting source. The Abra system is used for local operational monitoring, RHU workbook consolidation, validation, reconciliation, and follow-up.

## User roles

- **System Admin** — full Administration access and production-changing actions.
- **QA Admin — Read Only** — can inspect Administration, reports, histories, readiness, and monitoring screens but cannot perform production-changing Admin actions.
- **RHU Encoder** — can open MR SIA and SBI and may submit SBI workbook data only for the municipality assigned to the account.
- Other viewer roles retain read access according to the application shell.

Authentication is application-managed through `user_accounts`; the deployment does not use Supabase Auth/JWT user identity for RHU-level database isolation. RHU write restrictions and QA read-only controls are enforced by the application/service layer.

## SBI production workflow

The active SBI reporting method is an **offline-first aggregate workbook**. Learner-level line-list reporting is not part of the production RHU workflow.

1. RHU downloads its municipality-specific SBI workbook.
2. RHU encodes aggregate accomplishments offline using one row per Activity Date + School + Grade.
3. School Name is selected from a dropdown; School ID and Barangay populate automatically.
4. VaccTrack G1/G4/G7 sheets calculate the values to encode into VaccTrack for a selected date.
5. RHU uploads its complete current workbook when internet is available.
6. The system previews Added / Modified / Removed / Unchanged records before confirmation.
7. After a successful upload, the file uploader clears automatically.
8. Corrections are made in the same workbook and the complete workbook is uploaded again.
9. The latest confirmed workbook becomes the RHU's current operational dataset.
10. The dashboard reconciles RHU workbook totals against the latest available VaccTrack data.

Workbook format identifier: `SBI-AGGREGATE-2026-v1`.

## SBI campaign lifecycle

Administration -> Operations -> SBI Control manages the campaign without code changes:

- **Pre-Implementation** — testing, RHU onboarding, workbook preparation, and test-data cleanup.
- **Live** — field implementation and routine uploads.
- **Post-Activity Correction** — field implementation has ended, but corrections/late uploads are allowed for Activity Dates inside the official campaign period.
- **Closed** — new RHU uploads are blocked.

The System Admin also controls the official activity start/end dates and RHU announcement text. Changes to campaign status and dates are written to the Admin Audit Log.

Before switching from Pre-Implementation to Live, the launch guard checks for remaining workbook-derived test data. The Admin must clear it or explicitly confirm that retained records are legitimate production data.

## Administration -> Operations

The production operations area contains:

- **System Health** — required operational tables, VaccTrack source status, and optional legacy/archive table visibility.
- **SBI Control** — campaign status, activity dates, announcement, and launch guard.
- **RHU Submissions** — municipality submission status, history, restore, finalization/reopen, and Pre-Implementation test-data cleanup.
- **Data Quality** — province-wide validation findings and actionable filters.
- **VaccTrack Monitor** — province-wide RHU workbook vs VaccTrack reconciliation with pending-verification logic.
- **Workbook Dashboard** — operational/provisional RHU workbook analytics using the same MR/Td, HPV, and Deferrals & Refusals outline as VaccTrack.
- **VaccTrack Dashboard** — official/final VaccTrack analytics plus municipality accomplishment and combined daily-activity overview charts.
- **RHU Workbooks** — generate all 27 municipality-specific workbooks in one ZIP.
- **Production Readiness** — pre-launch checks across accounts, schema, rosters, campaign settings, VaccTrack, data quality, backup, and workbook package readiness.
- **RHU Rollout** — account/login/password/upload rollout visibility.
- **Feedback** — RHU feedback inbox.
- **Backup** — export production data to a ZIP of CSV files; password hashes are excluded.

## VaccTrack handling

The system prefers the latest completed direct VaccTrack upload for each of G1, G4, and G7. An Admin-controlled Google Sheet fallback can be enabled for a grade with no direct snapshot.

Reconciliation distinguishes true discrepancies from stale official data. RHU activity later than the available VaccTrack `Data Through` date is shown as **Pending Verification** instead of a false discrepancy.

## Workbook safeguards

The uploader checks:

- workbook version;
- municipality ownership;
- required sheet/column structure;
- valid grade-specific fields;
- official activity-date range when enforcement is enabled;
- nonnegative counts and workbook consistency;
- current submission/finalization state.

The upload is a complete-snapshot replacement for that RHU. Upload history stores a restorable JSON snapshot and change counts.

## Data sources

Core sources include:

- `sbi_targets` — school roster and target population;
- `sbi_rhu_accomplishments` — current RHU aggregate operational data;
- `sbi_rhu_workbook_submissions` — workbook upload history/finalization/snapshots;
- `sbi_vacctrack_imports` + `sbi_vacctrack_rows` — official VaccTrack snapshots;
- `sbi_settings` — campaign/fallback operational settings;
- `sbi_user_feedback` — RHU feedback;
- `user_accounts` + `access_logs` — account and audit/access data.

Older learner-line-list/regional tables may remain for historical cleanup/audit. They are not required by the current aggregate-workbook SBI workflow.

See `docs/DATABASE_SCHEMA.md` for a table-by-table summary.

## Database migrations

v5.22.0 adds **no new SQL migration**.

For the aggregate-workbook production workflow, the deployment must already include the earlier SBI schema and the following later migrations:

- `011_sbi_offline_aggregate_workbook.sql`
- `012_sbi_workbook_submission_control.sql`

Do not rerun destructive old migrations or remove legacy tables solely to simplify the UI.

## Production deployment

1. Back up the current production data.
2. Verify GitHub/Streamlit secrets are intact.
3. Deploy only the files listed in `DEPLOY_ONLY_THESE_FILES.txt` for this release.
4. Wait for Streamlit to finish provisioning and confirm there is no Python traceback.
5. Sign in as System Admin and run **Production Readiness**.
6. Test System Admin, QA Admin, and one RHU Encoder account.
7. Regenerate the all-27-RHU workbook package after any target-roster or workbook-format change.
8. Keep Campaign Status at Pre-Implementation until the launch guard and readiness checks are resolved.

See `docs/DEPLOYMENT_GUIDE.md` and `docs/PRODUCTION_PRELAUNCH_CHECKLIST.md`.

## Security and privacy

- Do not encode learner names, LRN, or other individual identifiers in the aggregate SBI workbook.
- Do not place passwords/password hashes in exports or documentation.
- Backups intentionally exclude password hashes.
- RHU write isolation is application-enforced; do not describe the current deployment as user-level Supabase RLS.
- VaccTrack and health-program data should be accessed only through authorized accounts.

## Code freeze

v5.22.0 is the production code-freeze baseline for the 2026 SBI rollout. After acceptance testing, only blocking defects, security issues, or corrections required for safe operation should change production before rollout.

See `docs/CODE_FREEZE.md`.
