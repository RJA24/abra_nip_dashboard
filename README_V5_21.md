# Abra NIP Monitoring Information System v5.21

## SBI Offline Aggregate Workbook

v5.21 replaces the active RHU learner-level line-list workflow with one offline-first aggregate workbook.

### Final RHU workflow

1. **Offline Workbook** — download one municipality-specific SBI workbook and keep using it throughout the activity.
2. **Upload Current Workbook** — upload the complete current workbook when internet is available.
3. **VaccTrack Check** — compare the latest RHU workbook totals with the latest official VaccTrack extract.

VaccTrack remains the official/final national SBI dataset.

## Workbook design

The downloaded workbook contains:

- `Setup` — municipality, visual instructions, and workbook reminders.
- `Accomplishments` — offline activity encoding by Activity Date + School + Grade.
- `VaccTrack G1` — daily Grade 1 values arranged using VaccTrack field names.
- `VaccTrack G4` — daily Grade 4 HPV accomplishment values arranged using VaccTrack field names. G4.A (Actual total Grade 4 Female Students) is intentionally omitted from the RHU workbook view.
- `VaccTrack G7` — daily Grade 7 values arranged using VaccTrack field names.
- hidden `Reference` — RHU school roster and actual targets.

The workbook supports G1/G7 MR/Td counts, G4 HPV counts, deferred/refused totals, and VaccTrack reason codes 01–19.

Each VaccTrack sheet has a Report Date cell. Changing the date recalculates the school-level values for that day using `SUMIFS` formulas.

### Workbook protection

The generated workbook now protects non-input cells against accidental edits. In `Accomplishments`, only Activity Date, School ID, Grade Level, vaccine counts, deferred/refused counts, and reason-code cells are editable. On each VaccTrack sheet, only the Report Date cell is editable. Setup, Reference, formulas, auto-filled school fields, targets, Row Check, and VaccTrack calculated values are locked. Editable cells are highlighted light yellow.

The RHU-facing VaccTrack sheets intentionally show only the information the encoder needs for manual VaccTrack entry: the selected **Report Date**, **School**, the grade-specific VaccTrack fields, and reason codes 01–19. Region, Province, Municipality, Barangay, Facility Name, and School ID are not shown because they are already known/selected in VaccTrack and only add clutter to the offline encoding aid.

### Visual Setup guide

The `Setup` sheet is designed as the RHU quick-start page. It includes:

- a large **HOW TO USE THIS FILE** section with four numbered steps;
- separate **DO** and **DON'T** boxes;
- a quick explanation of what each workbook sheet is for;
- a **BEFORE YOU UPLOAD** warning that the workbook must contain the RHU's complete current data; and
- a reminder that VaccTrack remains the official/final national reporting source.

## Corrections

The workbook is the RHU working record.

For a correction:

1. edit the `Accomplishments` sheet;
2. save the same workbook;
3. upload the complete workbook again;
4. review Added / Modified / Removed / Unchanged;
5. confirm the replacement.

The latest confirmed workbook is treated as the RHU's complete current dataset. Records no longer present in the workbook are removed after confirmation.

## Privacy simplification

The active SBI RHU workflow no longer requires:

- learner names;
- LRN;
- System Learner ID;
- learner-level vaccination records.

The old learner-line-list code and database tables may remain in the repository/database for legacy cleanup and audit history, but they are no longer shown in the normal RHU workflow.

## Database update

Run once in Supabase SQL Editor:

```text
supabase/011_sbi_offline_aggregate_workbook.sql
```

This adds aggregate deferred/refused fields and a JSONB reason-count field to the existing `sbi_rhu_accomplishments` table.

Do not rerun or re-deploy old SQL migrations for this update.

## Files to deploy

Replace:

```text
admin/operations.py
programs/sbi/rhu_tracker.py
programs/sbi/help_content.py
programs/sbi/import_management.py
programs/sbi/support.py
docs/SBI_RHU_Encoder_Full_Guide.md
docs/SBI_RHU_Encoder_FAQ.md
```

Add:

```text
programs/sbi/aggregate_workbook.py
supabase/011_sbi_offline_aggregate_workbook.sql
```

No `app.py`, `core/data.py`, VaccTrack importer, or previous SQL files are included in this incremental patch.

## Existing dependencies

No new Python package is required. The workflow uses the existing project dependencies:

```text
python-calamine
xlsxwriter
```

## Recommended deployment test

1. Run SQL `011` once.
2. Deploy the files in this patch.
3. Sign in with one RHU Encoder test account.
4. Open SBI → RHU Accomplishments → `1. Offline Workbook`.
5. Download the RHU workbook and confirm only that municipality's schools are available.
6. Add dummy G1, G4, and G7 rows for at least two dates.
7. Change the Report Date in each VaccTrack sheet and verify the daily totals.
8. Upload the workbook in `2. Upload Current Workbook`.
9. Review the Added / Modified / Removed / Unchanged preview and confirm.
10. Edit one count, remove one dummy row, add another row, then upload again and verify the preview.
11. Open `My Accomplishments` and confirm the current data matches the workbook.
12. Open `3. VaccTrack Check` and verify reconciliation still works.

## Version

Administration / feedback version label: `v5.21`.
