# v5.22.0 — Production Documentation + Code Freeze

This is the final production-cleanup/documentation batch after v5.21.3.

## Code fixes

- Production version labels standardized to v5.22.0.
- RHU banner correctly identifies **Post-Activity Correction** instead of displaying Pre-Implementation wording.
- System Health now counts only current operational tables as required; historical learner/regional tables are shown separately as legacy/archive tables.
- Feedback privacy wording no longer implies the retired learner workflow is active.
- RHU guide/FAQ updated for automatic uploader clearing and current aggregate-only reporting.

## Documentation

Adds:

- `README.md`
- `CHANGELOG.md`
- `docs/ADMIN_GUIDE.md`
- `docs/DEPLOYMENT_GUIDE.md`
- `docs/DATABASE_SCHEMA.md`
- `docs/QA_ADMIN_CHECKLIST.md`
- `docs/PRODUCTION_PRELAUNCH_CHECKLIST.md`
- `docs/CODE_FREEZE.md`

## Database

No SQL migration is required. Keep the existing production schema through migration 012.

## Release policy

After acceptance testing, treat v5.22.0 as code frozen. Only blocking operational/security fixes should be deployed before/during rollout.
