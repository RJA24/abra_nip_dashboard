# Deployment Guide — v5.22.0

## Scope

This release is an incremental production-cleanup/documentation release. It adds no database migration.

## Before deployment

1. Download a current data backup from Administration -> Operations -> Backup.
2. Confirm the GitHub repository/branch used by Streamlit Community Cloud.
3. Confirm Streamlit Secrets still contain the required Supabase and Google Sheet configuration.
4. Confirm migrations through the currently deployed SBI schema are present. The aggregate-workbook workflow requires `011_sbi_offline_aggregate_workbook.sql` and `012_sbi_workbook_submission_control.sql`.
5. Do not delete legacy SBI tables solely because they are no longer active in the RHU workflow.

## Deploy

Replace/add only the files listed in `DEPLOY_ONLY_THESE_FILES.txt`.

Do not upload old SQL files as part of this incremental release. There is no v5.22 SQL migration.

## After deployment

1. Wait for Streamlit Community Cloud to finish provisioning.
2. If logs stop at `Provisioning machine`, `Preparing system`, or `Spinning up manager process` across multiple apps, treat it as infrastructure until an application traceback appears.
3. Sign in as System Admin.
4. Open Operations -> System Health and confirm required operational components are ready.
5. Run Production Readiness.
6. Sign in as QA Admin and verify Admin production-changing controls remain disabled/read-only.
7. Sign in as an RHU Encoder and verify:
   - municipality-specific workbook download;
   - School Name dropdown and automatic School ID/Barangay;
   - workbook upload/validation;
   - successful upload clears the file uploader;
   - VaccTrack Check works;
   - campaign-status banner is correct.
8. Return to System Admin and confirm the upload appears in RHU Submissions and Data Quality/VaccTrack Monitor.

## Rollback

If a blocking code defect appears immediately after deployment, restore the previous Git commit/revision. Do not roll back database migrations 011/012 merely to roll back application code.

Use workbook history/restore for a bad RHU workbook upload; do not manually edit production rows unless the Admin tools cannot safely recover the state.
