# Abra NIP Monitoring Information System v5.21.1.3

## Workbook save hotfix + v5.21.1.2 usability fixes

This patch supersedes v5.21.1.2 for deployments currently on v5.21.1.1.

### Fixed
- Fixes workbook save error `22P02 invalid input syntax for type bigint` when confirming an RHU workbook.
- The SHA-256 workbook hash is now stored only in `sbi_rhu_workbook_submissions.batch_id` (text), where it belongs.
- The legacy `sbi_rhu_accomplishments.source_batch_id` field remains null for offline aggregate workbook records because that column is a bigint used by the retired learner-line-list workflow.

### Also includes the v5.21.1.2 usability improvements
- School Name is the RHU dropdown; School ID fills automatically and is protected.
- Campaign Control summary cards use responsive sizing to prevent text overlap.
- Updated RHU help/guide wording for the School Name dropdown workflow.

### Deploy
Replace only:
- `programs/sbi/aggregate_workbook.py`
- `admin/operations.py`
- `programs/sbi/help_content.py`
- `programs/sbi/support.py`
- `docs/SBI_RHU_Encoder_Full_Guide.md`
- `docs/SBI_RHU_Encoder_FAQ.md`

No SQL migration is required for this hotfix. Keep the already-applied `011` and `012` migrations.
