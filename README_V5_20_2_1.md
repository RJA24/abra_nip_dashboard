# Abra NIP Dashboard v5.20.2.1 — Template / System Learner ID Warning Hotfix

This is a small incremental hotfix for an existing v5.20.2 deployment.

## What changed

The RHU interface and help content now explicitly warn that a downloaded blank fresh template must not be shared with another RHU or copied/reused for another independent activity roster.

The guidance now distinguishes the three cases clearly:

- **New first-time roster:** download a fresh template from the dashboard.
- **Follow-up vaccination:** use **Create Follow-up Line List** so the same learners keep their existing System Learner IDs.
- **Correction:** use the original workbook so the existing System Learner IDs remain attached to the correct learners.

The warning appears:

1. directly below **1A. Download Fresh SBI Line List Template (Excel)**;
2. in the in-app Full Guide; and
3. in the FAQ.

## Deploy only these files

```text
programs/sbi/linelist.py
programs/sbi/help_content.py
docs/SBI_RHU_Encoder_Full_Guide.md
docs/SBI_RHU_Encoder_FAQ.md
```

No SQL migration is required. No other v5.20.2 files need to be replaced.
