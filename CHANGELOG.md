# Changelog

## v5.22.0 — Production Documentation and Code Freeze

- Standardized production version labels to v5.22.0.
- Corrected the RHU campaign banner for Post-Activity Correction.
- System Health now separates required operational tables from optional legacy/archive SBI tables.
- Refreshed RHU guide/FAQ wording for the aggregate-workbook production workflow and automatic uploader clearing.
- Added master production documentation, Admin/Deployment/Schema guides, QA checklist, pre-launch checklist, and code-freeze policy.
- No database migration.

## v5.21.3 — Production Readiness and Bulk RHU Workbooks

- Added generation of all 27 municipality-specific RHU workbooks in one ZIP.
- Added target-roster fingerprint/version checks for package freshness.
- Added Production Readiness checks for accounts, rosters, database, campaign settings, VaccTrack, data quality, backup, and contingency package readiness.
- Added campaign date/status audit details.

## v5.21.2.1 — RHU Uploader Reset

- Clears the selected RHU workbook after a successful confirmed upload to reduce accidental duplicate submissions.

## v5.21.2 — Data Quality and VaccTrack Monitor

- Added province-wide Data Quality Center.
- Added province-wide VaccTrack reconciliation monitor with pending-verification handling for stale official extracts.

## v5.21.1.6 — Campaign Lifecycle Safeguards

- Added Post-Activity Correction status.
- Added pre-launch test-data guard before switching to Live.
- Kept activity-date enforcement tied to official campaign dates, allowing later corrections for dates inside the campaign period.

## v5.21.1 — Operational Controls

- Added Campaign Control, RHU submission status, RHU finalization/reopen, workbook version enforcement, upload history/restore, activity-date validation, and RHU announcement control.
- Added `012_sbi_workbook_submission_control.sql`.

## v5.21 — Offline Aggregate Workbook

- Replaced the active SBI learner-level workflow with one municipality-specific offline aggregate workbook per RHU.
- Added aggregate deferred/refused/reason fields through `011_sbi_offline_aggregate_workbook.sql`.
- Added workbook-generated VaccTrack G1/G4/G7 sheets and complete-snapshot upload behavior.

## Earlier releases

Earlier v5.13-v5.20 releases established RHU accounts, target dashboards, direct VaccTrack imports, historical learner/test workflows, Import Management, operational health/feedback/backup, Google fallback controls, QA Admin read-only access, and system renaming. Legacy learner/regional tables are retained only where needed for historical cleanup/audit.
