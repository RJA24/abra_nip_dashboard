# v5.21.3 Admin Test Checklist

## RHU Workbooks

- Open Administration → Operations → RHU Workbooks.
- Confirm all 27 municipalities appear in the manifest.
- Confirm every RHU shows at least one school.
- Click **Generate All 27 RHU Workbooks**.
- Download the ZIP and confirm it contains 27 `.xlsx` workbooks plus `WORKBOOK_MANIFEST.csv` and `README.txt`.
- Open at least two workbooks from different municipalities and confirm the School Name dropdown contains only that RHU's schools.

## Production Readiness

- Open Administration → Operations → Production Readiness.
- Run the readiness check.
- Confirm the database, RHU accounts, target roster, campaign dates, VaccTrack, Data Quality, backup, and offline-workbook package checks are shown.
- If password changes or other operational items are incomplete, confirm they appear as **Needs Review** rather than being hidden.
- Confirm genuine configuration/schema failures appear as **Blocked**.
- Download the readiness CSV.

## Campaign date audit

- In SBI Control, change the activity end date temporarily and save.
- Open Administration → Audit Log and confirm the entry records the old and new activity-date range.
- Restore the intended official activity dates and save again.

## Final

- Prepare a Backup ZIP under Operations → Backup.
- Generate the All RHU Workbook ZIP.
- Re-run Production Readiness and verify those two checks change to Ready within the same session.
