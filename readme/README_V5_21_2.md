# Abra NIP Monitoring Information System v5.21.2

## SBI Data Quality Center + Province-wide VaccTrack Monitor

This update adds two read-only operational monitoring views under:

**Administration → Operations**

### Data Quality
The Data Quality Center checks the current RHU accomplishment dataset across all municipalities and groups findings as **Critical**, **Warning**, or **Info**.

Checks include:
- duplicate Activity Date + School + Grade records;
- missing or invalid municipality, school, date, or grade fields;
- schools not found in the current SBI target roster;
- schools assigned to a different municipality;
- activity dates outside the configured official SBI period;
- negative values;
- grade/vaccine field mismatches;
- reason-code totals greater than deferred/refused outcomes;
- deferred/refused rows with no reason code entered;
- legacy non-workbook accomplishment records still active;
- cumulative vaccination totals above the loaded school target;
- RHUs without a current workbook submission;
- stale RHU workbook uploads during Live activity;
- finalized RHUs that still have unresolved Critical/Warning findings.

The findings can be filtered by severity, municipality, and finding type and downloaded as CSV.

### VaccTrack Monitor
The province-wide monitor compares the current RHU workbook data against the latest available official VaccTrack data for all Abra municipalities on one screen.

It shows:
- RHU workbook dose totals;
- VaccTrack dose totals;
- verified difference;
- RHU doses still pending verification because the RHU activity is newer than the latest VaccTrack data;
- latest RHU activity date;
- conservative all-grade VaccTrack-through date when all three grade sources are available;
- municipality status.

Statuses include:
- **Matched**
- **Discrepancy**
- **Pending Verification**
- **No VaccTrack Data**
- **No RHU Upload**

Selecting a municipality shows the six vaccine/grade metrics and school-level discrepancy or pending-verification details.

The existing freshness behavior is preserved: activity newer than the corresponding VaccTrack grade is not treated as a confirmed discrepancy.

## QA Admin
Both new monitoring views are read-only. QA Admin can inspect them without receiving any production-changing action.

## Deployment
Replace:

```text
admin/operations.py
```

Add:

```text
programs/sbi/admin_monitoring.py
```

No SQL migration is required.

Keep all previously applied migrations through `012_sbi_workbook_submission_control.sql`.

## Validation performed
- Both changed Python files pass `py_compile`.
- Pure-function tests covered a matched RHU, a pending-verification RHU, a confirmed discrepancy, and no-upload handling.
- Data-quality test coverage verified that clean data stays free of Critical findings and that RHUs without a workbook appear as informational findings.
