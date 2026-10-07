# v5.22.2 Admin Test Checklist

## Workbook Dashboard

- [ ] Login as System Admin.
- [ ] Open SBI → RHU Accomplishments.
- [ ] Confirm two sub-tabs are visible: **Workbook Dashboard** and **VaccTrack Reconciliation**.
- [ ] Workbook Dashboard loads without an exception.
- [ ] RHUs Reporting matches the municipalities with uploaded workbook data.
- [ ] G1 MR / G1 Td / G4 HPV1 / G4 HPV2 / G7 MR / G7 Td totals agree with known workbook test data.
- [ ] Main sidebar municipality filter limits workbook data correctly.
- [ ] Main reporting-period filter limits workbook data correctly.
- [ ] Municipality Summary shows all 27 RHUs when viewing Abra-wide data.
- [ ] School-level workbook totals appear for RHUs with data.
- [ ] CSV downloads work.

## Reconciliation

- [ ] Open VaccTrack Reconciliation.
- [ ] RHU Workbook and VaccTrack totals appear side by side.
- [ ] A known matching test produces **Matched**.
- [ ] A deliberate verified difference produces **Discrepancy**.
- [ ] RHU activity newer than the current VaccTrack extract shows **Pending VaccTrack Verification**.
- [ ] Daily discrepancy tally loads.
- [ ] School-level reconciliation loads.

## Roles / Safety

- [ ] QA Admin can view both tabs but cannot modify production data.
- [ ] RHU Encoder workflow is unchanged.
- [ ] RHU QA Encoder workflow is unchanged and remains dry-run only.
- [ ] Workbook upload/finalization logic is unchanged.
- [ ] System Health shows **v5.22.2**.
