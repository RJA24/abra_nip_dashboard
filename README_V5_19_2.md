# Abra NIP Dashboard v5.19.2 — Encoder Guidance & Safer Corrections

This is a small usability/documentation patch on top of v5.19.1. It does not change the System Learner ID database schema.

## What changed

### 1. Simpler RHU authorization reminder

The technical privacy/RLS warning shown to RHU encoders is replaced with:

> **For authorized RHU/NIP users only. Please upload only the correct learner records for your assigned municipality.**

Technical deployment/security notes can remain in developer/admin documentation instead of the normal encoder workflow.

### 2. Expanded in-app step-by-step guide

The guide now clearly distinguishes:

- New Activity
- Follow-up Activity
- Revision / Correction
- multiple Activity Dates
- MR yesterday / Td today
- exact duplicates
- Added / Modified / Removed / Unchanged
- wrong Activity Date corrections
- wrong School / Grade corrections
- when NIP/System Admin cleanup is required
- Matched vs Pending VaccTrack Verification

### 3. Important correction clarification

**Corrections / History is for reviewing saved records and import history.** The revised workbook is uploaded again through **1. Upload Learner Records**.

For ordinary field corrections on the same Activity Date, upload the complete **Activity Date + School + Grade** group.

If the Activity Date itself was wrong, do not simply add the correct date and leave the old record. When the wrong-date record cannot safely be removed through a complete-group revision, the NIP/System Admin should remove the incorrect batch in **Administration → Import Management** before the corrected activity is uploaded.

If School or Grade was wrong, do not force the same System Learner ID into the new school/grade; the conflict protection may block it. Clean up the incorrect import first.

## Files to replace

```text
programs/sbi/linelist.py
programs/sbi/rhu_tracker.py
programs/sbi/help_content.py
programs/sbi/assets/SBI_Linelist_Template.xlsx
docs/SBI_RHU_Encoder_Full_Guide.md
docs/SBI_RHU_Encoder_FAQ.md
```

No SQL migration is required when upgrading from v5.19.1.

## Suggested quick test

1. Sign in as an RHU Encoder.
2. Open SBI → RHU Accomplishments.
3. Confirm the short authorization reminder appears in Step 1 and the technical RLS wording is not shown to the encoder.
4. Expand **Need help? Step-by-step guide, corrections & FAQs**.
5. Verify the guide explains New Activity, Follow-up Activity, Revision, MR yesterday/Td today, wrong-date corrections, and unexpected removals.
6. Download a fresh template and verify the Instructions sheet shows v5.19.2 and the updated correction guidance.
7. Test a normal follow-up row on a new date and confirm it remains an Added activity record rather than a revision.
8. Test a same-date field correction using the complete Activity Date + School + Grade group and confirm it appears as Modified.
