# Abra NIP Monitoring Information System v5.22.4

## Workbook / VaccTrack Dashboard Parity

This is an incremental dashboard-only update on top of **v5.22.3**.

### Workbook Dashboard

The operational/provisional RHU Workbook Dashboard now follows the same analytical outline as the official VaccTrack Dashboard from top to bottom:

1. **MR & Td (Grades 1 & 7)**
   - Combined Grades 1 & 7
   - Grade 1
   - Grade 7
   - targets and coverage
   - municipality or school performance
   - municipality coverage maps
   - daily activity trend
   - cumulative vaccination trend
   - daily tally sheets
   - school-level performance
   - raw CSV export

2. **HPV (Grade 4)**
   - target and dose coverage
   - municipality or school performance
   - municipality coverage maps
   - daily activity trend
   - cumulative dose trend
   - daily tally sheets
   - school-level performance
   - raw CSV export

3. **Deferrals & Refusals**
   - total deferred/refused
   - vaccine/dose summary
   - daily trend
   - Reason 01–19 analysis
   - municipality/barangay outcome summary
   - CSV export

The workbook remains an **operational/provisional** source. VaccTrack remains the **official/final** SBI source.

### VaccTrack Dashboard

Two useful overview visuals from the Workbook Dashboard are now also shown at the top of VaccTrack Dashboard:

- **Vaccination Accomplishments by Municipality** — G1 MR, G1 Td, G4 HPV1, G4 HPV2, G7 MR, and G7 Td.
- **Daily Activity Trend** — the same six indicators over VaccTrack Report Date.

### Reason-code support

Workbook `reason_counts` JSON is expanded into canonical Reason 01–19 columns so the Workbook Dashboard can use the same missed-vaccination reason analysis as VaccTrack.

### Database

**No SQL migration.** Keep the existing production schema through migration `012_sbi_workbook_submission_control.sql`.

### RHU workflow

No RHU encoding, workbook generation, upload, finalization, or reconciliation workflow was changed.

### Files to deploy

Replace:

```text
programs/sbi/dashboard.py
programs/sbi/workbook_dashboard.py
admin/operations.py
programs/sbi/support.py
programs/sbi/help_content.py
CHANGELOG.md
```

Optional documentation file:

```text
README_V5_22_4.md
```

### Validation

- Changed Python files compile successfully.
- Workbook event normalization was tested for G1 MR/Td, G4 HPV, and Reason 01–19 extraction from both dictionary and JSON-string payloads.
- No database schema changes are required.
