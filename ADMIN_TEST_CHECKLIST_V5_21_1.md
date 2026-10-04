# v5.21.1 Admin Test Checklist

## SBI Control
- Open Administration → Operations → SBI Control.
- Confirm QA Admin can view controls but cannot save changes.
- As System Admin, keep status on Pre-Implementation for testing.
- Save an announcement and confirm it appears for an RHU Encoder.
- Enable an activity date range and confirm a workbook row outside the range is rejected.
- Return the desired dates/settings after testing.

## Workbook version safety
- Download a fresh workbook from an RHU account.
- Confirm Setup shows `SBI-AGGREGATE-2026-v1`.
- Confirm the fresh workbook uploads normally.
- Try an older workbook without the version marker and confirm it is rejected.
- Try a workbook downloaded under another municipality and confirm it is rejected.

## Submission history
- Upload a workbook and confirm the RHU appears under Administration → Operations → RHU Submissions.
- Confirm the status table shows upload time, row count, activity-date range, uploader and version.
- Edit one count and re-upload; confirm a second history entry appears.
- Confirm the previous history entry is retained.

## Finalization / reopen
- As RHU Encoder, mark the current workbook as Final.
- Confirm Step 2 blocks another upload.
- As System Admin, reopen the RHU submission.
- Confirm the RHU can upload again.

## Restore
- Upload at least two different workbook versions for one test RHU.
- In RHU Submissions, select an older non-current submission.
- Confirm restore only after checking the confirmation box.
- Verify My Accomplishments matches the restored snapshot.
- Verify a new history entry is created and the older history remains.

## Campaign Closed
- Set Campaign Status to Closed.
- Confirm RHUs can still open My Accomplishments and VaccTrack Check.
- Confirm new workbook uploads are blocked.
- Return status to Pre-Implementation after the test.
