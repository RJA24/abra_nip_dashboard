from __future__ import annotations

from datetime import date, datetime

import pytz

from core.data import SBI_SETTINGS_TABLE


MANILA_TZ = pytz.timezone("Asia/Manila")
CAMPAIGN_STATUS_KEY = "sbi_campaign_status"
CAMPAIGN_START_KEY = "sbi_campaign_start_date"
CAMPAIGN_END_KEY = "sbi_campaign_end_date"
CAMPAIGN_ANNOUNCEMENT_KEY = "sbi_campaign_announcement"
CAMPAIGN_STATUSES = ("Pre-Implementation", "Live", "Post-Activity Correction", "Closed")


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def get_campaign_config(supabase) -> dict[str, object]:
    defaults = {
        "status": "Pre-Implementation",
        "start_date": None,
        "end_date": None,
        "announcement": "",
    }
    try:
        response = (
            supabase.table(SBI_SETTINGS_TABLE)
            .select("setting_key,setting_value")
            .in_(
                "setting_key",
                [CAMPAIGN_STATUS_KEY, CAMPAIGN_START_KEY, CAMPAIGN_END_KEY, CAMPAIGN_ANNOUNCEMENT_KEY],
            )
            .execute()
        )
    except Exception:
        return defaults

    values = {
        str(row.get("setting_key") or ""): str(row.get("setting_value") or "")
        for row in (response.data or [])
    }
    status = values.get(CAMPAIGN_STATUS_KEY, defaults["status"])
    if status not in CAMPAIGN_STATUSES:
        status = defaults["status"]
    return {
        "status": status,
        "start_date": _parse_date(values.get(CAMPAIGN_START_KEY)),
        "end_date": _parse_date(values.get(CAMPAIGN_END_KEY)),
        "announcement": values.get(CAMPAIGN_ANNOUNCEMENT_KEY, "").strip(),
    }


def save_campaign_config(
    supabase,
    *,
    status: str,
    start_date: date | None,
    end_date: date | None,
    announcement: str,
    actor: str,
) -> None:
    if status not in CAMPAIGN_STATUSES:
        raise ValueError("Invalid SBI campaign status.")
    if start_date and end_date and end_date < start_date:
        raise ValueError("Campaign end date cannot be earlier than the start date.")

    now = datetime.now(MANILA_TZ).isoformat()
    rows = [
        {
            "setting_key": CAMPAIGN_STATUS_KEY,
            "setting_value": status,
            "updated_at": now,
            "updated_by": actor,
        },
        {
            "setting_key": CAMPAIGN_START_KEY,
            "setting_value": start_date.isoformat() if start_date else "",
            "updated_at": now,
            "updated_by": actor,
        },
        {
            "setting_key": CAMPAIGN_END_KEY,
            "setting_value": end_date.isoformat() if end_date else "",
            "updated_at": now,
            "updated_by": actor,
        },
        {
            "setting_key": CAMPAIGN_ANNOUNCEMENT_KEY,
            "setting_value": announcement.strip(),
            "updated_at": now,
            "updated_by": actor,
        },
    ]
    supabase.table(SBI_SETTINGS_TABLE).upsert(rows, on_conflict="setting_key").execute()


def activity_date_issues(frame, config: dict[str, object]) -> list[str]:
    if frame is None or frame.empty or "activity_date" not in frame.columns:
        return []
    start_date = config.get("start_date")
    end_date = config.get("end_date")
    if not start_date and not end_date:
        return []

    dates = frame["activity_date"]
    issues: list[str] = []
    if start_date:
        before = sorted({value for value in dates if value and value < start_date})
        if before:
            issues.append(
                f"Workbook contains activity date(s) before the configured SBI start date ({start_date:%b %d, %Y})."
            )
    if end_date:
        after = sorted({value for value in dates if value and value > end_date})
        if after:
            issues.append(
                f"Workbook contains activity date(s) after the configured SBI end date ({end_date:%b %d, %Y})."
            )
    return issues
