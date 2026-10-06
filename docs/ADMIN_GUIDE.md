# System Administrator Guide — v5.22.0

## Daily starting point

Open **Administration -> Operations**. During rollout, the most useful tabs are SBI Control, RHU Submissions, Data Quality, VaccTrack Monitor, RHU Rollout, and Production Readiness.

## SBI Control

Use this screen to manage the campaign without changing code.

### Campaign statuses

- **Pre-Implementation**: testing and cleanup.
- **Live**: field implementation.
- **Post-Activity Correction**: activities have ended, but RHUs may still upload corrections whose Activity Dates remain inside the official campaign period.
- **Closed**: blocks RHU workbook uploads.

Set the official Activity Start Date and Activity End Date and enable enforcement. If Region changes the official schedule, update the dates here; subsequent uploads are validated against the revised period. The change is recorded in the Admin Audit Log.

Use the RHU Announcement field for short operational notices such as the latest VaccTrack data-through date or an upload deadline.

## Pre-Implementation test cleanup

Under **RHU Submissions**, select the RHU and use the Pre-Implementation cleanup control to remove workbook-derived test accomplishment data and its workbook history. The control is intentionally unavailable after the campaign leaves Pre-Implementation.

Before switching to Live, the launch guard checks for remaining workbook-derived data and requires you to clear it or explicitly confirm it as legitimate production data.

## RHU Submissions

Use this page to review current submission state and upload history by municipality.

- Reopen a finalized RHU when a legitimate correction is required.
- Restore an earlier workbook snapshot when the current upload is incomplete or incorrect.
- Finalization is intended only at the end of reporting, not after each routine upload.

## Data Quality

Resolve **Critical** findings before launch or final closure. Review **Warning** findings and confirm whether they are legitimate.

Typical checks include invalid/missing keys, duplicate scopes, wrong municipality/school relationships, activity dates outside the official period, grade/vaccine inconsistencies, target overages, stale uploads, and old active source types.

## VaccTrack Monitor

Use the province-wide monitor to identify RHUs that are Matched, Discrepant, Pending Verification, missing RHU data, or missing official VaccTrack data.

Do not treat a newer RHU activity date as a discrepancy when the latest VaccTrack extract has not yet reached that date. The system marks those rows Pending Verification.

## RHU Workbooks

Generate the all-27-RHU ZIP after the target roster is final. Regenerate it after any target-roster correction or workbook-version change. Distribute only the workbook intended for each RHU.

## Production Readiness

Run the readiness scan before rollout and after important configuration/data changes. A green **READY FOR SBI IMPLEMENTATION** result requires all automated items to be Ready in the current session.

Password activation may remain a Needs Review item until all RHUs have changed their temporary passwords. Follow up with inactive RHUs before rollout.

## Backup

Prepare and download a final backup before launch and again before major cleanup/closure activities. Password hashes are excluded from the backup export.

## QA Admin

QA Admin may inspect Administration and production monitoring but must not mutate production settings/data. If a production-changing control appears enabled for QA Admin, treat it as a release-blocking defect.

## Legacy Import Management

Older learner-line-list/manual/regional cleanup screens are retained for historical test-data cleanup only. They are not part of the current RHU reporting workflow. Do not revive those workflows for 2026 SBI production reporting.
