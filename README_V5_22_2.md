# Abra NIP Monitoring Information System v5.22.2

## RHU Workbook Dashboard + VaccTrack Reconciliation

This is an incremental patch on top of **v5.22.1.3**.

### What changed

For System Admin / QA Admin, the existing **SBI → RHU Accomplishments** area now has two sub-tabs:

1. **Workbook Dashboard**
   - Reads the current `sbi_rhu_accomplishments` workbook rows.
   - Uses only `source_type = workbook`; legacy/manual rows are excluded.
   - Follows the main SBI municipality and reporting-period filters.
   - Shows RHUs reporting, schools with data, activity rows, latest activity date.
   - Shows G1 MR, G1 Td, G4 HPV Dose 1, G4 HPV Dose 2, G7 MR, and G7 Td totals.
   - Shows current effective-target coverage in an expandable section.
   - Shows municipality summary, daily activity trend, school totals, deferrals, and refusals.
   - Provides CSV downloads.

2. **VaccTrack Reconciliation**
   - Surfaces the existing RHU Workbook vs VaccTrack reconciliation beside the workbook dashboard.
   - Shows province summary, municipality comparison, daily discrepancy tally, and school-level reconciliation.
   - Preserves freshness-aware logic: RHU activity newer than the available VaccTrack grade is **Pending VaccTrack Verification**, not a false discrepancy.

### RHU Encoder behavior

No RHU encoding/upload workflow was changed. RHU Encoder and RHU QA Encoder accounts continue to see their existing workbook download/upload/check workflow.

### Database

**No SQL migration.** Keep migrations through `012`.

### Version

Visible system version is updated to **v5.22.2**.
