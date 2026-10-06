# Production Code Freeze — v5.22.0

v5.22.0 is the production code-freeze baseline for the 2026 SBI rollout.

## Allowed before/during rollout

- Fixes for crashes, failed uploads, data corruption risk, incorrect validation, incorrect permissions, incorrect reconciliation, or other blocking operational defects.
- Required changes caused by an official reporting rule/schedule change that cannot be handled through existing Admin controls.
- Security/privacy fixes.

## Avoid until after rollout stabilization

- New dashboards/charts that are not operationally required.
- New reporting flows.
- New roles or permission models.
- Cosmetic redesigns.
- Convenience features that change tested workflows.
- Database cleanup/removal of legacy tables unless necessary for safety.

For official activity-date extensions, use SBI Control rather than changing code.
