# Abra NIP Monitoring Information System — SBI RHU Encoder Guide

Version: v5.21.1.2

## 1. The RHU workflow

The SBI RHU workflow is now offline-first:

1. **Download and maintain one SBI workbook**
2. **Use the workbook's VaccTrack sheets, then upload the current workbook**
3. **Refresh and compare with VaccTrack**

VaccTrack remains the official/final national SBI reporting source. The workbook is the RHU working record used to prepare, consolidate, correct, and upload accomplishment data.

## 2. Keep one workbook for the whole activity

Download the RHU-specific workbook from **1. Offline Workbook**. Keep using that same file throughout the SBI activity.

The workbook already contains your municipality's school list and targets. You can encode even when there is no internet connection.

The workbook protects cells that RHUs should not change. **Light-yellow cells are the editable input cells.** Gray/calculated cells, headings, formulas, the Setup sheet, and the hidden Reference sheet are locked to prevent accidental changes.

Do not rename the **Accomplishments** sheet or its column headings.

## 3. Encode accomplishments in Excel

Open the **Accomplishments** sheet and use one row per activity entry. Enter data only in the **light-yellow input cells**. The automatically filled School ID, Barangay, and Row Check cells are locked. The Actual Target is kept in a hidden locked column for internal reference and is not something the RHU needs to encode.

Enter:

- Activity Date
- School Name — select from the dropdown
- Grade Level: G1, G4, or G7
- applicable vaccination counts
- deferred/refused counts when applicable
- reason-code counts when applicable

School ID and Barangay are filled automatically after you select the School Name from the RHU school roster. The Actual Target is retained internally in a hidden locked column and does not need to be viewed or encoded by the RHU.

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

- The VaccTrack G1/G4/G7 sheets are intentionally simplified: only the selected **Report Date**, **School**, grade-specific VaccTrack fields, and reason codes 01–19 are shown. Location/facility metadata is omitted because it is already handled in VaccTrack.

**VaccTrack G1**
- **VaccTrack G4**
- **VaccTrack G7**

Change the **Report Date** in the light-yellow cell at the top of the sheet. It is the only editable cell on each VaccTrack sheet. The calculated school rows and VaccTrack values are locked so they cannot be accidentally overwritten. The workbook automatically sums the accomplishment rows for that date and arranges the values using the VaccTrack field names.

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
- Enter data only in light-yellow input cells; calculated/reference cells are intentionally locked.
- Encode all accomplishments in the workbook, including days when internet is unavailable.
- Use the workbook's VaccTrack sheets for daily VaccTrack encoding.
- Upload the complete current workbook, not only the rows you changed.
- Correct mistakes in Excel and re-upload the workbook.
- Do not upload a VaccTrack export into the RHU workbook uploader.
- Do not upload data belonging to another municipality.
- VaccTrack remains the official/final national reporting source.

## 11. Send Feedback / Report a Problem

Use **Send Feedback / Report a Problem** for upload issues, confusing instructions, mobile display issues, or suggestions. Do not include learner names or other unnecessary identifying information.


## 11. Campaign status and final submission

The System Administrator controls the SBI campaign status: **Pre-Implementation**, **Live**, or **Closed**. Pre-Implementation is for testing and preparation, Live is the implementation period, and Closed blocks new RHU workbook uploads while keeping existing data and VaccTrack checks available.

When your RHU is completely finished, use **Mark Current Workbook as Final**. After finalization, the workbook cannot be replaced unless the System Administrator reopens the RHU submission.

Always use the workbook downloaded from the current system. The uploader checks the workbook version and municipality before accepting it.
