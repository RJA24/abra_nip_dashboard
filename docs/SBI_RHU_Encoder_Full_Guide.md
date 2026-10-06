# Abra NIP Monitoring Information System — SBI RHU Encoder Guide

Version: v5.22.0

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
