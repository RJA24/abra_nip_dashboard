# Abra NIP Monitoring Information System v5.21.1.6

## SBI launch guard + post-activity correction period

This incremental patch adds two operational safeguards before production SBI reporting.

### 1. Pre-launch workbook-data guard

While the campaign is **Pre-Implementation**, Administration → Operations → SBI Control now checks for existing RHU workbook data and upload history.

If workbook data still exists, the page shows the affected municipalities and the number of workbook rows/history entries. When changing the campaign from **Pre-Implementation** to **Live**, the system will block the change unless either:

- the test data is cleared under **RHU Submissions → Pre-Implementation Cleanup**, or
- the System Admin explicitly confirms that the existing records were reviewed and are legitimate production data that should be kept.

This prevents forgotten test uploads from being carried silently into the live campaign.

### 2. New campaign status: Post-Activity Correction

The SBI campaign statuses are now:

1. **Pre-Implementation** — testing, RHU preparation, and test-data cleanup.
2. **Live** — normal field implementation and workbook uploads.
3. **Post-Activity Correction** — field activity has ended, but RHUs may still correct or submit late workbook data.
4. **Closed** — new workbook uploads are blocked; existing data and reconciliation remain viewable.

During **Post-Activity Correction**:

- RHUs may upload corrected or late versions of their workbook.
- The workbook remains a complete replacement snapshot for that RHU.
- Activity Date validation still applies. If the official range is Oct 15–Oct 31, an upload made on Nov 1 may contain Oct 15–Oct 31 activity dates, but it may not add a Nov 1 activity date.
- Final RHU submission remains available.
- A finalized RHU still requires System Admin reopening before another correction can be uploaded.

The system does not automatically change campaign status based on today's date. The System Admin intentionally moves the campaign from Live → Post-Activity Correction → Closed.

### End-date reminder

If the configured activity end date has already passed while the campaign is still **Live**, SBI Control now warns the System Admin to consider changing the status to **Post-Activity Correction**.

## Files to replace

- `admin/operations.py`
- `programs/sbi/campaign_control.py`
- `programs/sbi/aggregate_workbook.py`
- `programs/sbi/help_content.py`
- `docs/SBI_RHU_Encoder_Full_Guide.md`
- `docs/SBI_RHU_Encoder_FAQ.md`

## Database

**No new SQL migration is required.**

Keep the existing migrations already applied through `012_sbi_workbook_submission_control.sql`.

## Recommended test

1. Keep Campaign Status on **Pre-Implementation**.
2. Upload one dummy RHU workbook if no test data currently exists.
3. Open **Administration → Operations → SBI Control** and confirm the pre-launch warning lists that RHU.
4. Try changing status to **Live** without the confirmation checkbox. Confirm the system blocks the change.
5. Either clear the RHU test data or explicitly confirm that the existing data should be kept, then verify Live can be saved.
6. Change status to **Post-Activity Correction**.
7. As an RHU Encoder, upload a corrected workbook containing an Activity Date within the configured official range. Confirm it is accepted.
8. Try an Activity Date after the official end date. Confirm it is rejected.
9. Confirm final submission remains available during Post-Activity Correction.
10. Set status to **Closed** and confirm new workbook uploads are blocked.
