# Abra NIP Dashboard v5.20 — Operations, Training, Feedback and Performance

This update keeps the RHU encoder workflow from v5.19.3.1 unchanged while adding the supporting tools needed during rollout.

## What v5.20 adds

### Administration → Operations

The new **Operations** tab contains four tools:

1. **System Health**
   - checks the main SBI database tables;
   - shows the current dashboard version;
   - shows the active G1/G4/G7 VaccTrack source;
   - shows the latest report date available for each grade.

2. **RHU Rollout**
   - shows every RHU Encoder account;
   - shows the last login;
   - shows whether the temporary password has already been changed;
   - shows the first upload, latest activity date, number of batches, and rows uploaded;
   - provides a CSV download for rollout monitoring.

3. **Feedback**
   - receives feedback submitted from the RHU SBI page;
   - filters feedback by status and municipality;
   - lets the System Admin mark an item Open, In Review, or Resolved;
   - stores an optional admin note.

4. **Backup**
   - prepares a ZIP containing CSV exports of the main operational tables;
   - excludes password hashes from the account export;
   - can optionally include the larger raw VaccTrack row table;
   - does not change any database records.

### RHU Training / Practice Mode

A new collapsed **Training / Practice Mode — no data is saved** section appears above the normal RHU workflow tabs.

Encoders can:

- download a practice line list;
- validate a practice file using the same rules as a real upload;
- set a temporary practice baseline in the current browser session;
- upload another file and see Added / Modified / Removed / Unchanged results;
- create a practice follow-up workbook;
- reset the practice session at any time.

Training Mode never calls the production line-list import function.

### Built-in RHU feedback

A **Send Feedback / Report a Problem** form is available on the RHU SBI page. The form stores:

- username;
- municipality;
- role;
- category;
- page/area;
- dashboard version;
- the encoder's message.

The form reminds users not to include learner names, LRN, or other identifying information.

### Better import history

The RHU Corrections / History page now includes:

- total import batches;
- total rows processed;
- latest upload time;
- downloadable import-history CSV;
- per-batch change counts;
- downloadable batch change log.

Administration → Import Management also shows batch/row/change totals and can download the line-list import history.

### VaccTrack source and freshness

VaccTrack Check now shows a simple G1/G4/G7 table with:

- current source;
- latest report date available (Data Through).

Google Sheet fallback loading was also tightened so the dashboard fetches only the grade tabs that are actually missing from direct VaccTrack snapshots.

### Performance and UI cleanup

- cached data functions in `core/data.py` no longer show internal function names while loading;
- Google Sheet fallback work is reduced when only one or two grades need fallback data;
- mobile tab bars can scroll horizontally instead of being forced into a centered fixed-width layout;
- mobile metric cards, file uploaders, tables, and page padding were tightened for smaller screens.

## Database update

Run once in Supabase SQL Editor after the existing v5.19.3 migrations:

```text
supabase/009_sbi_operations.sql
```

This creates only the `sbi_user_feedback` table and its indexes.

This update does **not** add database-side RHU row-level security. The existing application-level municipality controls remain unchanged.

## Files to replace

```text
app.py
admin/dashboard.py
core/data.py
programs/sbi/linelist.py
programs/sbi/rhu_tracker.py
programs/sbi/help_content.py
programs/sbi/import_management.py
docs/SBI_RHU_Encoder_Full_Guide.md
docs/SBI_RHU_Encoder_FAQ.md
```

## Files to add

```text
admin/operations.py
programs/sbi/support.py
supabase/009_sbi_operations.sql
```

The patch also retains the v5.19.3 password-change files and migrations so it can be deployed cumulatively from v5.19.2 if needed.

## Recommended test after deployment

1. Run `supabase/009_sbi_operations.sql`.
2. Deploy the files.
3. Sign in as System Admin and open **Administration → Operations → System Health**.
4. Confirm the expected tables show Ready and review the G1/G4/G7 VaccTrack sources.
5. Open **RHU Rollout** and confirm the RHU accounts and login/password-change states appear.
6. Log in as a test RHU Encoder.
7. Open SBI → RHU Accomplishments and expand **Training / Practice Mode**.
8. Validate a dummy practice file and set it as the practice baseline.
9. Change one dummy outcome and upload it again; confirm the practice comparison shows the expected modification and does not create a real import batch.
10. Send a test feedback message.
11. Return to System Admin → Operations → Feedback and confirm the message appears.
12. Open Backup, prepare a backup, and confirm the ZIP downloads.
13. Open the site at phone width and confirm the top tabs scroll horizontally and the upload/guide areas remain usable.

## Validation performed

The modified Python files pass `py_compile` in the build environment. Live Supabase writes were not executed from this build environment, so test the feedback form and backup against the deployed project after running migration 009.
