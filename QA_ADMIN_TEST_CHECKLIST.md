# QA Admin Test Checklist — v5.20.2

Use this checklist with the read-only QA account.

## First login

- Sign in with the temporary QA Admin password.
- Confirm the dashboard requires a new personal password.
- Confirm the new password works on the next login.

## Main menu

- Confirm **MR SIA**, **SBI**, **Account Settings**, and **Administration** are visible.
- Open MR SIA and verify normal filters, charts, tables, and downloads work.
- Open SBI and verify province/municipality views, charts, tables, and downloads work.

## Administration

- Confirm the yellow **QA ADMIN — READ-ONLY TEST ACCOUNT** notice is visible.
- Open every tab: Overview, Operations, Data Sync, Import Management, Map Labels, RHU Accounts, Admin Accounts, Login History, and Audit Log.

## Read-only safeguards

Confirm these controls are visible but disabled or cannot apply a production change:

- MR SIA target sync
- SBI target sync
- VaccTrack final import
- Google Sheet fallback on/off setting
- Line-list batch deletion
- Manual fallback record deletion
- Legacy regional-session deletion
- VaccTrack snapshot deletion
- Map-label save/reset actions
- RHU account create/reset/assignment/status/delete actions
- System Admin account create/reset/status/delete actions
- QA Admin account create/reset/status/delete actions
- Feedback status/admin-note update

## Safe QA actions

- Upload a VaccTrack file and confirm parsing/validation preview works without importing it.
- Review RHU rollout status and System Health.
- Review Feedback Inbox, Login History, and Audit Log.
- Download available CSV/Excel reports and the administrative backup.
- Change the QA account's own password from Account Settings.

## Finish

Send any issue using the built-in feedback form or report the page, action, expected result, and actual result to the System Admin.
