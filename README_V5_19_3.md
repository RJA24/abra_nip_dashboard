# Abra NIP Dashboard v5.19.3 — RHU Password Change & Reset

This is a cumulative patch on top of v5.19.2. It keeps the System Learner ID, follow-up, correction, and encoder-guide changes from v5.19.2 and adds password handling for RHU Encoder accounts.

## What changed

### RHU temporary passwords are now temporary

New RHU Encoder accounts are marked **Password Change Required** when the System Admin creates them.

On first login, the RHU Encoder is stopped before the program menu and must create a new password. The new password must:

- contain at least 8 characters;
- match the confirmation field; and
- be different from the current temporary password.

After a successful change, the account continues normally and the requirement is cleared.

### Existing RHU accounts

The new migration adds `must_change_password` to `user_accounts`. The first time the migration is applied, existing **RHU Encoder** accounts are marked to change their password at the next login.

The migration is safe to run again: it does not re-flag RHU accounts after the column already exists.

### System Admin password reset

Administration → **RHU Accounts → Reset RHU Password** still lets the System Admin reset an RHU account.

The reset password is treated as a temporary password. After a reset:

- failed login attempts are cleared;
- `must_change_password` is set to `true`; and
- the RHU Encoder must create a new password on the next login.

The System Admin does not need to know the RHU Encoder's new personal password.

### Account Settings

Registered accounts now have **Account Settings** on the main program menu. A user can change their password by entering:

- current password;
- new password; and
- confirmation of the new password.

Guest users do not get Account Settings.

### Cleaner SBI loading screen

The internal `_fetch_sbi_vacctrack_google_cached()` function name is no longer shown as a Streamlit loading message. The Google Sheet fallback behavior itself is unchanged.

## Database migration

Run once in Supabase SQL Editor:

```text
supabase/008_user_password_change.sql
```

Run it after the existing RHU account migrations. If v5.19.1+ is already deployed, keep `007_sbi_system_learner_id.sql` in place; do not roll it back.

## Files to replace

```text
app.py
auth_utils.py
admin/dashboard.py
core/data.py
programs/sbi/linelist.py
programs/sbi/rhu_tracker.py
programs/sbi/help_content.py
programs/sbi/assets/SBI_Linelist_Template.xlsx
docs/SBI_RHU_Encoder_Full_Guide.md
docs/SBI_RHU_Encoder_FAQ.md
```

## Files to add

```text
supabase/008_user_password_change.sql
```

The included `007_sbi_system_learner_id.sql` is unchanged and is only needed if the v5.19 System Learner ID migration has not yet been applied.

## Suggested quick test

1. Run `supabase/008_user_password_change.sql`.
2. Sign in with an existing RHU Encoder account using its current/default password.
3. Confirm the dashboard shows **Set Your New Password** before the main program menu.
4. Try fewer than 8 characters and confirm it is rejected.
5. Try the same password as the temporary password and confirm it is rejected.
6. Set a new password and confirm the main program menu opens.
7. Log out and confirm the old temporary password no longer works and the new password does.
8. Sign in as System Admin and open **Administration → RHU Accounts**.
9. Confirm the RHU account table shows **Password Change Required**.
10. Reset one RHU account to another temporary password.
11. Sign in with that temporary password and confirm the password-change screen appears again.
12. From the main program menu, open **Account Settings** and test a normal voluntary password change using the current password.
13. Open SBI and confirm the technical `_fetch_sbi_vacctrack_google_cached()` loading label no longer appears.
14. Recheck the v5.19.2 learner upload, follow-up, and correction workflows.
