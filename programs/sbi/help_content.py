"""In-app RHU help content for the SBI offline workbook workflow."""

RHU_FULL_GUIDE_MD = """# Abra NIP Monitoring Information System — SBI RHU Encoder Guide

Version: v5.22.2.2

## 1. Reporting workflow

The SBI RHU reporting workflow is offline-first:

1. Download your municipality-specific SBI workbook.
2. Encode accomplishments in the same workbook throughout the activity.
3. Use its VaccTrack G1/G4/G7 sheets as your encoding aid.
4. Upload the complete current workbook when internet is available.
5. Review the comparison before confirming.
6. Refresh VaccTrack Check after the latest official extract is available.

VaccTrack remains the official/final national SBI reporting source. The Abra NIP Monitoring Information System is used for local operational monitoring, validation, and reconciliation.

## 2. Use one working workbook

Download the workbook from **1. Offline Workbook**. Keep that file as your RHU's working copy for the activity.

Do not rename the **Accomplishments** sheet or its column headings. If the system says your workbook version is unsupported, download a fresh workbook from the system and transfer your current accomplishment rows to it.

## 3. Accomplishments sheet

Encode only in the **light-yellow input cells**.

For every activity entry, provide:

- Activity Date
- School Name — choose from the dropdown
- Grade Level — G1, G4, or G7
- applicable vaccination counts
- deferred/refused counts when applicable
- Reason 01–19 counts when applicable

After selecting School Name, **School ID and Barangay fill automatically**. You do not need to memorize or type the School ID.

Formula/reference cells are locked. The Actual Target is retained internally and is not an RHU input field.

If the same school and grade have more than one session on the same date, you may use more than one row. The workbook/dashboard consolidate those rows for reporting.

## 4. Grade-specific fields

### Grade 1 and Grade 7

Use the MR/Td fields:

- MR Male
- MR Female
- Td Male
- Td Female
- MR Deferred / Refused
- Td Deferred / Refused

Leave HPV fields blank.

### Grade 4

Use the HPV fields:

- HPV Dose 1
- HPV Dose 2
- HPV1 Deferred / Refused
- HPV2 Deferred / Refused

Leave MR/Td fields blank.

### Reason codes

Use Reason 01–19 when applicable. Reason counts must remain consistent with the deferred/refused totals for the row.

## 5. VaccTrack sheets

The workbook contains:

- **VaccTrack G1**
- **VaccTrack G4**
- **VaccTrack G7**

Set the **Report Date** in the editable light-yellow cell. The workbook automatically summarizes that date by school and shows the grade-specific VaccTrack values and Reason 01–19 counts.

The calculated rows are locked. Region, Province, Municipality, Barangay, Facility Name, and School ID are not shown because the sheets are only an encoding aid for the values you enter into VaccTrack.

For Grade 4, the RHU-facing workbook does not show G4.A Actual Total Female Students; the target remains internal to the workbook/system.

## 6. Upload Current Workbook

When internet is available:

1. Open **2. Upload Current Workbook**.
2. Select your complete working workbook.
3. Review validation results.
4. Review **Added / Modified / Removed / Unchanged**.
5. Confirm only if the comparison is correct.
6. Click **Use This Workbook as Current RHU Data**.

After a successful save, the file uploader clears automatically. This prevents the previously submitted file from looking like a new pending upload. Select a workbook again only when you intentionally want to submit another update.

The confirmed workbook becomes your RHU's complete current operational dataset in the Abra system.

## 7. Corrections and follow-up activities

Do not create a separate correction record in the web system.

If something changes:

1. Open the same working workbook.
2. Add, correct, or remove the appropriate row.
3. Save the workbook.
4. Upload the complete current workbook again.
5. Review the comparison carefully.
6. Confirm the update.

A row that existed in the dashboard but is missing from the newly uploaded complete workbook appears as **Removed**. Do not confirm unexpected removals.

## 8. Understanding the comparison

- **Added** — new Date + School + Grade record.
- **Modified** — saved record exists, but one or more counts changed.
- **Removed** — saved record is no longer present in the complete workbook.
- **Unchanged** — workbook record already matches the saved data.

## 9. VaccTrack Check

Open **3. VaccTrack Check** after the NIP coordinator has uploaded/refreshed the latest official VaccTrack extract.

The system compares the RHU workbook with VaccTrack by date, school, grade, and vaccine metric.

If RHU activity is newer than the latest VaccTrack **Data Through** date, the system may show **Pending VaccTrack Verification**. This means the official extract has not reached that activity date yet; it is not automatically an RHU error.

## 10. Campaign status

The System Administrator controls the campaign status:

- **Pre-Implementation** — testing/preparation.
- **Live** — field implementation and routine uploads.
- **Post-Activity Correction** — field activities have ended, but corrections/late uploads are still allowed for Activity Dates inside the official campaign period.
- **Closed** — new workbook uploads are blocked.

Example: if Oct 31 is the last official activity date, an RHU may still correct an Oct 28 record on Nov 1 while the campaign is in Post-Activity Correction. A new Nov 1 Activity Date would not be accepted if it is outside the official activity period.

## 11. Final RHU report

Final submission is **not an after-upload step**.

During Live/Post-Activity Correction, the final-report section is intentionally separate. Continue updating and re-uploading the workbook as needed. Submit the final RHU report only when routine reporting and corrections are complete.

After finalization, further uploads are locked until the System Administrator reopens the RHU submission.

## 12. Important reminders

- Keep one working workbook for the activity.
- Upload the complete current workbook, not only changed rows.
- Select School Name from the dropdown; School ID is automatic.
- Do not enter data for another municipality.
- Do not upload a VaccTrack export into the RHU workbook uploader.
- Do not put learner names, LRN, or other individual identifiers in the aggregate workbook.
- VaccTrack remains the official/final national SBI dataset.

## 13. Feedback

Use **Send Feedback / Report a Problem** for workbook, upload, reconciliation, instruction, mobile-display, or usability concerns. Do not include names or other identifying personal/health information in the feedback message.
"""

RHU_FAQ_MD = """# Abra NIP Monitoring Information System — SBI RHU Encoder FAQs

Version: v5.22.2.2

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
"""
