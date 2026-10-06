# Abra NIP Dashboard v5.16 — Direct VaccTrack Extract Import

This update removes the routine copy-paste step from downloaded VaccTrack extracts into the SBI Google Sheet.

## New official-data flow

```text
VaccTrack
  ↓ download extract
Administration → Data Sync
  ↓ upload / validate / confirm
Supabase official VaccTrack snapshot
  ↓
SBI dashboard + RHU VaccTrack Check
```

**VaccTrack remains the official/final SBI dataset.** The RHU learner line list remains the operational/provisional source used to calculate what RHUs should encode into VaccTrack and to reconcile against the latest official extract.

## Google Sheet compatibility

The existing `VaccTrackG1`, `VaccTrackG4`, and `VaccTrackG7` worksheets are retained as a fallback.

The dashboard chooses the source **independently for each grade**:

- latest completed direct upload in Supabase, when one exists;
- otherwise the corresponding Google Sheet worksheet.

This allows a gradual rollout. For example, G4 can already use a direct upload while G1 and G7 still use Google Sheets.

After the first successful direct import for a grade, future official updates for that grade should be made through **Administration → Data Sync → SBI VaccTrack Extract Import**. You no longer need to paste that grade's export into Google Sheets.

`Actual Targets` continues to come from the SBI Google Sheet.

## Real VaccTrack formats validated

The importer was built against the actual files downloaded from VaccTrack during the dry run:

| Grade | Actual download format | Rows | Abra rows | Latest report date |
| --- | --- | ---: | ---: | --- |
| G1 | HTML document saved with `.xls` extension | 2,930 | 665 | 2026-03-04 |
| G4 | HTML document saved with `.xls` extension | 2,768 | 585 | 2026-09-17 |
| G7 | UTF-8 CSV | 998 | 194 | 2026-03-04 |

The importer therefore does not assume that `.xls` means a normal binary Excel workbook. It also handles:

- VaccTrack HTML/XLS exports;
- standard `.xls` / `.xlsx` files readable by `python-calamine`;
- CSV exports;
- Excel-style values such as `="135813"`;
- extra/trailing whitespace and non-breaking spaces in headers;
- duplicate Grade 7 `Facility Name` header, which is normalized to `Updated date`;
- automatic G1/G4/G7 detection from the actual VaccTrack columns rather than the filename.

## Administration workflow

Go to:

```text
Administration
→ Data Sync
→ SBI VaccTrack Extract Import
```

Then:

1. Upload one, two, or all three downloaded grade extracts.
2. Review the validation preview.
3. Confirm that the files are the latest complete extracts.
4. Click **Import Validated VaccTrack Extracts**.
5. The dashboard reloads and begins using the new completed snapshot for each imported grade.

The preview shows:

- detected grade;
- file format;
- total rows;
- Abra rows;
- earliest/latest report date;
- current dashboard latest date;
- invalid report-date count;
- sample Abra rows.

Safeguards:

- exact same file hash cannot be imported twice;
- only one file per grade is allowed in one import action;
- an extract older than the data already available for that grade is blocked;
- import status remains `Importing` until every row is stored;
- failed imports are marked `Failed` and never become the official dashboard source;
- only `Complete` snapshots are read by the dashboard;
- import history is retained.

## RHU workflow

RHU Encoders do not upload official VaccTrack extracts. Their normal workflow stays:

```text
1. Upload Line List
2. VaccTrack Encoding
3. VaccTrack Check
```

For Step 3, the RHU waits until the NIP coordinator/System Admin has uploaded the latest available VaccTrack extraction, then clicks **Refresh VaccTrack Data**.

If the latest VaccTrack extraction has not yet reached the RHU activity date, reconciliation remains **Pending VaccTrack Verification** rather than producing a false discrepancy.

## Database migration

Run once in Supabase SQL Editor:

```text
supabase/005_sbi_vacctrack_imports.sql
```

It creates:

- `sbi_vacctrack_imports` — one metadata/history row per uploaded snapshot;
- `sbi_vacctrack_rows` — raw cleaned VaccTrack rows stored as JSONB for that snapshot.

## Files to replace

```text
core/data.py
admin/dashboard.py
programs/sbi/dashboard.py
programs/sbi/rhu_tracker.py
```

## File to add

```text
programs/sbi/vacctrack_import.py
supabase/005_sbi_vacctrack_imports.sql
```

No changes are required to the v5.15 line-list module/template for this update.

## Requirements

No new package is required if v5.14+ is already deployed. Keep:

```text
python-calamine>=0.3.1
```

The two real VaccTrack `.xls` samples supplied for this update are HTML-based and are parsed without an additional HTML library.

## Validation performed

- All modified Python files pass `py_compile`.
- The importer was executed against all three real VaccTrack sample downloads.
- It correctly identified G1, G4, and G7, row counts, Abra row counts, and report-date ranges.
- Grade 7's duplicate `Facility Name` / update-date header was normalized successfully.

A full deployed Streamlit + Supabase import was not run from this build environment, so run SQL `005` first and test one direct import in Administration before the production activity.
