# Abra NIP Dashboard v5.20.1.1 — Cumulative Hotfix

This is a cumulative deployment package for v5.20 + v5.20.1.

## Why this hotfix exists

The v5.20.1 fallback-toggle package was an incremental patch. Its `programs/sbi/rhu_tracker.py` imports `render_training_mode` from `programs/sbi/linelist.py`, which was introduced in v5.20.

If v5.20.1 was copied directly over v5.19.3.1 without first deploying the full v5.20 package, Streamlit fails during startup with an ImportError while importing `programs.sbi.linelist`.

This package includes the complete matching v5.20 line-list and Operations files together with the v5.20.1 fallback-toggle changes so the module versions stay synchronized.

## Deploy

If you already ran migration 009, do not run it again unless needed; it is included for reference.

Run once if not yet applied:

- `supabase/009_sbi_operations.sql`
- `supabase/010_sbi_vacctrack_fallback_setting.sql`

Migration 010 adds the global VaccTrack Google Sheet fallback setting and defaults it to enabled.

Replace/add the files in this package using the same paths in the repository.

Important matching files include:

- `app.py`
- `admin/dashboard.py`
- `admin/operations.py`
- `core/data.py`
- `programs/sbi/linelist.py`
- `programs/sbi/rhu_tracker.py`
- `programs/sbi/support.py`
- `programs/sbi/import_management.py`
- `programs/sbi/vacctrack_import.py`
- `programs/sbi/help_content.py`
- `auth_utils.py`

The existing v5.19.3 password migration and v5.19 System Learner ID migration remain included for cumulative reference.

## VaccTrack fallback behavior

- ON: direct uploads are preferred; Google Sheets are queried only for grades with no completed direct upload.
- OFF: Google Sheets are not queried for VaccTrack. Missing direct grades show as unavailable.

Control:

`Administration → Operations → System Health → VaccTrack Data Sources`

## Validation

All Python files included in this package were compiled with Python `py_compile` successfully.
