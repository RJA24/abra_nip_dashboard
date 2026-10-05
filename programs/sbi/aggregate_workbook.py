from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
import hashlib

import pandas as pd
import pytz
import streamlit as st
import xlsxwriter

from core.map_labels import canonical_municipality_name, normalize_municipality_key
from programs.sbi.campaign_control import activity_date_issues, get_campaign_config


MANILA_TZ = pytz.timezone("Asia/Manila")
TABLE_NAME = "sbi_rhu_accomplishments"
SUBMISSION_TABLE = "sbi_rhu_workbook_submissions"
WORKBOOK_VERSION = "SBI-AGGREGATE-2026-v1"
MAX_INPUT_ROWS = 1200
PROTECTION_PASSWORD = "AbraNIPSBI2026"

REASON_LABELS = {
    "01": "Parent/caregiver not home or decision-maker (e.g., spouse) unavailable",
    "02": "Fear of vaccine side effects",
    "03": "Concerns over vaccine safety (e.g., past adverse reaction, Dengvaxia)",
    "04": "Refused extra dose (already completed routine vaccines) [campaign-specific]",
    "05": "No time due to work or caregiving; no one to accompany child",
    "06": "Belief that vaccine is not effective, low quality, or expired",
    "07": "Belief that child is too young for vaccination",
    "08": "Already vaccinated or advised against it by private doctor",
    "09": "Religious or personal beliefs not aligned with vaccination",
    "10": "Lack of trust in the vaccinator",
    "11": "Child was sick, just recovered, or recently discharged from hospital",
    "12": "Unaware of vaccination schedule/activity",
    "13": "No health worker visit or vaccine promotion in the area",
    "14": "Child is visiting, recently moved, or not in target client list",
    "15": "Too far from site or no transportation (geographical challenges)",
    "16": "Language or communication barrier",
    "17": "Caregiver has disability or health condition limiting access",
    "18": "Fear of injection",
    "19": "Refused with no reason or Other",
}

BASE_COLUMNS = [
    "Activity Date",
    "School Name",
    "School ID",
    "Grade Level",
    "Barangay",
    "Actual Target",
    "MR Male",
    "MR Female",
    "Td Male",
    "Td Female",
    "MR Deferred",
    "Td Deferred",
    "MR Refused",
    "Td Refused",
    "HPV Dose 1",
    "HPV Dose 2",
    "HPV1 Deferred",
    "HPV2 Deferred",
    "HPV1 Refused",
    "HPV2 Refused",
]
REASON_COLUMNS = [f"Reason {code}" for code in REASON_LABELS]
ALL_COLUMNS = BASE_COLUMNS + REASON_COLUMNS + ["Row Check"]
INPUT_COUNT_COLUMNS = [
    "MR Male",
    "MR Female",
    "Td Male",
    "Td Female",
    "MR Deferred",
    "Td Deferred",
    "MR Refused",
    "Td Refused",
    "HPV Dose 1",
    "HPV Dose 2",
    "HPV1 Deferred",
    "HPV2 Deferred",
    "HPV1 Refused",
    "HPV2 Refused",
] + REASON_COLUMNS

GRADE_TO_CODE = {"Grade 1": "G1", "G1": "G1", "Grade 4": "G4", "G4": "G4", "Grade 7": "G7", "G7": "G7"}



def workbook_schema_available(supabase) -> bool:
    try:
        supabase.table(TABLE_NAME).select(
            "id,mr_deferred,td_deferred,mr_refused,td_refused,hpv1_deferred,hpv2_deferred,hpv1_refused,hpv2_refused,reason_counts"
        ).limit(1).execute()
        supabase.table(SUBMISSION_TABLE).select("id,is_current,is_finalized,snapshot").limit(1).execute()
        return True
    except Exception:
        return False


def submission_schema_available(supabase) -> bool:
    try:
        supabase.table(SUBMISSION_TABLE).select("id").limit(1).execute()
        return True
    except Exception:
        return False

def _clean_school_id(value: object) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _same_muni(value: object, municipality: str) -> bool:
    return normalize_municipality_key(value) == normalize_municipality_key(municipality)


def _roster(targets: pd.DataFrame, municipality: str) -> pd.DataFrame:
    columns = ["Municipality", "Barangay", "School ID", "School Name", "G1 Total", "G4 Female", "G7 Total"]
    if targets is None or targets.empty:
        return pd.DataFrame(columns=["School ID", "School Name", "Barangay", "G1 Target", "G4 Female Target", "G7 Target"])

    work = targets.copy()
    if "Municipality" not in work.columns:
        return pd.DataFrame(columns=["School ID", "School Name", "Barangay", "G1 Target", "G4 Female Target", "G7 Target"])
    work = work.loc[work["Municipality"].map(lambda value: _same_muni(value, municipality))].copy()
    for column in columns:
        if column not in work.columns:
            work[column] = 0 if column in {"G1 Total", "G4 Female", "G7 Total"} else ""
    work["School ID"] = work["School ID"].map(_clean_school_id)
    work["School Name"] = work["School Name"].fillna("").astype(str).str.strip()
    work["Barangay"] = work["Barangay"].fillna("").astype(str).str.strip()
    for column in ["G1 Total", "G4 Female", "G7 Total"]:
        work[column] = pd.to_numeric(work[column], errors="coerce").fillna(0)
    work = work.loc[work["School ID"].ne("")].copy()
    work = (
        work.groupby("School ID", as_index=False)
        .agg({
            "School Name": "first",
            "Barangay": "first",
            "G1 Total": "sum",
            "G4 Female": "sum",
            "G7 Total": "sum",
        })
        .rename(columns={"G1 Total": "G1 Target", "G4 Female": "G4 Female Target", "G7 Total": "G7 Target"})
        .sort_values("School Name")
        .reset_index(drop=True)
    )
    return work


