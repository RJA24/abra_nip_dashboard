# Abra NIP Dashboard v5.20.1 — Manual VaccTrack Google Sheet Fallback

This patch adds a System Admin switch for the legacy VaccTrack Google Sheet fallback.

## Behavior

- **Fallback ON**: direct VaccTrack uploads remain first priority. Google Sheets is contacted only for a grade that has no completed direct upload.
- **Fallback OFF**: Google Sheets is never queried for VaccTrack. Only completed direct uploads are used.
- The setting is global for SBI and persists in Supabase.
- Existing deployments default to **ON** after the migration so current behavior is preserved until the System Admin changes it.

If fallback is off and a grade has no direct upload, that grade is shown as **Unavailable - fallback disabled** instead of silently loading the Google Sheet.

## Admin control

Open:

`Administration → Operations → System Health → VaccTrack Data Sources`

Use:

`Enable Google Sheet fallback when a direct VaccTrack upload is missing`

Changing the switch clears the Streamlit data cache and reloads the current source status.

## Performance note

v5.20 already avoids Google Sheet reads when completed direct uploads exist for G1, G4 and G7. This switch gives the System Admin explicit control so the Google Sheet source can be disabled entirely once direct-file imports are the intended source.

## Database update

Run once:

`supabase/010_sbi_vacctrack_fallback_setting.sql`

It creates `sbi_settings` and inserts the fallback setting as `true` only if the setting does not already exist.

## Replace

- `core/data.py`
- `admin/operations.py`
- `programs/sbi/rhu_tracker.py`
- `programs/sbi/vacctrack_import.py`
- `programs/sbi/support.py`
- `programs/sbi/help_content.py`
- `docs/SBI_RHU_Encoder_Full_Guide.md`
- `docs/SBI_RHU_Encoder_FAQ.md`

## Add

- `supabase/010_sbi_vacctrack_fallback_setting.sql`

No RHU line-list workflow or database isolation behavior is changed by this patch.
