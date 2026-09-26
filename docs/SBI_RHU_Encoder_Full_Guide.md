# Abra NIP Dashboard — SBI RHU Encoder Step-by-Step Guide

Version: v5.20.2

## 1. The 3-step RHU workflow

The normal SBI workflow has only three main steps:

1. **Upload Learner Records**
2. **VaccTrack Encoding**
3. **Refresh & VaccTrack Check**

VaccTrack remains the official/final SBI dataset. The dashboard line list is the RHU's operational record used to prepare and verify what should be encoded in VaccTrack.

> **For authorized RHU/NIP users only. Please upload only the correct learner records for your assigned municipality.**

### First login

If you signed in using a temporary/default password, the dashboard will require you to create your own password before you can continue. Use at least 8 characters and do not reuse the temporary password.

If you forget your password later, ask the NIP/System Admin to reset it. The reset password is temporary and you will be required to change it again on your next login.

## 2. First question: what are you trying to do?

| Situation | Use this workflow |
|---|---|
| First vaccination activity for these learners | **New Activity** |
| Same learners return on another date | **Follow-up Activity** |
| MR was given yesterday and Td is given today | **Follow-up Activity** |
| Correct a vaccine status, lot/batch, reason, section, or remarks for an already-saved date | **Revision / Correction** |
| Activity Date itself was saved incorrectly | **Correction requiring removal of the wrong-date record** — see Section 8 |
| School or Grade was saved incorrectly | **Correction requiring NIP/Admin assistance** — see Section 8 |
| The exact same file was already imported correctly | **Do not import again** |

### The easiest rule to remember

- **New vaccination date = New / Follow-up Activity.**
- **Fixing something on the same saved activity date = Revision / Correction.**

A follow-up is **not** a revision.

## 3. What is the System Learner ID?

The template contains a dashboard-generated value such as:

`SBI-7F3A9C2D4E1B8A4C`

This is not a DepEd LRN. The RHU encoder does not create or edit it.

The same learner must keep the same **System Learner ID** on later activity dates and during corrections. The ID allows the dashboard to recognize the learner without using the LRN as the matching key.

### Do not

- type a new System Learner ID yourself;
- replace an existing System Learner ID;
- copy one learner's ID to another learner.

## 4. NEW ACTIVITY — first vaccination activity

Use this when the learners are being recorded for their first activity in this workbook/workflow.

1. Open **1. Upload Learner Records**.
2. Click **1A. Download Fresh SBI Line List Template (Excel)**.
3. Use one row per learner per Activity Date.
4. Enter the correct School, Grade, Section, Sex, Activity Date, and vaccination outcome fields.
5. Do not edit the **System Learner ID**.
6. Save the workbook.
7. Under **1B. Upload completed line list**, upload the file.
8. Correct any validation errors shown by the dashboard.
9. Review the **Added / Modified / Removed / Unchanged** comparison.
10. Confirm only after the comparison is correct.
11. Continue to **2. VaccTrack Encoding**.

### Grade 1 and Grade 7

Complete both **MR Status** and **Td Status** for every activity row.

Allowed statuses:

- Given
- Deferred
- Refused
- Not Given

If only one vaccine was administered that day, mark that vaccine **Given** and the other **Not Given**, unless the other vaccine was specifically Deferred or Refused.

### Grade 4 Female

Complete:

- HPV Dose: 1 or 2
- HPV Status

Leave MR and Td fields blank.

### Deferred / Refused

If a vaccine is Deferred or Refused, select the correct Reason Code. Add Reason Details only when useful.

## 5. FOLLOW-UP ACTIVITY — learner returns on another date

A follow-up vaccination is a **new activity record** for the same learner.

Do **not** edit yesterday's Activity Date to today's date and do **not** replace yesterday's result.

### Recommended method: Create Follow-up Line List

1. Open **1. Upload Learner Records**.
2. Expand **Create Follow-up Line List**.
3. Upload the previous v5.19+ workbook containing those learners.
4. Download the generated follow-up workbook.
5. The dashboard keeps the same System Learner IDs and learner identity rows but clears Activity Date and vaccination outcome fields.
6. Enter a new Activity Date and outcomes **only for learners who actually have a new follow-up activity**.
7. Leave learners with no new activity untouched; blank identity-only rows are ignored during upload.
8. Upload the completed follow-up workbook through **1B**.
9. Review the comparison and confirm.

