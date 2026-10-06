# Abra NIP Monitoring Information System — SBI RHU Encoder FAQs

Version: v5.22.0

## 1. Do I need internet while encoding accomplishments?

No. Encode in the workbook offline. Internet is needed when you upload the current workbook or refresh/check online data.

## 2. What data goes into the workbook?

Aggregate counts by **Activity Date + School + Grade**. Do not place learner names, LRN, or other individual identifiers in the workbook.

## 3. How many workbooks should our RHU use?

One working workbook for the activity. Keep adding/correcting rows in that file.

## 4. Do I need to know the School ID?

No. Select **School Name** from the dropdown. School ID and Barangay fill automatically.

## 5. Can one workbook contain many dates and schools?

Yes. That is the intended workflow.

## 6. What if one school/grade has two sessions on the same date?

You may use more than one row. The workbook/dashboard consolidate the rows for reporting.

## 7. Which cells can I edit?

Use the **light-yellow input cells**. Formula/reference cells are protected. On VaccTrack G1/G4/G7, the Report Date is the RHU input used to change the displayed daily summary.

## 8. Why is Actual Target not visible in Accomplishments?

It is kept internally for validation/reference and is not something the RHU needs to encode.

## 9. Why is G4.A not shown in the VaccTrack G4 sheet?

The workbook keeps target information internally. The RHU-facing VaccTrack G4 sheet focuses on the accomplishment/deferral/refusal values needed for encoding.

## 10. How do I correct a wrong accomplishment?

Edit the same workbook, save it, and upload the complete current workbook again.

## 11. Should I upload only the corrected row?

No. Always upload the complete working workbook. The system treats it as your RHU's current complete dataset.

## 12. What do Added, Modified, Removed, and Unchanged mean?

**Added** is new, **Modified** changed, **Removed** no longer appears in the complete workbook, and **Unchanged** already matches the saved data.

## 13. What if I see unexpected Removed records?

Do not confirm. Make sure you did not select an older/incomplete copy of the workbook.

## 14. Why did the selected upload file disappear after I successfully saved it?

That is intentional. The uploader clears after success so the same file is not mistaken for a new pending upload. Select the workbook again only when you intentionally have another update to submit.

## 15. What if our internet is unavailable for several days?

Continue using the same workbook. Upload its latest complete version when internet becomes available.

## 16. What is Pending VaccTrack Verification?

The latest official VaccTrack extract has not yet reached that RHU activity date. Wait for a newer extract before treating it as a discrepancy.

## 17. Can I upload a VaccTrack export into Upload Current Workbook?

No. That uploader accepts the municipality-specific SBI Accomplishment Workbook. Official VaccTrack extracts are handled separately by the System Admin.

## 18. Which dataset is official?

VaccTrack is the official/final national SBI dataset. The Abra workbook/system supports offline work, provincial monitoring, validation, and reconciliation.

## 19. Can we still correct data after the last activity day?

Yes, while the campaign is in **Post-Activity Correction**. The Activity Date inside the workbook must still fall within the official activity date range.

## 20. What does Finalized mean?

It means the RHU has declared its current report complete/final. Finalization is not required after routine uploads. Once finalized, new uploads require the System Administrator to reopen the RHU submission.

## 21. What happens when the campaign is Closed?

Existing reports/checks remain visible, but new RHU workbook uploads are blocked.

## 22. What if the workbook version is unsupported?

Download a fresh workbook from the system and transfer your current Accomplishments rows into the new workbook.
