# Abra NIP Monitoring Information System v5.21.1.4

## Final RHU Submission UX hotfix

This update makes the RHU final-submission action harder to mistake for a normal post-upload step.

### Changes

- The final-submission control is hidden while the SBI campaign is in **Pre-Implementation**.
- During **Live**, it appears only inside a collapsed section named **End of SBI only — Submit Final RHU Report**.
- The section clearly states that it is **not required after each workbook upload**.
- RHUs are instructed to keep updating and re-uploading the same workbook throughout SBI.
- The confirmation now states that the RHU has finished SBI reporting for the activity.
- The final action is renamed to **Submit Final RHU Report & Lock Further Uploads**.
- Help/guide wording was updated to match.

No SQL migration is required.
