# Abra NIP Monitoring Information System v5.21.1

## SBI operational controls and submission safety

This is an incremental patch for v5.21. It adds the first production-readiness controls for the offline SBI workbook workflow.

### Added tonight

#### Administration → Operations → SBI Control
System Admin can set:
- **Pre-Implementation** — testing and preparation; workbook uploads remain allowed.
- **Live** — normal SBI implementation.
- **Closed** — RHUs can still view existing data and VaccTrack reconciliation, but new workbook uploads are blocked.
- optional official activity start/end dates;
- an RHU announcement shown inside SBI.

When an activity date range is enabled, workbook uploads containing dates outside that range are rejected before data are saved.

#### Administration → Operations → RHU Submissions
Shows all 27 municipalities with:
- upload status;
- latest upload;
- uploader;
- current row count;
- activity date range;
- workbook version;
- finalized status.

A municipality can be opened to view its workbook upload history.

System Admin can:
- **Reopen RHU Submission** after an RHU has finalized it;
- **Restore a Previous Workbook** from the retained aggregate snapshot.

A restore creates a new history entry. Previous history is not deleted.

#### RHU final submission
After an RHU has uploaded its complete current workbook, it can mark that workbook as **Final**.

Once finalized:
- further RHU workbook uploads are blocked;
- the existing data remain visible;
- a System Admin must explicitly reopen the submission before a correction can be uploaded.

#### Workbook version enforcement
Current workbook version:

`SBI-AGGREGATE-2026-v1`

The version is stored in the workbook Setup sheet. The uploader now verifies both:
- workbook municipality; and
- workbook version.

An old or wrong-RHU workbook is rejected before saving data.

#### Upload history / recovery
Every confirmed workbook upload now stores submission metadata and a restorable aggregate snapshot:
- file name;
- uploader;
- upload time;
- workbook version;
- row count;
- activity-date range;
- Added / Modified / Removed / Unchanged counts;
- current/finalized state.

Only aggregate accomplishment data are stored in these snapshots. No learner-level information is introduced.

#### Rollout status update
The RHU Rollout screen now uses the offline-workbook submission history instead of the old learner-line-list import history.

#### Feedback wording update
Feedback categories and pages now refer to the offline workbook workflow instead of the retired learner line-list workflow.

### Database update
Run once after v5.21 SQL `011`:

```text
supabase/012_sbi_workbook_submission_control.sql
```

This creates only:

```text
sbi_rhu_workbook_submissions
```

No previous SQL migrations are included in this incremental patch.

### Files to deploy
Replace:

```text
admin/operations.py
programs/sbi/aggregate_workbook.py
programs/sbi/rhu_tracker.py
programs/sbi/help_content.py
programs/sbi/support.py
docs/SBI_RHU_Encoder_Full_Guide.md
docs/SBI_RHU_Encoder_FAQ.md
```

Add:

```text
programs/sbi/campaign_control.py
supabase/012_sbi_workbook_submission_control.sql
```

### Deployment order

1. Make sure v5.21 is already deployed and SQL `011` has already been run.
2. Run `supabase/012_sbi_workbook_submission_control.sql` once.
3. Deploy only the files listed in this patch.
4. Restart/redeploy Streamlit.
5. Open Administration → Operations → SBI Control.
6. Keep the campaign as **Pre-Implementation** while testing.
7. Download a fresh RHU workbook. Older v5.21 test workbooks without the version marker will be rejected by the v5.21.1 uploader.

### Validation performed
- All Python files in the reconstructed current project compile successfully.
- The generated workbook contains six expected worksheets.
- The generated workbook contains the expected version marker `SBI-AGGREGATE-2026-v1`.
- Submission snapshot serialization/restoration data shape was tested.
- Reason-code-only changes are now detected as Modified during upload comparison.
- Campaign activity-date range validation was exercised.
