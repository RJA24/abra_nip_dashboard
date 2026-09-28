# Abra NIP Monitoring Information System v5.20.2.2 — System Rename

This is a small incremental branding patch for the deployed v5.20.2.1 build.

## New system name

**Abra NIP Monitoring Information System**

The login screen, main menu, browser title, SBI guide/FAQ, generated SBI line-list title, feedback wording, and backup metadata now use the new system name.

The main program buttons and program names remain unchanged:
- MR SIA
- SBI
- Administration

## Deploy only these files

- `app.py`
- `admin/operations.py`
- `programs/sbi/support.py`
- `programs/sbi/linelist.py`
- `programs/sbi/help_content.py`
- `docs/SBI_RHU_Encoder_Full_Guide.md`
- `docs/SBI_RHU_Encoder_FAQ.md`

No SQL migration is required.