def _column_letter(index: int) -> str:
    letters = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def build_offline_workbook(targets: pd.DataFrame, municipality: str) -> bytes:
    municipality = canonical_municipality_name(municipality)
    roster = _roster(targets, municipality)
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    workbook.set_properties({
        "title": f"SBI Offline Accomplishment Workbook - {municipality}",
        "subject": "School-Based Immunization offline accomplishment and VaccTrack encoding workbook",
        "author": "Abra NIP Monitoring Information System",
    })

    blue = "#0033A0"
    light_blue = "#EAF1FB"
    light_gray = "#F3F4F6"
    dark = "#0F172A"
    green = "#DCFCE7"
    amber = "#FEF3C7"
    red = "#FEE2E2"

    title_fmt = workbook.add_format({"bold": True, "font_size": 16, "font_color": blue})
    subtitle_fmt = workbook.add_format({"font_size": 10, "font_color": "#475569", "text_wrap": True})
    section_fmt = workbook.add_format({"bold": True, "font_size": 11, "font_color": dark, "bg_color": light_blue, "border": 1, "border_color": "#CBD5E1"})
    header_fmt = workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": blue, "border": 1, "border_color": "#D1D5DB", "text_wrap": True, "valign": "vcenter", "align": "center"})
    input_fmt = workbook.add_format({"border": 1, "border_color": "#F59E0B", "bg_color": "#FFFBEB", "locked": False})
    formula_fmt = workbook.add_format({"border": 1, "border_color": "#E5E7EB", "bg_color": light_gray, "font_color": "#334155"})
    date_fmt = workbook.add_format({"border": 1, "border_color": "#E5E7EB", "num_format": "mmm d, yyyy"})
    input_date_fmt = workbook.add_format({"border": 1, "border_color": "#F59E0B", "bg_color": "#FFFBEB", "num_format": "mmm d, yyyy", "locked": False})
    count_fmt = workbook.add_format({"border": 1, "border_color": "#E5E7EB", "num_format": "0"})
    input_count_fmt = workbook.add_format({"border": 1, "border_color": "#F59E0B", "bg_color": "#FFFBEB", "num_format": "0", "locked": False})
    vac_date_fmt = workbook.add_format({"border": 1, "border_color": "#F59E0B", "bg_color": "#FFFBEB", "num_format": "mmm d, yyyy", "bold": True, "locked": False})
    note_fmt = workbook.add_format({"font_color": "#475569", "text_wrap": True, "valign": "top"})
    warning_fmt = workbook.add_format({"bg_color": amber, "font_color": "#92400E", "text_wrap": True, "border": 1, "border_color": "#F59E0B"})
    ok_fmt = workbook.add_format({"bg_color": green, "font_color": "#166534"})
    bad_fmt = workbook.add_format({"bg_color": red, "font_color": "#991B1B"})
    helper_fmt = workbook.add_format({"bg_color": light_gray, "font_color": "#475569", "border": 1, "border_color": "#E5E7EB"})

    setup = workbook.add_worksheet("Setup")
    setup.hide_gridlines(2)
    setup.set_column("A:A", 18)
    setup.set_column("B:B", 22)
    setup.set_column("C:C", 18)
    setup.set_column("D:D", 22)
    setup.set_column("E:E", 18)
    setup.set_column("F:F", 22)

    setup_title = workbook.add_format({
        "bold": True,
        "font_size": 18,
        "font_color": "#FFFFFF",
        "bg_color": blue,
        "align": "center",
        "valign": "vcenter",
    })
    setup_subtitle = workbook.add_format({
        "font_size": 10,
        "font_color": "#334155",
        "bg_color": "#EFF6FF",
        "align": "center",
        "valign": "vcenter",
        "text_wrap": True,
        "bottom": 1,
        "bottom_color": "#BFDBFE",
    })
    setup_label = workbook.add_format({
        "bold": True,
        "font_color": dark,
        "bg_color": light_blue,
        "border": 1,
        "border_color": "#CBD5E1",
        "valign": "vcenter",
    })
    setup_value = workbook.add_format({
        "font_color": "#334155",
        "bg_color": "#FFFFFF",
        "border": 1,
        "border_color": "#CBD5E1",
        "valign": "vcenter",
    })
    setup_input = workbook.add_format({
        "bold": True,
        "font_color": dark,
        "bg_color": "#FFF7ED",
        "border": 1,
        "border_color": "#FDBA74",
        "valign": "vcenter",
    })
    guide_header = workbook.add_format({
        "bold": True,
        "font_size": 13,
        "font_color": "#FFFFFF",
        "bg_color": blue,
        "align": "center",
        "valign": "vcenter",
    })
    step_number = workbook.add_format({
        "bold": True,
        "font_size": 15,
        "font_color": "#FFFFFF",
        "bg_color": "#2563EB",
        "align": "center",
        "valign": "vcenter",
        "border": 1,
        "border_color": "#93C5FD",
    })
    step_title = workbook.add_format({
        "bold": True,
        "font_size": 11,
        "font_color": "#1E3A8A",
        "bg_color": "#DBEAFE",
        "align": "left",
        "valign": "vcenter",
        "border": 1,
        "border_color": "#93C5FD",
    })
    step_text = workbook.add_format({
        "font_color": "#334155",
        "bg_color": "#F8FAFC",
        "text_wrap": True,
        "valign": "top",
        "border": 1,
        "border_color": "#CBD5E1",
    })
    do_header = workbook.add_format({
        "bold": True,
        "font_size": 12,
        "font_color": "#166534",
        "bg_color": green,
        "align": "center",
        "valign": "vcenter",
        "border": 1,
        "border_color": "#86EFAC",
    })
    dont_header = workbook.add_format({
        "bold": True,
        "font_size": 12,
        "font_color": "#991B1B",
        "bg_color": red,
        "align": "center",
        "valign": "vcenter",
        "border": 1,
        "border_color": "#FCA5A5",
    })
    do_text = workbook.add_format({
        "font_color": "#166534",
        "bg_color": "#F0FDF4",
        "text_wrap": True,
        "valign": "top",
        "border": 1,
        "border_color": "#BBF7D0",
    })
    dont_text = workbook.add_format({
        "font_color": "#991B1B",
        "bg_color": "#FEF2F2",
        "text_wrap": True,
        "valign": "top",
        "border": 1,
        "border_color": "#FECACA",
    })
    sheet_name_fmt = workbook.add_format({
        "bold": True,
        "font_color": blue,
        "bg_color": light_blue,
        "border": 1,
        "border_color": "#CBD5E1",
        "valign": "vcenter",
    })
    sheet_use_fmt = workbook.add_format({
        "font_color": "#334155",
        "bg_color": "#FFFFFF",
        "border": 1,
        "border_color": "#CBD5E1",
        "text_wrap": True,
        "valign": "vcenter",
    })

    setup.merge_range("A1:F1", "SBI OFFLINE ACCOMPLISHMENT WORKBOOK", setup_title)
    setup.set_row(0, 32)
    setup.merge_range(
        "A2:F2",
        "Your RHU working file for offline accomplishment encoding, VaccTrack preparation, corrections, and re-upload.",
        setup_subtitle,
    )
    setup.set_row(1, 30)

    setup.write("A4", "Municipality", setup_label)
    setup.merge_range("B4:C4", municipality, setup_value)
    setup.write("D4", "System", setup_label)
    setup.merge_range("E4:F4", "Abra NIP Monitoring Information System", setup_value)
    setup.write("A5", "Workbook Scope", setup_label)
    setup.merge_range("B5:C5", "One RHU workbook for the entire SBI activity", setup_value)
    setup.write("D5", "Workbook Version", setup_label)
    setup.merge_range("E5:F5", WORKBOOK_VERSION, setup_value)

    setup.set_row(3, 24)
    setup.set_row(4, 26)

    setup.merge_range("A7:F7", "HOW TO USE THIS FILE", guide_header)
    setup.set_row(6, 26)

    steps = [
        (
            "1",
            "ENCODE OFFLINE",
            "Open Accomplishments. Use one row per activity entry. Enter Activity Date, select School Name from the dropdown, choose Grade Level, then enter the applicable counts. School ID and Barangay fill automatically; the Actual Target is kept hidden for internal reference.",
            "A9:C11",
        ),
        (
            "2",
            "PREPARE VACC TRACK",
            "Open VaccTrack G1, G4 or G7. Set the Report Date at the top. Use the rows marked YES and encode the displayed values into VaccTrack.",
            "D9:F11",
        ),
        (
            "3",
            "UPLOAD WHEN ONLINE",
            "When internet is available, upload this complete workbook to the monitoring system. Review Added / Modified / Removed / Unchanged before confirming.",
            "A13:C15",
        ),
        (
            "4",
            "CORRECT IN THE SAME FILE",
            "If something is wrong, edit Accomplishments in this same workbook, save it, then re-upload the complete current workbook. Do not start a separate correction file.",
            "D13:F15",
        ),
    ]
    for number, heading, text, area in steps:
        start, end = area.split(":")
        start_col = ord(start[0]) - 65
        start_row = int(start[1:]) - 1
        end_col = ord(end[0]) - 65
        end_row = int(end[1:]) - 1
        setup.write(start_row, start_col, number, step_number)
        setup.merge_range(start_row, start_col + 1, start_row, end_col, heading, step_title)
        setup.merge_range(start_row + 1, start_col, end_row, end_col, text, step_text)
    for row in [8, 12]:
        setup.set_row(row, 24)
        setup.set_row(row + 1, 34)
        setup.set_row(row + 2, 34)

    setup.merge_range("A17:C17", "DO", do_header)
    setup.merge_range("D17:F17", "DON'T", dont_header)
    setup.merge_range(
        "A18:C22",
        "• Keep one workbook for the entire SBI activity.\n"
        "• Keep every activity date and school in Accomplishments.\n"
        "• Save the file after every encoding session.\n"
        "• Check Row Check and correct any row marked CHECK.\n"
        "• Enter data only in the light-yellow input cells.\n"
        "• Re-upload the complete workbook after corrections.",
        do_text,
    )
    setup.merge_range(
        "D18:F22",
        "• Do not rename, delete or rearrange workbook sheets.\n"
        "• Do not change the Accomplishments column headings.\n"
        "• Do not try to edit gray/calculated cells; they are locked.\n"
        "• Do not upload a VaccTrack export as your RHU workbook.\n"
        "• Do not delete an old row unless that accomplishment should be removed.\n"
        "• Do not create a new workbook just to make a correction.",
        dont_text,
    )
    for row in range(17, 22):
        setup.set_row(row, 24)

    setup.merge_range("A24:F24", "WHAT EACH SHEET IS FOR", guide_header)
    sheet_guide = [
        ("Setup", "Start here. Review the workflow and workbook reminders."),
        ("Accomplishments", "Main offline encoding sheet. Keep all SBI activity dates, schools and aggregate counts here."),
        ("VaccTrack G1", "Daily Grade 1 values arranged for VaccTrack."),
        ("VaccTrack G4", "Daily Grade 4 HPV values arranged for VaccTrack."),
        ("VaccTrack G7", "Daily Grade 7 values arranged for VaccTrack."),
    ]
    for row, (sheet_name, purpose) in enumerate(sheet_guide, start=24):
        setup.write(row, 0, sheet_name, sheet_name_fmt)
        setup.merge_range(row, 1, row, 5, purpose, sheet_use_fmt)
        setup.set_row(row, 24)

    setup.merge_range("A31:F31", "BEFORE YOU UPLOAD", guide_header)
    setup.merge_range(
        "A32:F33",
        "Make sure this workbook contains your RHU's COMPLETE CURRENT SBI accomplishment data. "
        "The latest confirmed upload becomes the dashboard's current RHU dataset. If an old Date + School + Grade entry is removed from the workbook, the system will treat it as removed after you confirm the upload.",
        warning_fmt,
    )
    setup.set_row(31, 32)
    setup.set_row(32, 32)

    setup.merge_range(
        "A35:F36",
        "IMPORTANT: This workbook is the RHU working record and offline encoding aid. VaccTrack remains the official/final national SBI reporting source.",
        warning_fmt,
    )
    setup.set_row(34, 28)
    setup.set_row(35, 28)
    setup.protect(PROTECTION_PASSWORD)

    reference = workbook.add_worksheet("Reference")
    reference_headers = ["School ID", "School Name", "Barangay", "G1 Target", "G4 Female Target", "G7 Target"]
    for col, header in enumerate(reference_headers):
        reference.write(0, col, header, header_fmt)
    for row_idx, row in roster.iterrows():
        values = [row.get(header, "") for header in reference_headers]
        for col_idx, value in enumerate(values):
            reference.write(row_idx + 1, col_idx, value)
    reference.set_column("A:A", 16)
    reference.set_column("B:B", 36)
    reference.set_column("C:C", 24)
    reference.set_column("D:F", 14)
    reference.protect(PROTECTION_PASSWORD)
    reference.hide()
    last_ref_row = max(2, len(roster) + 1)
    workbook.define_name("School_IDs", f"=Reference!$A$2:$A${last_ref_row}")
    workbook.define_name("School_Names", f"=Reference!$B$2:$B${last_ref_row}")

    sheet = workbook.add_worksheet("Accomplishments")
    sheet.freeze_panes(1, 4)
    sheet.autofilter(0, 0, MAX_INPUT_ROWS, len(ALL_COLUMNS) - 1)
    for col, header in enumerate(ALL_COLUMNS):
        sheet.write(0, col, header, header_fmt)
    sheet.set_row(0, 42)
    sheet.set_column("A:A", 14)
    sheet.set_column("B:B", 34)
    sheet.set_column("C:C", 14)
    sheet.set_column("D:D", 12)
    sheet.set_column("E:E", 22)
    sheet.set_column("F:F", 13, None, {"hidden": True})
    sheet.set_column("G:T", 12)
    reason_start_col = BASE_COLUMNS.index("HPV2 Refused") + 1
    sheet.set_column(reason_start_col, reason_start_col + len(REASON_COLUMNS) - 1, 10)
    sheet.set_column(len(ALL_COLUMNS) - 1, len(ALL_COLUMNS) - 1, 14)

    school_name_col = BASE_COLUMNS.index("School Name")
    barangay_col = BASE_COLUMNS.index("Barangay")
    target_col = BASE_COLUMNS.index("Actual Target")
    grade_col = BASE_COLUMNS.index("Grade Level")
    school_id_col = BASE_COLUMNS.index("School ID")
    date_col = BASE_COLUMNS.index("Activity Date")
    row_check_col = len(ALL_COLUMNS) - 1

    for excel_row in range(2, MAX_INPUT_ROWS + 2):
        row = excel_row - 1
        school_name_cell = f"${_column_letter(school_name_col)}{excel_row}"
        school_id_cell = f"${_column_letter(school_id_col)}{excel_row}"
        grade_cell = f"${_column_letter(grade_col)}{excel_row}"
        sheet.write_blank(row, date_col, None, input_date_fmt)
        sheet.write_blank(row, school_name_col, None, input_fmt)
        sheet.write_formula(row, school_id_col, f'=IFERROR(INDEX(Reference!$A$2:$A${last_ref_row},MATCH({school_name_cell},Reference!$B$2:$B${last_ref_row},0)),"")', formula_fmt)
        sheet.write_formula(row, barangay_col, f'=IFERROR(INDEX(Reference!$C$2:$C${last_ref_row},MATCH({school_name_cell},Reference!$B$2:$B${last_ref_row},0)),"")', formula_fmt)
        sheet.write_blank(row, grade_col, None, input_fmt)
        sheet.write_formula(
            row,
            target_col,
            f'=IF({school_id_cell}="","",IF({grade_cell}="G1",IFERROR(VLOOKUP({school_id_cell},Reference!$A$2:$F${last_ref_row},4,FALSE),""),IF({grade_cell}="G4",IFERROR(VLOOKUP({school_id_cell},Reference!$A$2:$F${last_ref_row},5,FALSE),""),IF({grade_cell}="G7",IFERROR(VLOOKUP({school_id_cell},Reference!$A$2:$F${last_ref_row},6,FALSE),""),""))))',
            formula_fmt,
        )
        for col_name in INPUT_COUNT_COLUMNS:
            col = ALL_COLUMNS.index(col_name)
            sheet.write_blank(row, col, None, input_count_fmt)
        first_reason_letter = _column_letter(ALL_COLUMNS.index(REASON_COLUMNS[0]))
        last_reason_letter = _column_letter(ALL_COLUMNS.index(REASON_COLUMNS[-1]))
        grade_letter = _column_letter(grade_col)
        mr_start_letter = _column_letter(ALL_COLUMNS.index("MR Male"))
        td_refused_letter = _column_letter(ALL_COLUMNS.index("Td Refused"))
        hpv_start_letter = _column_letter(ALL_COLUMNS.index("HPV Dose 1"))
        hpv_refused_letter = _column_letter(ALL_COLUMNS.index("HPV2 Refused"))
        missed_parts = [
            f'${_column_letter(ALL_COLUMNS.index(name))}{excel_row}'
            for name in ["MR Deferred", "Td Deferred", "MR Refused", "Td Refused", "HPV1 Deferred", "HPV2 Deferred", "HPV1 Refused", "HPV2 Refused"]
        ]
        date_letter = _column_letter(date_col)
        school_name_letter = _column_letter(school_name_col)
        check_formula = (
            f'=IF(AND(${date_letter}{excel_row}="",${school_name_letter}{excel_row}="",${grade_letter}{excel_row}=""),"",'
            f'IF(OR(${date_letter}{excel_row}="",${school_name_letter}{excel_row}="",${grade_letter}{excel_row}=""),"CHECK KEY",'
            f'IF(AND(${grade_letter}{excel_row}="G4",SUM(${mr_start_letter}{excel_row}:${td_refused_letter}{excel_row})>0),"CHECK G4 FIELDS",'
            f'IF(AND(OR(${grade_letter}{excel_row}="G1",${grade_letter}{excel_row}="G7"),SUM(${hpv_start_letter}{excel_row}:${hpv_refused_letter}{excel_row})>0),"CHECK GRADE FIELDS",'
            f'IF(SUM(${first_reason_letter}{excel_row}:${last_reason_letter}{excel_row})>SUM({",".join(missed_parts)}),"CHECK REASONS","OK")))))'
        )
        sheet.write_formula(row, row_check_col, check_formula, helper_fmt)

    sheet.data_validation(1, date_col, MAX_INPUT_ROWS, date_col, {"validate": "date", "criteria": "between", "minimum": date(2026, 1, 1), "maximum": date(2027, 12, 31), "input_title": "Activity Date", "input_message": "Enter the date the school vaccination activity was conducted."})
    if not roster.empty:
        sheet.data_validation(1, school_name_col, MAX_INPUT_ROWS, school_name_col, {"validate": "list", "source": "=School_Names", "input_title": "School Name", "input_message": "Select a school assigned to this RHU. The School ID fills automatically."})
    sheet.data_validation(1, grade_col, MAX_INPUT_ROWS, grade_col, {"validate": "list", "source": ["G1", "G4", "G7"]})
    for col_name in INPUT_COUNT_COLUMNS:
        col = ALL_COLUMNS.index(col_name)
        sheet.data_validation(1, col, MAX_INPUT_ROWS, col, {"validate": "integer", "criteria": ">=", "value": 0, "error_title": "Invalid count", "error_message": "Enter a whole number of 0 or higher."})

    sheet.conditional_format(1, row_check_col, MAX_INPUT_ROWS, row_check_col, {"type": "text", "criteria": "containing", "value": "OK", "format": ok_fmt})
    sheet.conditional_format(1, row_check_col, MAX_INPUT_ROWS, row_check_col, {"type": "text", "criteria": "containing", "value": "CHECK", "format": bad_fmt})
    grade_letter = _column_letter(grade_col)
    sheet.conditional_format(1, ALL_COLUMNS.index("MR Male"), MAX_INPUT_ROWS, ALL_COLUMNS.index("Td Refused"), {"type": "formula", "criteria": f"=${grade_letter}2=\"G4\"", "format": workbook.add_format({"bg_color": "#E5E7EB", "font_color": "#9CA3AF"})})
    sheet.conditional_format(1, ALL_COLUMNS.index("HPV Dose 1"), MAX_INPUT_ROWS, ALL_COLUMNS.index("HPV2 Refused"), {"type": "formula", "criteria": f"=OR(${grade_letter}2=\"G1\",${grade_letter}2=\"G7\")", "format": workbook.add_format({"bg_color": "#E5E7EB", "font_color": "#9CA3AF"})})

    reason_comments = {f"Reason {code}": f"{code} {label}" for code, label in REASON_LABELS.items()}
    for header, comment in reason_comments.items():
        sheet.write_comment(0, ALL_COLUMNS.index(header), comment, {"author": "Abra NIP"})
    sheet.protect(PROTECTION_PASSWORD, {"autofilter": True})

    def add_vacctrack_sheet(name: str, grade: str) -> None:
        vac = workbook.add_worksheet(name)
        vac.hide_gridlines(2)
        vac.write("A1", f"{name} — values to encode in VaccTrack", title_fmt)
        vac.write("A2", "Report Date", section_fmt)
        vac.write_datetime("B2", datetime.now(MANILA_TZ).replace(tzinfo=None), vac_date_fmt)
        vac.data_validation("B2", {"validate": "date", "criteria": "between", "minimum": date(2026, 1, 1), "maximum": date(2027, 12, 31)})
        vac.merge_range("D2:J2", "Select the report date, then encode the displayed school values in the matching VaccTrack grade page. Location and facility details are already handled in VaccTrack and are intentionally omitted here.", subtitle_fmt)
        vac.set_row(1, 42)

        common = ["Activity?", "School"]
        reasons = [f"{code} {label}" for code, label in REASON_LABELS.items()]
        if grade == "G1":
            metrics = [
                "G1.A Number of Students vaccinated with MR (Male)",
                "G1.B Number of Students vaccinated with MR (Female)",
                "G1.C Number of Students vaccinated with TD (Male)",
                "G1.D Number of Students vaccinated with TD (Female)",
            ]
        elif grade == "G4":
            metrics = [
                "G4.B Number of Students Who Received the First Dose of the HPV Vaccine",
                "G4.C Number of Students Who Received the Second Dose of the HPV Vaccine",
                "G4.D Number of Students Deferred for the First Dose of the HPV Vaccine",
                "G4.E Number of Students Deferred for the Second Dose of the HPV Vaccine",
                "G4.F Number of Students Who Refused the First Dose of the HPV Vaccine",
                "G4.G Number of Students Who Refused the Second Dose of the HPV Vaccine",
            ]
        else:
            metrics = [
                "G7.A Number of Students vaccinated with MR (Male)",
                "G7.B Number of Students vaccinated with MR (Female)",
                "G7.C Number of Students vaccinated with TD (Male)",
                "G7.D Number of Students vaccinated with TD (Female)",
            ]

        headers = common + metrics + reasons
        for col, header in enumerate(headers):
            vac.write(3, col, header, header_fmt)
        vac.set_row(3, 72)
        # Keep only the header rows frozen. The encoder should be able to scroll freely across VaccTrack fields.
        vac.freeze_panes(4, 0)
        vac.autofilter(3, 0, 3 + len(roster), len(headers) - 1)
        vac.set_column(0, 0, 10)
        vac.set_column(1, 1, 34)
        vac.set_column(2, len(headers) - 1, 15)

        input_end = MAX_INPUT_ROWS + 1
        acc_grade_col = _column_letter(ALL_COLUMNS.index("Grade Level"))
        acc_date_col = _column_letter(ALL_COLUMNS.index("Activity Date"))
        acc_school_col = _column_letter(ALL_COLUMNS.index("School ID"))

        metric_map = {
            "G1": ["MR Male", "MR Female", "Td Male", "Td Female"],
            "G7": ["MR Male", "MR Female", "Td Male", "Td Female"],
            "G4": ["HPV Dose 1", "HPV Dose 2", "HPV1 Deferred", "HPV2 Deferred", "HPV1 Refused", "HPV2 Refused"],
        }

        for idx, school in roster.iterrows():
            excel_row = idx + 5
            school_id = str(school["School ID"]).replace('"', '""')
            school_name = str(school.get("School Name", ""))
            vac.write(idx + 4, 1, school_name)

            count_formula = f'=IF(COUNTIFS(Accomplishments!${acc_date_col}$2:${acc_date_col}${input_end},$B$2,Accomplishments!${acc_school_col}$2:${acc_school_col}${input_end},"{school_id}",Accomplishments!${acc_grade_col}$2:${acc_grade_col}${input_end},"{grade}")>0,"YES","")'
            vac.write_formula(idx + 4, 0, count_formula, helper_fmt)

            metric_start = len(common)
            metric_inputs = metric_map[grade]

            for offset, source_name in enumerate(metric_inputs):
                source_col = _column_letter(ALL_COLUMNS.index(source_name))
                formula = f'=SUMIFS(Accomplishments!${source_col}$2:${source_col}${input_end},Accomplishments!${acc_date_col}$2:${acc_date_col}${input_end},$B$2,Accomplishments!${acc_school_col}$2:${acc_school_col}${input_end},"{school_id}",Accomplishments!${acc_grade_col}$2:${acc_grade_col}${input_end},"{grade}")'
                vac.write_formula(idx + 4, metric_start + offset, formula, count_fmt)

            reason_start = metric_start + len(metrics)
            for r_offset, code in enumerate(REASON_LABELS):
                source_col = _column_letter(ALL_COLUMNS.index(f"Reason {code}"))
                formula = f'=SUMIFS(Accomplishments!${source_col}$2:${source_col}${input_end},Accomplishments!${acc_date_col}$2:${acc_date_col}${input_end},$B$2,Accomplishments!${acc_school_col}$2:${acc_school_col}${input_end},"{school_id}",Accomplishments!${acc_grade_col}$2:${acc_grade_col}${input_end},"{grade}")'
                vac.write_formula(idx + 4, reason_start + r_offset, formula, count_fmt)

        vac.conditional_format(4, 0, 3 + len(roster), 0, {"type": "text", "criteria": "containing", "value": "YES", "format": ok_fmt})
        vac.protect(PROTECTION_PASSWORD, {"autofilter": True})

    add_vacctrack_sheet("VaccTrack G1", "G1")
    add_vacctrack_sheet("VaccTrack G4", "G4")
    add_vacctrack_sheet("VaccTrack G7", "G7")

    workbook.close()
    output.seek(0)
    return output.getvalue()


