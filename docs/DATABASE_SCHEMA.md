# Database Schema Summary — v5.22.0

This is an operational summary, not a substitute for the SQL migrations.

## Core application/account tables

### `user_accounts`
Application-managed accounts, role, assigned municipality, account state, and temporary-password/change state. RHU municipality restrictions are enforced by application logic.

### `access_logs`
Login and Admin audit/activity records.

## SBI production tables

### `sbi_targets`
School roster and grade target population used for workbook generation, dropdown/reference data, validation, and coverage calculations.

### `sbi_rhu_accomplishments`
Current RHU aggregate SBI data. The natural production scope is Municipality + School + Activity Date + Grade. Stores G1/G7 MR/Td, G4 HPV, deferred/refused fields, reason-code JSON, source metadata, and timestamps.

Migration 011 adds the current aggregate deferred/refused/reason fields used by the offline workbook workflow.

### `sbi_rhu_workbook_submissions`
Created by migration 012. Stores workbook upload history, version, uploader/time, row/date range, Added/Modified/Removed/Unchanged counts, restorable JSON snapshot, current/finalized flags, finalization metadata, and restore lineage.

### `sbi_vacctrack_imports`
Metadata for direct official VaccTrack snapshots by grade, including filename/hash, row counts, date range, import status, importer, and completion time.

### `sbi_vacctrack_rows`
Raw row JSON belonging to a VaccTrack import. Rows cascade when their parent import is deleted.

### `sbi_settings`
Key/value operational settings, including VaccTrack fallback and SBI campaign controls.

### `sbi_user_feedback`
RHU feedback/problem reports with context, app version, status, and submission metadata.

## Historical / legacy SBI tables

The following may still exist from retired workflows and can be retained for audit/cleanup:

- `sbi_linelist_imports`
- `sbi_linelist_records`
- `sbi_linelist_audit`
- `sbi_regional_sessions`

They are not required for the current aggregate-workbook RHU reporting workflow. System Health displays them separately from required operational components.

## Important implementation note

The application uses a Supabase service connection rather than user-specific Supabase Auth/JWT identity. Therefore RHU/QA restrictions are application/service-layer controls, not true user-level database RLS isolation.
