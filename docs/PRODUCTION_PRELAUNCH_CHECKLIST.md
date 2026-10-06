# SBI Production Pre-Launch Checklist — v5.22.0

Complete this before changing Campaign Status to **Live**.

- [ ] Official SBI activity start/end dates confirmed with Region.
- [ ] Date-range enforcement enabled.
- [ ] All 27 RHU accounts exist and have the correct municipality assignment.
- [ ] RHUs that have not changed their temporary password have been followed up.
- [ ] Final `Target by School` roster synchronized to `sbi_targets`.
- [ ] Known school-location corrections verified (including municipality/barangay placement).
- [ ] All 27 municipality workbooks generate successfully.
- [ ] Fresh all-27-RHU workbook ZIP generated after the last target-roster correction.
- [ ] RHU test workbook data cleared or explicitly reviewed as legitimate production data.
- [ ] Data Quality has no unresolved Critical findings.
- [ ] VaccTrack G1/G4/G7 source status reviewed.
- [ ] Final production backup prepared and downloaded.
- [ ] System Admin smoke test passed.
- [ ] QA Admin read-only test passed.
- [ ] RHU Encoder end-to-end test passed, including uploader clearing after success.
- [ ] RHU GC/DMO GC informed of the final reporting workflow and requested to test.
- [ ] Production Readiness run after all final changes.
- [ ] Campaign switched to Live only after the launch guard is satisfied.

After launch, do not add convenience features. Apply only blocking operational/security fixes until the implementation period is stable.