def _read_workbook_identity(raw: bytes) -> tuple[str, str]:
    try:
        setup = pd.read_excel(BytesIO(raw), sheet_name="Setup", engine="calamine", header=None, dtype=object)
    except Exception as exc:
        raise ValueError(f"Unable to read the Setup sheet: {exc}") from exc
    municipality = str(setup.iloc[3, 1] if setup.shape[0] > 3 and setup.shape[1] > 1 else "").strip()
    version = str(setup.iloc[4, 4] if setup.shape[0] > 4 and setup.shape[1] > 4 else "").strip()
    return municipality, version


def _read_accomplishments(raw: bytes) -> pd.DataFrame:
    try:
        return pd.read_excel(BytesIO(raw), sheet_name="Accomplishments", engine="calamine", dtype=object)
    except Exception as exc:
        raise ValueError(f"Unable to read the Accomplishments sheet: {exc}") from exc


def _parse_date(value: object) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _normalize_grade(value: object) -> str:
    text = str(value or "").strip()
    return GRADE_TO_CODE.get(text, "")


def _number(value: object) -> int | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return 0
    try:
        number = float(value)
    except Exception:
        return None
    if number < 0 or number != int(number):
        return None
    return int(number)


def validate_workbook(raw: bytes, targets: pd.DataFrame, municipality: str) -> tuple[pd.DataFrame, list[str]]:
    frame = _read_accomplishments(raw)
    frame.columns = [str(column).strip() for column in frame.columns]
    required = ["Activity Date", "School ID", "Grade Level"] + INPUT_COUNT_COLUMNS
    missing = [column for column in required if column not in frame.columns]
    if missing:
        return pd.DataFrame(), ["Missing required column(s): " + ", ".join(missing)]

    roster = _roster(targets, municipality)
    roster_map = roster.set_index("School ID").to_dict("index") if not roster.empty else {}
    data = frame[required].copy()
    key_or_count = data[["Activity Date", "School ID", "Grade Level"] + INPUT_COUNT_COLUMNS].copy()
    nonblank = key_or_count.apply(lambda row: any(str(value).strip() not in {"", "nan", "NaT", "None"} for value in row), axis=1)
    data = data.loc[nonblank].copy()
    if data.empty:
        return pd.DataFrame(), ["The Accomplishments sheet has no activity rows to upload."]

    errors: list[str] = []
    records: list[dict] = []
    for index, row in data.iterrows():
        excel_row = int(index) + 2
        activity_date = _parse_date(row.get("Activity Date"))
        school_id = _clean_school_id(row.get("School ID"))
        grade = _normalize_grade(row.get("Grade Level"))
        if activity_date is None:
            errors.append(f"Row {excel_row}: Activity Date is missing or invalid.")
        if not school_id:
            errors.append(f"Row {excel_row}: School ID is required.")
        elif school_id not in roster_map:
            errors.append(f"Row {excel_row}: School ID {school_id} is not assigned to {municipality}.")
        if not grade:
            errors.append(f"Row {excel_row}: Grade Level must be G1, G4, or G7.")

        counts: dict[str, int] = {}
        invalid_count = False
        for column in INPUT_COUNT_COLUMNS:
            parsed = _number(row.get(column))
            if parsed is None:
                errors.append(f"Row {excel_row}: {column} must be a whole number of 0 or higher.")
                invalid_count = True
                parsed = 0
            counts[column] = parsed
        if invalid_count or activity_date is None or school_id not in roster_map or not grade:
            continue

        if grade == "G4" and sum(counts[column] for column in ["MR Male", "MR Female", "Td Male", "Td Female", "MR Deferred", "Td Deferred", "MR Refused", "Td Refused"]) > 0:
            errors.append(f"Row {excel_row}: G4 rows must use HPV fields, not MR/Td fields.")
            continue
        if grade in {"G1", "G7"} and sum(counts[column] for column in ["HPV Dose 1", "HPV Dose 2", "HPV1 Deferred", "HPV2 Deferred", "HPV1 Refused", "HPV2 Refused"]) > 0:
            errors.append(f"Row {excel_row}: G1/G7 rows must use MR/Td fields, not HPV fields.")
            continue

        total_missed = (
            counts["MR Deferred"] + counts["Td Deferred"] + counts["MR Refused"] + counts["Td Refused"]
            + counts["HPV1 Deferred"] + counts["HPV2 Deferred"] + counts["HPV1 Refused"] + counts["HPV2 Refused"]
        )
        reason_total = sum(counts[column] for column in REASON_COLUMNS)
        if reason_total > total_missed:
            errors.append(f"Row {excel_row}: reason-code counts cannot exceed total deferred/refused counts.")
            continue

        school = roster_map[school_id]
        record = {
            "activity_date": activity_date,
            "school_id": school_id,
            "school_name": str(school.get("School Name") or "").strip(),
            "barangay": str(school.get("Barangay") or "").strip(),
            "grade_level": grade,
        }
        record.update(counts)
        records.append(record)

    if errors:
        return pd.DataFrame(), errors

    result = pd.DataFrame(records)
    group_keys = ["activity_date", "school_id", "school_name", "barangay", "grade_level"]
    result = result.groupby(group_keys, as_index=False)[INPUT_COUNT_COLUMNS].sum()
    result = result.sort_values(["activity_date", "school_name", "grade_level"]).reset_index(drop=True)
    return result, []


