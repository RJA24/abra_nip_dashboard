"""In-app RHU help content for the SBI offline workbook workflow."""

RHU_FULL_GUIDE_MD = """# Abra NIP Monitoring Information System — SBI RHU Encoder Guide

Version: v5.21

## 1. The RHU workflow

The SBI RHU workflow is now offline-first:

1. **Download and maintain one SBI workbook**
2. **Use the workbook's VaccTrack sheets, then upload the current workbook**
3. **Refresh and compare with VaccTrack**

VaccTrack remains the official/final national SBI reporting source. The workbook is the RHU working record used to prepare, consolidate, correct, and upload accomplishment data.

## 2. Keep one workbook for the whole activity

Download the RHU-specific workbook from **1. Offline Workbook**. Keep using that same file throughout the SBI activity.

The workbook already contains your municipality's school list and targets. You can encode even when there is no internet connection.

Do not rename the **Accomplishments** sheet or its column headings.

## 3. Encode accomplishments in Excel

Open the **Accomplishments** sheet and use one row per activity entry.

Enter:

- Activity Date
- School ID
- Grade Level: G1, G4, or G7
- applicable vaccination counts
- deferred/refused counts when applicable
- reason-code counts when applicable

School Name, Barangay, and Actual Target are filled automatically from the RHU school roster.

If the same school and grade have more than one entry on the same date, you may use more than one row. The workbook and dashboard add those rows together automatically.

## 4. Grade rules

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

The workbook includes Reason 01 to Reason 19. Enter the aggregate count for each applicable reason. Reason counts should not exceed the total deferred/refused counts for that row.

## 5. Encode in VaccTrack

The workbook contains three automatic sheets:

- **VaccTrack G1**
- **VaccTrack G4**
- **VaccTrack G7**

Change the **Report Date** at the top of the sheet. The workbook automatically sums the accomplishment rows for that date and arranges the values using the VaccTrack field names.

Rows marked **YES** had an accomplishment entry for the selected date.

Copy the displayed values into VaccTrack.

## 6. Upload the workbook to the monitoring system

When internet is available:

1. Open **2. Upload Current Workbook**.
2. Upload the same working workbook.
3. Review the validation results.
4. Review Added / Modified / Removed / Unchanged.
5. Confirm only when the comparison is correct.

The uploaded workbook is treated as your RHU's **complete current dataset**.

## 7. Corrections are done in the workbook

If an accomplishment was wrong:

1. Open your working workbook.
2. Correct the row in **Accomplishments**.
3. Save the file.
4. Upload the complete workbook again.
5. Review the changes and confirm.

You do not create a separate correction entry in the web system.

If a row is removed from the workbook, the corresponding current dashboard record will be shown as **Removed** during upload and will be removed after confirmation.

## 8. Understanding the upload comparison

- **Added** — a new Date + School + Grade record will be added.
- **Modified** — an existing record has different counts.
- **Removed** — an existing dashboard record is no longer present in the complete workbook.
- **Unchanged** — the workbook matches the current dashboard record.

Do not confirm unexpected removals.

## 9. VaccTrack Check

After the NIP coordinator uploads the latest VaccTrack extract, open **3. VaccTrack Check** and click **Refresh VaccTrack Data**.

The system compares the latest RHU workbook totals with VaccTrack by date, school, grade, and vaccine metric.

If the VaccTrack extract has not yet reached your activity date, the result may remain pending. Check the **Data Through** date before treating a difference as an error.

## 10. Important reminders

- Keep one working workbook throughout the SBI activity.
- Encode all accomplishments in the workbook, including days when internet is unavailable.
- Use the workbook's VaccTrack sheets for daily VaccTrack encoding.
- Upload the complete current workbook, not only the rows you changed.
- Correct mistakes in Excel and re-upload the workbook.
- Do not upload a VaccTrack export into the RHU workbook uploader.
- Do not upload data belonging to another municipality.
- VaccTrack remains the official/final national reporting source.

## 11. Send Feedback / Report a Problem

Use **Send Feedback / Report a Problem** for upload issues, confusing instructions, mobile display issues, or suggestions. Do not include learner names or other unnecessary identifying information.
"""

RHU_FAQ_MD = """# Abra NIP Monitoring Information System — SBI RHU Encoder FAQs

Version: v5.21

## 1. Do I need internet while encoding SBI accomplishments?

No. The SBI workbook is designed to be used offline. Internet is needed only when you want to upload the current workbook or check the latest VaccTrack comparison.

## 2. Do we still use the learner line list or System Learner ID?

No. The SBI RHU workflow now uses aggregate accomplishment data by Activity Date + School + Grade.

## 3. How many workbooks should our RHU use?

Use one RHU working workbook for the whole SBI activity. Keep adding new activity rows to the **Accomplishments** sheet.

## 4. Can one workbook contain many activity dates and schools?

Yes. That is the intended workflow.

## 5. What if the same school and grade have two sessions on the same date?

You may encode two rows. The workbook and dashboard automatically add rows with the same Activity Date + School + Grade when preparing the current totals.

## 6. How do I know what to encode in VaccTrack?

Open **VaccTrack G1**, **VaccTrack G4**, or **VaccTrack G7**, then set the Report Date. The sheet automatically displays that day's school-level values using VaccTrack field names.

## 7. How do I correct a wrong accomplishment?

Edit the row in the **Accomplishments** sheet, save the workbook, and upload the complete current workbook again.

## 8. Should I upload only the corrected row?

No. Upload the complete working workbook. The dashboard treats it as your RHU's current complete dataset.

## 9. What do Added, Modified, Removed, and Unchanged mean?

**Added** is a new Date + School + Grade record. **Modified** has changed counts. **Removed** existed in the dashboard but is no longer in the workbook. **Unchanged** already matches.

## 10. What if I see unexpected Removed records?

Do not confirm. Check whether you accidentally uploaded an incomplete or older copy of the workbook.

## 11. What if I accidentally use MR/Td fields for Grade 4?

The uploader will reject the row. Grade 4 must use HPV fields.

## 12. What if I accidentally use HPV fields for Grade 1 or Grade 7?

The uploader will reject the row. Grade 1 and Grade 7 must use MR/Td fields.

## 13. Are reason counts required?

Use the reason-code counts when applicable to your VaccTrack reporting. The workbook checks that reason totals do not exceed the total deferred/refused counts entered for the row.

## 14. What if our internet is unavailable for several days?

Continue encoding in the same workbook. When internet becomes available, upload the latest complete workbook. The dashboard will receive all of the activity dates contained in it.

## 15. What does Pending VaccTrack Verification mean?

The latest official VaccTrack extract available to the monitoring system has not yet reached your activity date. It does not automatically mean the RHU encoded something incorrectly.

## 16. Can I upload a VaccTrack export into Step 2?

No. Step 2 accepts the RHU SBI Offline Accomplishment Workbook. Official VaccTrack extracts are uploaded separately by the System Admin.

## 17. Which dataset is official?

VaccTrack remains the official/final national SBI dataset. The RHU workbook and Abra NIP Monitoring Information System are used for offline working records, provincial monitoring, and reconciliation.
"""
