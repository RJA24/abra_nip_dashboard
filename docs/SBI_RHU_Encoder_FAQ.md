# Abra NIP Monitoring Information System — SBI RHU Encoder FAQs

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