def _to_db_record(row: pd.Series, municipality: str, username: str, batch_id: str) -> dict:
    grade = str(row["grade_level"])
    extra = {
        "mr_deferred": int(row["MR Deferred"]),
        "td_deferred": int(row["Td Deferred"]),
        "mr_refused": int(row["MR Refused"]),
        "td_refused": int(row["Td Refused"]),
        "hpv1_deferred": int(row["HPV1 Deferred"]),
        "hpv2_deferred": int(row["HPV2 Deferred"]),
        "hpv1_refused": int(row["HPV1 Refused"]),
        "hpv2_refused": int(row["HPV2 Refused"]),
        "reason_counts": {code: int(row[f"Reason {code}"]) for code in REASON_LABELS},
    }
    return {
        "municipality": canonical_municipality_name(municipality),
        "school_id": _clean_school_id(row["school_id"]),
        "school_name": str(row["school_name"]),
        "barangay": str(row["barangay"]),
        "activity_date": row["activity_date"].isoformat(),
        "grade_level": grade,
        "mr_male": int(row["MR Male"]) if grade in {"G1", "G7"} else None,
        "mr_female": int(row["MR Female"]) if grade in {"G1", "G7"} else None,
        "td_male": int(row["Td Male"]) if grade in {"G1", "G7"} else None,
        "td_female": int(row["Td Female"]) if grade in {"G1", "G7"} else None,
        "hpv_dose1": int(row["HPV Dose 1"]) if grade == "G4" else None,
        "hpv_dose2": int(row["HPV Dose 2"]) if grade == "G4" else None,
        "mr_deferred": extra["mr_deferred"],
        "td_deferred": extra["td_deferred"],
        "mr_refused": extra["mr_refused"],
        "td_refused": extra["td_refused"],
        "hpv1_deferred": extra["hpv1_deferred"],
        "hpv2_deferred": extra["hpv2_deferred"],
        "hpv1_refused": extra["hpv1_refused"],
        "hpv2_refused": extra["hpv2_refused"],
        "reason_counts": extra["reason_counts"],
        "updated_by": username,
        "updated_at": datetime.now(MANILA_TZ).isoformat(),
        "source_type": "workbook",
        # source_batch_id is the legacy learner-line-list bigint field.
        # Workbook hashes are stored in sbi_rhu_workbook_submissions.batch_id instead.
        "source_batch_id": None,
    }