The same follow-up workbook may contain different new Activity Dates for different learners.

### Example — MR yesterday, Td today

For the same learner:

| Activity | Activity Date | MR Status | Td Status |
|---|---|---|---|
| Yesterday | Sep 23 | **Given** | **Not Given** |
| Today | Sep 24 | **Not Given** | **Given** |

Both rows use the **same System Learner ID**.

Do not copy yesterday's `MR = Given` into today's row. If you do, the dashboard will count another MR dose today.

## 6. MULTIPLE ACTIVITY DATES

The same learner can appear on several activity dates. This is normal when vaccination is completed in separate sessions.

The dashboard treats these as separate activity records because the Activity Dates are different.

An exact duplicate of:

**Activity Date + School + Grade + System Learner ID**

is not allowed as another separate record.

### Example

A Grade 1 learner may legitimately have:

- Sep 23 — MR Given / Td Not Given
- Sep 24 — MR Not Given / Td Given

The Sep 23 record remains unchanged when Sep 24 is uploaded.

## 7. REVISION / CORRECTION — fixing an existing saved activity

Use Revision when the **Activity Date is staying the same** and you are correcting information already saved for that date.

Examples:

- MR/Td/HPV status was encoded incorrectly;
- lot/batch number was wrong;
- reason code/details were wrong;
- section or remarks need correction.

### How to revise safely

1. Open **Corrections / History** to review the affected date and previous import batch.
2. Open the workbook that contains the affected activity.
3. Keep the same **System Learner ID**.
4. Keep the same **Activity Date**.
5. Correct the field(s).
6. Make sure the workbook contains the **complete current list for the affected Activity Date + School + Grade group**.
7. Return to **1. Upload Learner Records** and upload the corrected workbook.
8. Review **Added / Modified / Removed / Unchanged** carefully.
9. Confirm only when the comparison is correct.

### Important: do not upload only the one corrected learner

For every **Activity Date + School + Grade** group included in an upload, the dashboard treats the uploaded rows as the complete current list for that group.

If a previously saved learner is missing from that group, the dashboard will show that learner as **Removed**.

## 8. WHAT IF THE WRONG FIELD IS ACTIVITY DATE, SCHOOL, OR GRADE?

These fields need extra care because they help define where the saved record belongs.

### Wrong Activity Date

Do **not** simply add the same learner using the correct date. That can leave both the wrong-date row and the correct-date row saved.

If the wrong-date group still contains other learners, a corrected complete upload can remove the learner from the old group and add the learner on the correct date. Review the **REMOVE** and **ADD** preview before confirming.

If the whole batch/date was wrong, or the wrong-date group would become empty, ask the NIP/System Admin to remove the incorrect import in **Administration → Import Management**, then upload the corrected activity file.

### Wrong School or Grade

Do not assign the same System Learner ID to a different School or Grade just to force the correction. The dashboard protects against accidental reassignment and may block it.

Ask the NIP/System Admin to clean up the incorrect imported record/batch first, then re-upload the corrected record using the proper workflow.

### Wrong System Learner ID

Do not manually edit the ID. If an ID was accidentally assigned to the wrong learner, stop and ask the NIP/System Admin before importing more follow-up records with that ID.

## 9. Understanding the upload comparison

Before an import is saved, the dashboard may show:

- **Added** — a new saved activity record will be created.
- **Modified** — an existing record in the same activity group will be updated.
- **Removed** — a previously saved learner in an included Activity Date + School + Grade group is missing from the new complete list and will be made inactive.
- **Unchanged** — the uploaded record already matches the saved record.

### What should I expect?

- New Activity: mostly **Added**.
- Follow-up Activity on a new date: mostly **Added**.
- Simple correction on the same date: usually **Modified**.
- Correcting a wrong date: may show **Removed** from the old date and **Added** on the correct date.
- Exact same file uploaded again: no changes / already up to date.

Never confirm a large unexpected number of **Removed** records.

## 10. Step 2 — VaccTrack Encoding

After the learner activity is saved:

1. Open **2. VaccTrack Encoding**.
2. Select the Activity / Report Date to encode.
3. Review the generated Grade 1, Grade 4, and Grade 7 totals.
4. Copy the required figures and reason totals into VaccTrack.

For a follow-up day, encode the figures generated for that follow-up Activity Date only.

