# Abra NIP Dashboard v5.12 - SBI Vaccine Requirements

This is a cumulative patch. It includes the v5.11 shared municipality label editor files plus the new v5.12 RHU vaccine-requirement workflow.

## What v5.12 adds

### RHU Encoder accounts

Administration now has a dedicated **RHU Accounts** tab.

A System Admin can:

- create an RHU Encoder account;
- assign it to exactly one Abra municipality;
- change the municipality assignment;
- reset its password;
- enable or disable the account; and
- delete the account.

When an RHU Encoder signs in, the app automatically opens SBI and locks all SBI views to that account's assigned municipality. The normal program selector and province-wide geographic selector are not available to that role.

### Direct prior-vaccination encoding

SBI now contains a **Vaccine Requirements** tab.

For each school the RHU encodes learners vaccinated **before the upcoming SBI activity**:

- Grade 1 MR prior vaccinated
- Grade 1 Td prior vaccinated
- Grade 4 HPV Dose 1 prior vaccinated
- Grade 4 HPV Dose 2 prior vaccinated
- Grade 7 MR prior vaccinated
- Grade 7 Td prior vaccinated

Blank means **not yet verified / not yet encoded**. Blank is never converted to zero.

The system validates that prior counts are whole numbers, non-negative, and do not exceed the current Actual Target. HPV Dose 2 prior also cannot exceed HPV Dose 1 prior.

### Remaining requirement calculations

The Actual Target remains unchanged and continues to be the coverage denominator.

Operational remaining need is calculated separately:

- G1 MR Remaining = G1 Actual Target - G1 MR Prior
- G1 Td Remaining = G1 Actual Target - G1 Td Prior
- G7 MR Remaining = G7 Actual Target - G7 MR Prior
- G7 Td Remaining = G7 Actual Target - G7 Td Prior
- HPV Dose 1 Remaining = G4 Female Actual Target - HPV Dose 1 Prior
- HPV Dose 2 Pending = HPV Dose 1 Prior - HPV Dose 2 Prior

MR and Td province/municipality requirements combine Grade 1 + Grade 7.

The code also calculates full HPV-series doses outstanding internally for future supply-planning use, but the immediate dashboard separates Dose 1 remaining from Dose 2 pending so the two are not incorrectly combined.

### Draft and Submitted workflow

Each RHU municipality has one of four effective states:

- **Not Started**
- **Draft**
- **Submitted**
- **Needs Review**

An RHU may save incomplete work as Draft. Submission is blocked until:

- Actual Targets are complete for every school in the municipality; and
- every prior-vaccination field required by a non-zero target has been encoded and passes validation.

Submitted data are locked. A System Admin can reopen the municipality for corrections.

### Automatic Actual Target change detection

Each submission stores a signature of the municipality's current Actual Targets.

If the Actual Targets worksheet changes after submission, the municipality automatically becomes **Needs Review** and is excluded from official province totals until the RHU reviews and resubmits it. This prevents a previously submitted requirement from silently becoming inconsistent with a revised target.

### Coordinator view

For province-wide viewing, the Vaccine Requirements tab shows:

- RHUs submitted / 27
- MR remaining doses
- Td remaining doses
- HPV Dose 1 remaining
- HPV Dose 2 pending
- municipality submission/completion table
- downloadable municipality CSV
- school-level CSV export
- municipality choropleth for selectable vaccine requirement metric
- supply-planning allowance inputs

Official province totals and maps include only current **Submitted** municipalities. Draft, Not Started, and Needs Review municipalities are not treated as zero.

The choropleths use the shared v5.11 municipality label positions, so changes made in Administration > Map Labels also affect these maps.

## Supabase setup

If v5.11 has not yet been applied, run first:

```text
supabase/001_map_label_positions.sql
```

Then run once:

```text
supabase/002_sbi_vaccine_requirements.sql
```

The v5.12 migration:

1. adds `assigned_muni` to `user_accounts`;
2. creates `sbi_prior_vaccination`; and
3. creates `sbi_requirement_submissions`.

Both requirement tables preserve missing prior counts as SQL `NULL`.

## Files to add or replace

Replace:

```text
app.py
admin/dashboard.py
programs/sbi/dashboard.py
```

Add:

```text
core/vaccine_requirements.py
programs/sbi/requirements.py
```

The cumulative patch also contains these v5.11 files. Replace/add them if v5.11 is not already deployed:

```text
core/map_labels.py
programs/sbi/reporting.py
programs/sia/dashboard.py
supabase/001_map_label_positions.sql
```

No new Python dependency is required beyond the packages already used by the current dashboard.

## Recommended first test

1. Run the SQL migrations.
2. Deploy the patch.
3. Sign in as System Admin.
4. Open Administration > RHU Accounts.
5. Create one test RHU Encoder, preferably for one municipality only.
6. Sign out and sign in with the test RHU account.
7. Confirm SBI opens directly and the municipality is locked.
8. Open Vaccine Requirements.
9. Enter a few prior-vaccination counts and Save Draft.
10. Confirm Remaining values update correctly.
11. Complete all required fields and Submit.
12. Sign back in as System Admin and confirm the municipality appears as Submitted in the province view.

## Important interpretation

The new requirement figures answer a different operational question from coverage:

```text
Actual Target
    -> coverage denominator

Prior Vaccinated Before SBI
    -> already vaccinated before the upcoming activity

Remaining Eligible
    -> operational workload / raw dose requirement

Planning Allowance
    -> optional coordinator-selected buffer applied to raw remaining doses
```

The dashboard intentionally does not hard-code an official wastage or buffer percentage. The coordinator enters the approved planning allowance when preparing supply estimates.