def _current_entries(supabase, municipality: str) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    limit = 1000
    while True:
        response = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("municipality", canonical_municipality_name(municipality))
            .range(offset, offset + limit - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    frame = pd.DataFrame(rows)
    if not frame.empty and "activity_date" in frame.columns:
        frame["activity_date"] = pd.to_datetime(frame["activity_date"], errors="coerce").dt.date
        frame["school_id"] = frame["school_id"].map(_clean_school_id)
    return frame


def get_current_submission(supabase, municipality: str) -> dict:
    try:
        response = (
            supabase.table(SUBMISSION_TABLE)
            .select("*")
            .eq("municipality", canonical_municipality_name(municipality))
            .eq("is_current", True)
            .order("uploaded_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def fetch_submission_history(supabase, municipality: str | None = None) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    limit = 500
    while True:
        query = supabase.table(SUBMISSION_TABLE).select("*").order("uploaded_at", desc=True)
        if municipality:
            query = query.eq("municipality", canonical_municipality_name(municipality))
        response = query.range(offset, offset + limit - 1).execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return pd.DataFrame(rows)


def _snapshot_payload(incoming: pd.DataFrame) -> list[dict]:
    records: list[dict] = []
    for _, row in incoming.iterrows():
        record = {
            "activity_date": row["activity_date"].isoformat(),
            "school_id": _clean_school_id(row["school_id"]),
            "school_name": str(row.get("school_name") or ""),
            "barangay": str(row.get("barangay") or ""),
            "grade_level": str(row["grade_level"]),
        }
        for column in INPUT_COUNT_COLUMNS:
            record[column] = int(pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").fillna(0).iloc[0])
        records.append(record)
    return records


def _snapshot_to_frame(snapshot: object) -> pd.DataFrame:
    if not isinstance(snapshot, list) or not snapshot:
        return pd.DataFrame()
    frame = pd.DataFrame(snapshot)
    if frame.empty:
        return frame
    frame["activity_date"] = pd.to_datetime(frame["activity_date"], errors="coerce").dt.date
    frame["school_id"] = frame["school_id"].map(_clean_school_id)
    for column in INPUT_COUNT_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0).astype(int)
    return frame


def _record_submission(
    supabase,
    incoming: pd.DataFrame,
    municipality: str,
    username: str,
    batch_id: str,
    file_name: str,
    *,
    added: int,
    modified: int,
    removed: int,
    unchanged: int,
    restored_from_submission_id: int | None = None,
) -> int:
    canonical = canonical_municipality_name(municipality)
    supabase.table(SUBMISSION_TABLE).update({"is_current": False}).eq("municipality", canonical).eq("is_current", True).execute()
    dates = pd.to_datetime(incoming["activity_date"], errors="coerce") if not incoming.empty else pd.Series(dtype="datetime64[ns]")
    payload = {
        "municipality": canonical,
        "batch_id": batch_id,
        "file_name": file_name,
        "workbook_version": WORKBOOK_VERSION,
        "uploaded_by": username,
        "uploaded_at": datetime.now(MANILA_TZ).isoformat(),
        "row_count": int(len(incoming)),
        "activity_date_min": dates.min().date().isoformat() if not dates.empty and dates.notna().any() else None,
        "activity_date_max": dates.max().date().isoformat() if not dates.empty and dates.notna().any() else None,
        "added_count": int(added),
        "modified_count": int(modified),
        "removed_count": int(removed),
        "unchanged_count": int(unchanged),
        "snapshot": _snapshot_payload(incoming),
        "is_current": True,
        "is_finalized": False,
        "restored_from_submission_id": restored_from_submission_id,
    }
    response = supabase.table(SUBMISSION_TABLE).insert(payload).execute()
    rows = response.data or []
    return int(rows[0].get("id")) if rows and rows[0].get("id") is not None else 0


def finalize_current_submission(supabase, municipality: str, username: str) -> bool:
    current = get_current_submission(supabase, municipality)
    if not current or current.get("id") is None:
        return False
    supabase.table(SUBMISSION_TABLE).update(
        {
            "is_finalized": True,
            "finalized_at": datetime.now(MANILA_TZ).isoformat(),
            "finalized_by": username,
        }
    ).eq("id", int(current["id"])).execute()
    return True


def reopen_current_submission(supabase, municipality: str) -> bool:
    current = get_current_submission(supabase, municipality)
    if not current or current.get("id") is None:
        return False
    supabase.table(SUBMISSION_TABLE).update(
        {"is_finalized": False, "finalized_at": None, "finalized_by": None}
    ).eq("id", int(current["id"])).execute()
    return True


def restore_submission(supabase, submission_id: int, username: str) -> tuple[int, int]:
    response = supabase.table(SUBMISSION_TABLE).select("*").eq("id", int(submission_id)).limit(1).execute()
    rows = response.data or []
    if not rows:
        raise ValueError("The selected workbook submission could not be found.")
    source = rows[0]
    incoming = _snapshot_to_frame(source.get("snapshot"))
    if incoming.empty:
        raise ValueError("The selected workbook submission has no restorable snapshot.")
    municipality = canonical_municipality_name(source.get("municipality") or "")
    current = _current_entries(supabase, municipality)
    added, modified, removed, unchanged = _preview_changes(current, incoming)
    seed = f"restore|{submission_id}|{datetime.now(MANILA_TZ).isoformat()}".encode("utf-8")
    batch_id = hashlib.sha256(seed).hexdigest()
    saved, removed_count = _save_snapshot(supabase, incoming, municipality, username, batch_id)
    _record_submission(
        supabase,
        incoming,
        municipality,
        username,
        batch_id,
        f"RESTORED submission #{submission_id}",
        added=added,
        modified=modified,
        removed=removed,
        unchanged=unchanged,
        restored_from_submission_id=int(submission_id),
    )
    return saved, removed_count


def _preview_changes(current: pd.DataFrame, incoming: pd.DataFrame) -> tuple[int, int, int, int]:
    keys = ["activity_date", "school_id", "grade_level"]
    if current.empty:
        return len(incoming), 0, 0, 0
    current_keys = {tuple(row) for row in current[keys].itertuples(index=False, name=None)}
    incoming_keys = {tuple(row) for row in incoming[keys].itertuples(index=False, name=None)}
    added = len(incoming_keys - current_keys)
    removed = len(current_keys - incoming_keys)
    shared = current_keys & incoming_keys

    compare_map = {
        "MR Male": "mr_male",
        "MR Female": "mr_female",
        "Td Male": "td_male",
        "Td Female": "td_female",
        "HPV Dose 1": "hpv_dose1",
        "HPV Dose 2": "hpv_dose2",
    }
    current_indexed = current.set_index(keys)
    incoming_indexed = incoming.set_index(keys)
    modified = 0
    for key in shared:
        old = current_indexed.loc[key]
        new = incoming_indexed.loc[key]
        changed = False
        for incoming_col, db_col in compare_map.items():
            old_value = pd.to_numeric(pd.Series([old.get(db_col)]), errors="coerce").fillna(0).iloc[0]
            new_value = pd.to_numeric(pd.Series([new.get(incoming_col)]), errors="coerce").fillna(0).iloc[0]
            if int(old_value) != int(new_value):
                changed = True
                break
        if not changed:
            for field in ["MR Deferred", "Td Deferred", "MR Refused", "Td Refused", "HPV1 Deferred", "HPV2 Deferred", "HPV1 Refused", "HPV2 Refused"]:
                db_col = field.lower().replace(" ", "_")
                if field.startswith("HPV1"):
                    db_col = field.lower().replace("hpv1", "hpv1").replace(" ", "_")
                if field.startswith("HPV2"):
                    db_col = field.lower().replace("hpv2", "hpv2").replace(" ", "_")
                old_value = pd.to_numeric(pd.Series([old.get(db_col)]), errors="coerce").fillna(0).iloc[0]
                new_value = pd.to_numeric(pd.Series([new.get(field)]), errors="coerce").fillna(0).iloc[0]
                if int(old_value) != int(new_value):
                    changed = True
                    break
        if not changed:
            old_reasons = old.get("reason_counts") if isinstance(old.get("reason_counts"), dict) else {}
            for code in REASON_LABELS:
                old_value = pd.to_numeric(pd.Series([old_reasons.get(code)]), errors="coerce").fillna(0).iloc[0]
                new_value = pd.to_numeric(pd.Series([new.get(f"Reason {code}")]), errors="coerce").fillna(0).iloc[0]
                if int(old_value) != int(new_value):
                    changed = True
                    break
        if changed:
            modified += 1
    unchanged = len(shared) - modified
    return added, modified, removed, unchanged


def _save_snapshot(supabase, incoming: pd.DataFrame, municipality: str, username: str, batch_id: str) -> tuple[int, int]:
    current = _current_entries(supabase, municipality)
    records = [_to_db_record(row, municipality, username, batch_id) for _, row in incoming.iterrows()]
    for start in range(0, len(records), 300):
        supabase.table(TABLE_NAME).upsert(
            records[start:start + 300],
            on_conflict="municipality,school_id,activity_date,grade_level",
        ).execute()

    incoming_keys = {(row["activity_date"], _clean_school_id(row["school_id"]), str(row["grade_level"])) for _, row in incoming.iterrows()}
    stale_ids = []
    if not current.empty and "id" in current.columns:
        for _, row in current.iterrows():
            key = (row.get("activity_date"), _clean_school_id(row.get("school_id")), str(row.get("grade_level")))
            if key not in incoming_keys and row.get("id"):
                stale_ids.append(row["id"])
    for start in range(0, len(stale_ids), 200):
        supabase.table(TABLE_NAME).delete().in_("id", stale_ids[start:start + 200]).execute()
    return len(records), len(stale_ids)


def render_workbook_download(targets: pd.DataFrame, municipality: str) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-file-excel" style="color:#0033A0;margin-right:8px;"></i>Step 1 — Download & Maintain Your SBI Workbook</h3>',
        unsafe_allow_html=True,
    )
    st.markdown(
        "Download one RHU-specific workbook and keep using that same file throughout the SBI activity. "
        "It works offline and automatically prepares the daily VaccTrack values from your accomplishment entries."
    )
    workbook_bytes = build_offline_workbook(targets, municipality)
    st.download_button(
        "Download SBI Offline Accomplishment Workbook",
        data=workbook_bytes,
        file_name=f"SBI_Accomplishment_{municipality.replace(' ', '_')}_2026.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
        key="sbi_offline_workbook_download",
    )
    st.info(
        "Keep this workbook as your RHU working file. Encode all activity dates and schools in it. "
        "For corrections, edit the workbook itself and upload the complete current version again."
    )


def render_workbook_upload(supabase, targets: pd.DataFrame, municipality: str, username: str) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-cloud-arrow-up" style="color:#0033A0;margin-right:8px;"></i>Step 2 — Upload Current Workbook</h3>',
        unsafe_allow_html=True,
    )
    st.markdown(
        "Upload the workbook when internet is available. The upload is treated as your RHU's complete current dataset. "
        "If you corrected or removed an entry in Excel, that change will replace the previous dashboard value after confirmation."
    )
    if not workbook_schema_available(supabase):
        st.error("Offline workbook upload is temporarily unavailable. Please contact the NIP coordinator.")
        return

    campaign = get_campaign_config(supabase)
    current = get_current_submission(supabase, municipality)
    if current:
        uploaded_at = pd.to_datetime(current.get("uploaded_at"), errors="coerce")
        uploaded_label = uploaded_at.strftime("%b %d, %Y %I:%M %p") if not pd.isna(uploaded_at) else "Unknown time"
        status_label = "Finalized" if current.get("is_finalized") else "Current workbook uploaded"
        st.info(
            f"{status_label} • {int(current.get('row_count') or 0):,} record(s) • "
            f"last upload {uploaded_label}."
        )

    if current and current.get("is_finalized"):
        st.success("Your RHU submission is finalized. Ask the System Administrator to reopen it if a correction is required.")
        return

    campaign_status = str(campaign.get("status") or "Pre-Implementation")
    if campaign_status == "Closed":
        st.warning("SBI workbook uploads are closed by the System Administrator. You can still view your existing data and VaccTrack check.")
        return
    if campaign_status == "Post-Activity Correction":
        st.info(
            "Post-activity correction period: you may still upload corrections or late reports, but every Activity Date must remain within the official SBI activity date range. "
            "Do not use a later upload date as the Activity Date unless the vaccination activity actually happened on that date."
        )

    # Rotate the uploader key after a successful save so the selected workbook is
    # cleared on the next rerun. This makes a completed upload visually obvious
    # and reduces accidental repeat uploads of the same file.
    upload_reset = int(st.session_state.get("sbi_offline_workbook_upload_reset", 0))

    success_notice = st.session_state.pop("sbi_offline_workbook_success_notice", None)
    if success_notice:
        st.success(success_notice)

    uploaded = st.file_uploader(
        "Upload SBI Offline Accomplishment Workbook",
        type=["xlsx"],
        key=f"sbi_offline_workbook_upload_{upload_reset}",
        help="Use the workbook downloaded from Step 1. Do not upload VaccTrack exports here.",
    )
    if uploaded is not None:
        raw = uploaded.getvalue()
        try:
            workbook_muni, workbook_version = _read_workbook_identity(raw)
        except ValueError as exc:
            st.error(str(exc))
            return
        if normalize_municipality_key(workbook_muni) != normalize_municipality_key(municipality):
            st.error(f"This workbook belongs to {workbook_muni or 'another RHU'}, not {municipality}.")
            return
        if workbook_version != WORKBOOK_VERSION:
            st.error(
                f"This workbook uses an unsupported template version ({workbook_version or 'unknown'}). "
                "Download a fresh SBI Offline Accomplishment Workbook from Step 1, then transfer the current accomplishment rows into it."
            )
            return

        incoming, errors = validate_workbook(raw, targets, municipality)
        errors.extend(activity_date_issues(incoming, campaign))
        if errors:
            st.error("The workbook needs correction before it can be uploaded.")
            st.dataframe(pd.DataFrame({"Problem": errors[:100]}), width="stretch", hide_index=True)
            return

        current_entries = _current_entries(supabase, municipality)
        added, modified, removed, unchanged = _preview_changes(current_entries, incoming)
        st.success(f"Workbook validated: {len(incoming):,} Date + School + Grade records.")
        a, b, c, d = st.columns(4)
        a.metric("Added", added)
        b.metric("Modified", modified)
        c.metric("Removed", removed)
        d.metric("Unchanged", unchanged)

        preview = incoming[["activity_date", "school_name", "grade_level", "MR Male", "MR Female", "Td Male", "Td Female", "HPV Dose 1", "HPV Dose 2"]].copy()
        preview.columns = ["Activity Date", "School", "Grade", "MR Male", "MR Female", "Td Male", "Td Female", "HPV Dose 1", "HPV Dose 2"]
        with st.expander("Review validated records", expanded=False):
            st.dataframe(preview, width="stretch", hide_index=True)

        if removed:
            st.warning(f"{removed} existing dashboard record(s) are not present in this workbook and will be removed after confirmation.")
        confirm = st.checkbox(
            "I confirm that this workbook contains the complete current SBI accomplishment data for our RHU.",
            key=f"sbi_offline_workbook_confirm_{upload_reset}",
        )
        if st.button(
            "Use This Workbook as Current RHU Data",
            type="primary",
            disabled=not confirm,
            width="stretch",
            key=f"sbi_offline_workbook_save_{upload_reset}",
        ):
            batch_id = hashlib.sha256(raw).hexdigest()
            try:
                saved, removed_count = _save_snapshot(supabase, incoming, municipality, username, batch_id)
                _record_submission(
                    supabase,
                    incoming,
                    municipality,
                    username,
                    batch_id,
                    uploaded.name,
                    added=added,
                    modified=modified,
                    removed=removed,
                    unchanged=unchanged,
                )
            except Exception as exc:
                st.error(f"The workbook could not be saved: {exc}")
                return
            st.cache_data.clear()
            st.session_state["sbi_offline_workbook_success_notice"] = (
                f"Workbook uploaded successfully. Current RHU data now contains {saved:,} record(s); "
                f"{removed_count:,} old record(s) were removed. The upload field has been cleared."
            )
            st.session_state["sbi_offline_workbook_upload_reset"] = upload_reset + 1
            st.toast("SBI workbook uploaded successfully.")
            st.rerun()

    current = get_current_submission(supabase, municipality)
    finalization_status = str(campaign.get("status") or "Pre-Implementation")
    if current and not current.get("is_finalized") and finalization_status in {"Live", "Post-Activity Correction"}:
        st.divider()
        expander_label = (
            "When corrections are complete — Submit Final RHU Report"
            if finalization_status == "Post-Activity Correction"
            else "End of SBI only — Submit Final RHU Report"
        )
        with st.expander(expander_label, expanded=False):
            if finalization_status == "Post-Activity Correction":
                st.warning(
                    "Submit this only after all late corrections are complete. This is NOT required after each corrected workbook upload."
                )
            else:
                st.warning(
                    "This is NOT required after each workbook upload. Keep updating and re-uploading your workbook throughout SBI. "
                    "Use this only when your RHU has finished the activity and no more routine updates are expected."
                )
            st.caption(
                "Submitting the final RHU report locks further workbook uploads. If a correction is needed later, "
                "the System Administrator must reopen the RHU submission first."
            )
            final_confirm = st.checkbox(
                "I confirm that our RHU has finished SBI reporting for the activity and this is our final workbook.",
                key="sbi_finalize_confirm",
            )
            if st.button(
                "Submit Final RHU Report & Lock Further Uploads",
                disabled=not final_confirm,
                width="stretch",
                key="sbi_finalize_button",
            ):
                try:
                    finalized = finalize_current_submission(supabase, municipality, username)
                except Exception as exc:
                    st.error(f"The final RHU report could not be submitted: {exc}")
                    return
                if finalized:
                    st.cache_data.clear()
                    st.success("Final RHU report submitted. Further workbook uploads are now locked.")
                    st.rerun()

