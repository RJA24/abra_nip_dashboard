# Abra NIP Monitoring Information System v5.21.3

## Production Operations Batch

This release finishes the planned bulk RHU workbook package and adds a production-readiness screen for SBI launch preparation.

### Administration → Operations → RHU Workbooks

- Reviews the current target roster for all 27 Abra RHUs.
- Shows the number of schools included for each municipality.
- Blocks package generation if an RHU has no school roster.
- Generates one ZIP containing all 27 municipality-specific SBI offline workbooks.
- Adds `WORKBOOK_MANIFEST.csv` and a short `README.txt` inside the ZIP.
- Every generated workbook uses the current `SBI-AGGREGATE-2026-v1` template.

### Administration → Operations → Production Readiness

The readiness scan checks:

- required operational Supabase tables;
- 27 RHU Encoder accounts and municipality coverage;
- RHU temporary-password changes;
- SBI target roster coverage for all 27 RHUs;
- successful workbook generation for all RHUs;
- configured official activity start/end dates;
- valid campaign status;
- VaccTrack source availability for G1, G4, and G7;
- current Data Quality Critical/Warning findings;
- remaining workbook-derived data during Pre-Implementation;
- whether a final backup has been prepared in the current session;
- whether the all-27-RHU workbook ZIP has been prepared in the current session.

Results are classified as `Ready`, `Needs Review`, or `Blocked` and can be downloaded as CSV.

The screen shows **READY FOR SBI IMPLEMENTATION** only when no checks remain in Needs Review or Blocked status.

### Campaign date-change audit trail

Saving SBI Campaign Control now records meaningful changes in the existing Administration Audit Log, including:

- campaign status changes;
- previous and new activity start/end dates;
- announcement updates;
- explicit confirmation when pre-launch workbook data is reviewed and intentionally retained when switching to Live.

This is useful if the Regional Office later extends or changes the official SBI implementation period.

## Deployment

Replace/add only:

```text
admin/operations.py
programs/sbi/production_readiness.py
```

No SQL migration is required. Keep the existing database migrations through `012`.
