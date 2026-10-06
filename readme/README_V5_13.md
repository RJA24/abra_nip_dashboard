# Abra NIP Dashboard v5.13

## RHU Accomplishment Tracker + VaccTrack Reconciliation

This update removes the Vaccine Requirements workflow from the SBI interface and reuses RHU accounts for a lighter operational purpose: RHUs encode a small accomplishment summary in the Abra NIP Dashboard while continuing to encode the official report in VaccTrack.

**VaccTrack remains the official/final SBI dataset.** The RHU Tracker is an independent cross-check to identify missing or inconsistent VaccTrack encoding.

## What changed

### RHU login and dashboard access
- RHU Encoder accounts now land on the normal NIP main program selector after login.
- RHUs can open both **MR SIA** and **SBI**.
- RHUs retain normal read access to **All Municipalities (Abra)** and municipality-level dashboard data.
- RHUs are **not** given Administration access.
- Only RHU accomplishment **writes** are locked to the account's assigned municipality.

### SBI RHU Accomplishments
The former **Vaccine Requirements** tab is replaced with **RHU Accomplishments**.

For RHU Encoder accounts it contains:
1. **Accomplishment Entry**
2. **My Accomplishments**
3. **VaccTrack Check**

RHU encoding fields are intentionally small:
- Grade 1: MR Male, MR Female, Td Male, Td Female
- Grade 4: HPV Dose 1, HPV Dose 2
- Grade 7: MR Male, MR Female, Td Male, Td Female

No deferrals/refusals are encoded here. Those remain in VaccTrack only.

Blank means **not encoded**. Zero means the RHU explicitly confirms zero vaccinations.

### Reconciliation
The tracker compares RHU-entered totals with prepared VaccTrack data for the selected reporting period.

Statuses:
- **Matched** – difference is zero
- **Check VaccTrack** – RHU Tracker is higher than VaccTrack
- **Check RHU Tracker** – VaccTrack is higher than RHU Tracker
- **Not Updated** – no RHU Tracker record exists for that metric

System Admin / normal dashboard viewers get an Abra summary, municipality reconciliation, and school-level discrepancy drill-down in the **RHU Accomplishments** tab.

### Login tracker
Continuous session-duration updates were removed from both MR SIA and SBI.

Each successful login now creates one access-log event containing:
- login timestamp
- display name
- role
- username when applicable
- assigned municipality when applicable

Administration now has a **Login History** tab plus recent-login information on the Overview page.

## Database migration
Run once in Supabase SQL Editor:

`supabase/003_sbi_rhu_accomplishments.sql`

The migration safely adds `assigned_muni` to `user_accounts` if it does not already exist, then creates `sbi_rhu_accomplishments`.

If you previously ran `002_sbi_vaccine_requirements.sql`, that is fine. Its old requirement tables can remain in Supabase; v5.13 no longer reads or writes them.

## Files
Replace:
- `app.py`
- `admin/dashboard.py`
- `programs/sbi/dashboard.py`
- `programs/sia/dashboard.py`

Add:
- `programs/sbi/rhu_tracker.py`
- `supabase/003_sbi_rhu_accomplishments.sql`

The old files `programs/sbi/requirements.py` and `core/vaccine_requirements.py` may remain in the repository. They are no longer imported by v5.13 and are inert.

## Recommended first test
1. Run the v5.13 SQL migration.
2. Deploy the files above.
3. Log in using the existing Bangued RHU account.
4. Confirm the main page shows both **MR SIA** and **SBI**.
5. Open SBI and confirm **All Municipalities (Abra)** is available.
6. Open **RHU Accomplishments > Accomplishment Entry** and confirm the encoding area says **Bangued** with no municipality selector.
7. Encode one test school/date and save it.
8. Open **VaccTrack Check** and compare the RHU Tracker value against VaccTrack.
9. Log in as System Admin and confirm the login appears under **Administration > Login History**.

## Validation performed
- Modified Python files compile successfully.
- Reconciliation helper logic was tested with sample G1/G4 data for matched and discrepant totals.