## 11. Step 3 — Refresh & VaccTrack Check

After the NIP coordinator has uploaded the latest VaccTrack extract:

1. Open **3. VaccTrack Check**.
2. Refresh the VaccTrack data.
3. Review the status.

### Status meanings

- **Matched** — RHU learner-record totals and the latest available VaccTrack extract agree.
- **Pending VaccTrack Verification** — the latest official extract has not yet caught up to that activity date. This is **not automatically an encoding error**.
- **Check VaccTrack** — RHU learner-derived total is higher than VaccTrack for the metric.
- **Check RHU Tracker / Line List** — VaccTrack is higher than the RHU learner-derived total.

If something does not match, first check Activity Date, School, Grade, vaccination status, and whether a previous dose was accidentally carried into a follow-up row.

## 12. Quick decision guide

**Was there a vaccination activity on a NEW DATE?**  
→ Yes: use **New / Follow-up Activity**. Create a new row using the same System Learner ID.

**Are you fixing a field for an activity that is already saved on the SAME DATE?**  
→ Yes: use **Revision / Correction** and upload the complete affected group.

**Was the saved Activity Date itself wrong?**  
→ Do not just add the correct date. Follow the wrong-date correction instructions in Section 8.

**Was the saved School, Grade, or System Learner ID wrong?**  
→ Ask NIP/System Admin before re-uploading so the incorrect identity assignment can be cleaned safely.

## 13. Common mistakes to avoid

- Do not edit System Learner IDs.
- Do not create a new System Learner ID just because the learner returned on another date.
- Do not overwrite yesterday's activity with today's follow-up.
- Do not carry yesterday's `Given` status into today's row unless that vaccine was actually given again today.
- Do not use Revision to record a new vaccination date.
- Do not upload only one learner when revising a group unless that learner is truly the complete group.
- Do not confirm unexpected removals.
- Do not upload records belonging to another municipality.
- Do not place names, LRN, or unnecessary identifying information in Remarks or Reason Details.

## 14. Examples

### A. MR yesterday, Td today

Yesterday: MR Given / Td Not Given.  
Today: MR Not Given / Td Given.  
Same System Learner ID. Two activity rows.

### B. Yesterday's Td should have been Deferred

Keep yesterday's date and same System Learner ID. Change Td Status to Deferred, add the reason code, and upload the complete affected group. This is a **Revision**.

### C. Grade 4 learner receives HPV Dose 2 later

Keep the same System Learner ID and add a new row for the new Activity Date. Encode HPV Dose 2 and its outcome. This is a **Follow-up Activity**.

### D. Learner did not return today

Do not create a new activity row unless there is a new outcome to record. The previous activity remains saved.

### E. Entire activity was encoded under the wrong date

Do not duplicate the activity under the correct date. Ask NIP/System Admin to remove the incorrect import/batch, then upload the corrected activity file.


## 15. Training / Practice Mode

Use **Training / Practice Mode** when you want to try the workflow without saving production data.

- Practice uploads are validated using the same line-list rules.
- You can set a practice baseline and upload another file to see Added / Modified / Removed / Unchanged results.
- You can also generate a practice follow-up workbook.
- Nothing in Training Mode is written to the SBI production tables.
- The practice baseline lasts only in the current browser session and can be reset at any time.

Use Training Mode for orientation and dry runs. When you are ready to submit real activity data, return to **1. Upload Learner Records**.

## 16. Send Feedback / Report a Problem

During the rollout, use **Send Feedback / Report a Problem** to report confusing instructions, upload problems, follow-up questions, mobile display issues, or suggestions.

Choose the category and page, then describe what happened. The dashboard automatically includes your account, municipality, and dashboard version so the NIP team can trace the issue more easily.

Do not include learner names, LRN, or other identifying information in feedback.

## 17. VaccTrack source and freshness

Inside **3. VaccTrack Check**, the dashboard shows the current source and the latest report date available for Grade 1, Grade 4, and Grade 7.

- **Direct VaccTrack upload** means the latest completed extract uploaded by the NIP/System Admin is being used.
- **Google Sheet fallback** means no direct snapshot is currently available for that grade and the System Admin has left fallback enabled.
- If fallback is disabled, the dashboard uses direct VaccTrack uploads only.
- **Data Through** shows the latest report date found in that source.

Always check the Data Through date before treating a pending result as an encoding problem.
