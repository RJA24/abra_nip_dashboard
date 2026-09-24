from __future__ import annotations

from datetime import date, datetime
from html.parser import HTMLParser
from io import BytesIO, StringIO
import hashlib
import re
from typing import Callable

import pandas as pd
import streamlit as st

from core.data import fetch_sbi_vacctrack


IMPORT_TABLE = "sbi_vacctrack_imports"
ROWS_TABLE = "sbi_vacctrack_rows"
VALID_GRADES = ("G1", "G4", "G7")
GRADE_LABELS = {"G1": "Grade 1", "G4": "Grade 4", "G7": "Grade 7"}

GRADE_REQUIRED_COLUMNS = {
    "G1": [
        "G1.A Number of Students vaccinated with MR (Male)",
        "G1.B Number of Students vaccinated with MR (Female)",
        "G1.C Number of Students vaccinated with TD (Male)",
        "G1.D Number of Students vaccinated with TD (Female)",
    ],
    "G4": [
        "G4.A Actual total number Grade 4 (Female) Students",
        "G4.B Number of Students Who Received the First Dose of the HPV Vaccine",
        "G4.C Number of Students Who Received the Second Dose of the HPV Vaccine",
    ],
    "G7": [
        "G7.A Number of Students vaccinated with MR (Male)",
        "G7.B Number of Students vaccinated with MR (Female)",
        "G7.C Number of Students vaccinated with TD (Male)",
        "G7.D Number of Students vaccinated with TD (Female)",
    ],
}

COMMON_REQUIRED_COLUMNS = [
    "Region Name",
    "Province Name",
    "City/Municipality Name",
    "Barangay Name",
    "Facility Name",
    "Report date",
    "School id",
    "School name",
]


class _VaccTrackHTMLTableParser(HTMLParser):
    """Small dependency-free reader for VaccTrack's HTML-disguised .xls export."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._capture = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            self._capture = True
        elif tag == "br" and self._capture and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._capture and self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None:
            value = _normalize_text("".join(self._cell))
            self._row.append(value)
            self._cell = None
            self._capture = False
        elif tag == "tr" and self._row is not None:
            if self._table is not None and any(str(value).strip() for value in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _normalize_text(value) -> str:  # noqa: ANN001
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _unwrap_excel_text_formula(value) -> str:  # noqa: ANN001
    text = _normalize_text(value)
    match = re.fullmatch(r'=\"(.*)\"', text)
    return _normalize_text(match.group(1)) if match else text


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    output: list[str] = []
    for raw in headers:
        base = _normalize_text(raw) or "Unnamed"
        count = seen.get(base, 0)
        output.append(base if count == 0 else f"{base}.{count}")
        seen[base] = count + 1
    return output


def _read_html_xls(data: bytes) -> pd.DataFrame:
    text = data.decode("utf-8-sig", errors="replace")
    parser = _VaccTrackHTMLTableParser()
    parser.feed(text)

    candidate = None
    for table in parser.tables:
        if not table:
            continue
        first = {_normalize_text(value) for value in table[0]}
        if "Region Name" in first and "Report date" in first:
            candidate = table
            break
    if candidate is None:
        raise ValueError("No VaccTrack data table was found in this .xls export.")

    headers = _dedupe_headers(candidate[0])
    rows: list[list[str]] = []
    for raw_row in candidate[1:]:
        row = list(raw_row[: len(headers)])
        if len(row) < len(headers):
            row.extend([""] * (len(headers) - len(row)))
        rows.append([_unwrap_excel_text_formula(value) for value in row])

    return pd.DataFrame(rows, columns=headers)


def _read_csv(data: bytes) -> pd.DataFrame:
    # pandas handles quoted commas and duplicate headings reliably. Keep everything
    # as text because the dashboard's existing analytics layer owns type conversion.
    df = pd.read_csv(
        BytesIO(data),
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )
    return df


def _read_excel(data: bytes) -> pd.DataFrame:
    # v5.14 already requires python-calamine, so no additional dependency is needed.
    return pd.read_excel(BytesIO(data), engine="calamine", dtype=str).fillna("")


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    out.columns = _dedupe_headers([_normalize_text(col) for col in out.columns])

    # VaccTrack's Grade 7 CSV currently contains a duplicate final "Facility Name"
    # heading even though that column stores the updated date. pandas / our header
    # normalizer makes the duplicate "Facility Name.1". Match the historical Google
    # Sheet behavior expected by core.data / analytics.
    if "Facility Name.1" in out.columns and "Updated date" not in out.columns:
        out = out.rename(columns={"Facility Name.1": "Updated date"})

    for col in out.columns:
        out[col] = out[col].map(_unwrap_excel_text_formula)

    # Remove fully blank export rows but do not drop rows merely because a vaccine
    # count is blank. Older VaccTrack forms can legitimately leave some fields blank.
    nonblank = out.apply(lambda row: any(_normalize_text(v) for v in row), axis=1)
    out = out.loc[nonblank].reset_index(drop=True)
    return out


def _detect_grade(df: pd.DataFrame) -> str:
    columns = set(df.columns)
    matches = [
        grade
        for grade, required in GRADE_REQUIRED_COLUMNS.items()
        if all(column in columns for column in required)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError("The file matches more than one VaccTrack grade format.")

    prefixes = {
        "G1": any(str(col).startswith("G1.") for col in df.columns),
        "G4": any(str(col).startswith("G4.") for col in df.columns),
        "G7": any(str(col).startswith("G7.") for col in df.columns),
    }
    possible = [grade for grade, present in prefixes.items() if present]
    if len(possible) == 1:
        missing = [
            col for col in GRADE_REQUIRED_COLUMNS[possible[0]] if col not in columns
        ]
        raise ValueError(
            f"The file looks like {GRADE_LABELS[possible[0]]}, but required columns are missing: "
            + ", ".join(missing)
        )
    raise ValueError("Unable to identify this file as a Grade 1, Grade 4, or Grade 7 VaccTrack export.")


def _canonicalize_common_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize known header spacing variants without changing VaccTrack labels."""
    out = df.copy()
    rename = {}
    for col in out.columns:
        normalized = _normalize_text(col)
        if normalized != col:
            rename[col] = normalized
    if rename:
        out = out.rename(columns=rename)
    return out


def _parse_report_dates(series: pd.Series) -> pd.Series:
    # VaccTrack currently exports m/d/yy. Use an explicit two-pass parser to avoid
    # locale ambiguity while still accepting older m/d/YYYY rows.
    text = series.astype(str).str.strip()
    parsed = pd.to_datetime(text, format="%m/%d/%y", errors="coerce")
    missing = parsed.isna() & text.ne("")
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            text.loc[missing], format="%m/%d/%Y", errors="coerce"
        )
    return parsed


def _province_series(df: pd.DataFrame) -> pd.Series:
    if "Province Name" not in df.columns:
        return pd.Series("", index=df.index, dtype="string")
    return df["Province Name"].astype(str).str.strip().str.upper()


def _dataframe_record_payload(df: pd.DataFrame) -> list[dict]:
    cleaned = df.fillna("").copy()
    payload: list[dict] = []
    for record in cleaned.to_dict(orient="records"):
        payload.append({str(k): _normalize_text(v) for k, v in record.items()})
    return payload


def parse_vacctrack_upload(uploaded_file) -> dict:  # noqa: ANN001
    filename = str(getattr(uploaded_file, "name", "VaccTrack export")).strip() or "VaccTrack export"
    data = uploaded_file.getvalue()
    if not data:
        raise ValueError("The uploaded file is empty.")

    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    looks_html = data.lstrip(b"\xef\xbb\xbf \r\n\t").lower().startswith((b"<html", b"<!doctype html"))

    if suffix == "csv":
        df = _read_csv(data)
        source_format = "CSV"
    elif suffix == "xls" and looks_html:
        df = _read_html_xls(data)
        source_format = "VaccTrack HTML/XLS"
    elif suffix in {"xls", "xlsx"}:
        try:
            df = _read_excel(data)
        except Exception as exc:
            if suffix == "xls":
                raise ValueError(
                    "This .xls file could not be read. VaccTrack's normal HTML/XLS export is supported, "
                    "as are standard Excel files readable by python-calamine."
                ) from exc
            raise
        source_format = "Excel"
    else:
        raise ValueError("Upload a VaccTrack .xls, .xlsx, or .csv export.")

    df = _canonicalize_common_headers(_normalize_dataframe(df))
    if df.empty:
        raise ValueError("No VaccTrack records were found in the uploaded file.")

    # The user's Grade 7 CSV has a trailing space in the Province header. Header
    # normalization above intentionally turns it into the canonical name.
    if "Province Name" not in df.columns:
        province_variant = next(
            (col for col in df.columns if _normalize_text(col).lower() == "province name"),
            None,
        )
        if province_variant:
            df = df.rename(columns={province_variant: "Province Name"})

    missing_common = [col for col in COMMON_REQUIRED_COLUMNS if col not in df.columns]
    if missing_common:
        raise ValueError("Required VaccTrack columns are missing: " + ", ".join(missing_common))

    grade = _detect_grade(df)
    report_dates = _parse_report_dates(df["Report date"])
    valid_dates = report_dates.dropna()
    province = _province_series(df)
    abra_mask = province.eq("ABRA")
    abra_rows = int(abra_mask.sum())
    if abra_rows == 0:
        raise ValueError("This export contains no rows where Province Name is ABRA.")

    # Keep the cleaned original report-date text in row_data. Parsed dates are only
    # used for validation/freshness metadata.
    file_hash = hashlib.sha256(data).hexdigest()
    sample_cols = [
        col
        for col in [
            "Province Name",
            "City/Municipality Name",
            "Report date",
            "School id",
            "School name",
            *GRADE_REQUIRED_COLUMNS[grade],
        ]
        if col in df.columns
    ]

    return {
        "filename": filename,
        "source_format": source_format,
        "grade": grade,
        "grade_label": GRADE_LABELS[grade],
        "file_sha256": file_hash,
        "row_count": int(len(df)),
        "abra_row_count": abra_rows,
        "report_date_min": valid_dates.min().date() if not valid_dates.empty else None,
        "report_date_max": valid_dates.max().date() if not valid_dates.empty else None,
        "invalid_report_date_rows": int(report_dates.isna().sum()),
        "columns": list(df.columns),
        "df": df,
        "records": _dataframe_record_payload(df),
        "preview": df.loc[abra_mask, sample_cols].head(8).copy(),
    }


def schema_available(supabase) -> tuple[bool, str]:  # noqa: ANN001
    try:
        supabase.table(IMPORT_TABLE).select("id").limit(1).execute()
        supabase.table(ROWS_TABLE).select("id").limit(1).execute()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _latest_complete_import(supabase, grade: str) -> dict | None:  # noqa: ANN001
    response = (
        supabase.table(IMPORT_TABLE)
        .select("id,grade_level,filename,file_sha256,row_count,abra_row_count,report_date_min,report_date_max,imported_by,imported_at,completed_at,status")
        .eq("grade_level", grade)
        .eq("status", "Complete")
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )
    return (response.data or [None])[0]


def _already_imported(supabase, grade: str, file_hash: str) -> dict | None:  # noqa: ANN001
    response = (
        supabase.table(IMPORT_TABLE)
        .select("id,filename,completed_at,report_date_max")
        .eq("grade_level", grade)
        .eq("file_sha256", file_hash)
        .eq("status", "Complete")
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )
    return (response.data or [None])[0]


def import_vacctrack_snapshot(supabase, parsed: dict, imported_by: str) -> dict:  # noqa: ANN001
    grade = parsed["grade"]
    duplicate = _already_imported(supabase, grade, parsed["file_sha256"])
    if duplicate:
        raise ValueError(
            f"This exact {GRADE_LABELS[grade]} export was already imported "
            f"({duplicate.get('filename') or 'previous file'})."
        )

    meta = {
        "grade_level": grade,
        "filename": parsed["filename"],
        "file_sha256": parsed["file_sha256"],
        "source_format": parsed["source_format"],
        "row_count": parsed["row_count"],
        "abra_row_count": parsed["abra_row_count"],
        "report_date_min": parsed["report_date_min"].isoformat() if parsed["report_date_min"] else None,
        "report_date_max": parsed["report_date_max"].isoformat() if parsed["report_date_max"] else None,
        "invalid_report_date_rows": parsed["invalid_report_date_rows"],
        "imported_by": imported_by,
        "status": "Importing",
    }
    response = supabase.table(IMPORT_TABLE).insert(meta).execute()
    if not response.data:
        raise RuntimeError("Supabase did not return the new VaccTrack import record.")
    import_id = response.data[0]["id"]

    try:
        records = parsed["records"]
        batch_size = 200
        for start in range(0, len(records), batch_size):
            chunk = [
                {
                    "import_id": import_id,
                    "grade_level": grade,
                    "row_number": index + 1,
                    "row_data": records[index],
                }
                for index in range(start, min(start + batch_size, len(records)))
            ]
            supabase.table(ROWS_TABLE).insert(chunk).execute()

        completed_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        supabase.table(IMPORT_TABLE).update(
            {"status": "Complete", "completed_at": completed_at, "error_message": None}
        ).eq("id", import_id).execute()
        return {
            "id": import_id,
            "grade": grade,
            "rows": parsed["row_count"],
            "abra_rows": parsed["abra_row_count"],
            "report_date_max": parsed["report_date_max"],
        }
    except Exception as exc:
        try:
            supabase.table(IMPORT_TABLE).update(
                {"status": "Failed", "error_message": str(exc)[:1500]}
            ).eq("id", import_id).execute()
        except Exception:
            pass
        raise


def _history_dataframe(supabase, limit: int = 30) -> pd.DataFrame:  # noqa: ANN001
    response = (
        supabase.table(IMPORT_TABLE)
        .select("grade_level,filename,source_format,row_count,abra_row_count,report_date_min,report_date_max,invalid_report_date_rows,imported_by,imported_at,completed_at,status,error_message")
        .order("imported_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = response.data or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.rename(
        columns={
            "grade_level": "Grade",
            "filename": "Filename",
            "source_format": "Format",
            "row_count": "Rows",
            "abra_row_count": "Abra Rows",
            "report_date_min": "Earliest Report Date",
            "report_date_max": "Latest Report Date",
            "invalid_report_date_rows": "Invalid Dates",
            "imported_by": "Imported By",
            "imported_at": "Imported At",
            "completed_at": "Completed At",
            "status": "Status",
            "error_message": "Error",
        }
    )


def _max_report_date_from_frame(df: pd.DataFrame) -> date | None:
    if df is None or df.empty or "Report date" not in df.columns:
        return None
    dates = pd.to_datetime(df["Report date"], errors="coerce").dropna()
    return dates.max().date() if not dates.empty else None


def _current_dashboard_dates() -> dict[str, date | None]:
    try:
        g1, g4, g7 = fetch_sbi_vacctrack()
        return {
            "G1": _max_report_date_from_frame(g1),
            "G4": _max_report_date_from_frame(g4),
            "G7": _max_report_date_from_frame(g7),
        }
    except Exception:
        return {grade: None for grade in VALID_GRADES}


def render_vacctrack_importer(
    supabase,
    audit_callback: Callable[[object, str], None] | None = None,
) -> None:  # noqa: ANN001
    st.markdown(
        '''<div style="display:flex;align-items:center;gap:0.55rem;margin:1.45rem 0 0.7rem 0;">
        <i class="fa-solid fa-file-arrow-up" style="color:#0033A0;font-size:1.25rem;"></i>
        <div style="font-size:1.32rem;font-weight:750;color:#1e293b;">SBI VaccTrack Extract Import</div>
        </div>''',
        unsafe_allow_html=True,
    )
    st.markdown(
        "Upload the files downloaded directly from VaccTrack. The dashboard stores each grade as an official "
        "snapshot in Supabase, so you no longer need to copy-paste the export into VaccTrackG1, VaccTrackG4, "
        "or VaccTrackG7 manually. Google Sheets remains a fallback only when no direct snapshot has been imported for a grade."
    )

    ready, message = schema_available(supabase)
    if not ready:
        st.error(
            "VaccTrack direct import is not initialized. Run supabase/005_sbi_vacctrack_imports.sql once, then reload this page."
        )
        if message:
            with st.expander("Technical detail", expanded=False):
                st.code(message)
        return

    # Use a rotating uploader key so a successful import clears the selected files
    # on the next rerun. Otherwise Streamlit keeps the same files selected and the
    # duplicate-file guard appears immediately after a successful import, which is
    # technically correct but confusing to users.
    upload_reset = int(st.session_state.get("admin_vacctrack_upload_reset", 0))

    success_notice = st.session_state.pop("admin_vacctrack_import_success_notice", None)
    if success_notice:
        st.success(success_notice)

    uploaded_files = st.file_uploader(
        "Upload VaccTrack extracts",
        type=["xls", "xlsx", "csv"],
        accept_multiple_files=True,
        help="You may upload Grade 1, Grade 4, and Grade 7 together. The grade is detected from the file columns, not from the filename.",
        key=f"admin_vacctrack_extract_uploads_{upload_reset}",
    )

    parsed_files: list[dict] = []
    parse_errors: list[str] = []
    if uploaded_files:
        for uploaded in uploaded_files:
            try:
                parsed_files.append(parse_vacctrack_upload(uploaded))
            except Exception as exc:
                parse_errors.append(f"{uploaded.name}: {exc}")

    if parse_errors:
        st.error("Some files could not be validated:\n\n" + "\n".join(f"- {item}" for item in parse_errors))

    if parsed_files:
        grades = [item["grade"] for item in parsed_files]
        duplicates = sorted({grade for grade in grades if grades.count(grade) > 1})
        if duplicates:
            st.error(
                "Upload only one file for each grade in a single import. Duplicate grade files detected: "
                + ", ".join(duplicates)
            )

        current_dates = _current_dashboard_dates()
        preview_rows = []
        downgrade_grades: list[str] = []
        exact_duplicates: list[str] = []

        for item in parsed_files:
            grade = item["grade"]
            current_date = current_dates.get(grade)
            latest_import = _latest_complete_import(supabase, grade)
            latest_import_date = None
            if latest_import and latest_import.get("report_date_max"):
                latest_import_date = pd.to_datetime(
                    latest_import.get("report_date_max"), errors="coerce"
                )
                latest_import_date = (
                    latest_import_date.date() if pd.notna(latest_import_date) else None
                )
            comparison_date = max(
                [d for d in [current_date, latest_import_date] if d is not None],
                default=None,
            )
            if (
                comparison_date
                and item["report_date_max"]
                and item["report_date_max"] < comparison_date
            ):
                downgrade_grades.append(grade)

            try:
                duplicate = _already_imported(supabase, grade, item["file_sha256"])
            except Exception:
                duplicate = None
            if duplicate:
                exact_duplicates.append(grade)

            preview_rows.append(
                {
                    "Grade": grade,
                    "File": item["filename"],
                    "Format": item["source_format"],
                    "Rows": item["row_count"],
                    "Abra Rows": item["abra_row_count"],
                    "Earliest Report Date": item["report_date_min"],
                    "Latest Report Date": item["report_date_max"],
                    "Current Dashboard Latest": comparison_date,
                    "Invalid Dates": item["invalid_report_date_rows"],
                }
            )

        st.markdown("#### Validation Preview")
        st.dataframe(pd.DataFrame(preview_rows), width="stretch", hide_index=True)

        for item in parsed_files:
            with st.expander(f"Preview {item['grade']} — {item['filename']}", expanded=False):
                st.dataframe(item["preview"], width="stretch", hide_index=True)

        if exact_duplicates:
            st.info(
                "Already up to date for: "
                + ", ".join(sorted(set(exact_duplicates)))
                + ". This exact extract is already saved, so no import is needed. "
                  "Upload a newer VaccTrack extract when new data becomes available."
            )

        if downgrade_grades:
            st.error(
                "Import blocked because the uploaded extract is older than the data currently available to the dashboard for: "
                + ", ".join(sorted(set(downgrade_grades)))
                + ". Download a current VaccTrack extract instead."
            )

        can_import = (
            not parse_errors
            and not duplicates
            and not exact_duplicates
            and not downgrade_grades
            and bool(parsed_files)
        )

        confirm = st.checkbox(
            "I confirm that these are the latest complete VaccTrack extracts for the selected grade(s).",
            value=False,
            key=f"admin_vacctrack_confirm_{upload_reset}",
        )
        if st.button(
            "Import Validated VaccTrack Extracts",
            type="primary",
            width="stretch",
            disabled=not (can_import and confirm),
            key="admin_vacctrack_import_button",
        ):
            imported_by = str(
                st.session_state.get("username")
                or st.session_state.get("user_name")
                or "System Admin"
            )
            successes: list[str] = []
            failures: list[str] = []
            progress = st.progress(0.0, text="Importing VaccTrack extracts...")
            total = max(len(parsed_files), 1)
            for idx, item in enumerate(parsed_files, start=1):
                try:
                    result = import_vacctrack_snapshot(supabase, item, imported_by)
                    successes.append(
                        f"{result['grade']}: {result['rows']:,} rows ({result['abra_rows']:,} Abra rows)"
                    )
                    if audit_callback:
                        audit_callback(
                            supabase,
                            "VaccTrack import complete | "
                            f"grade={result['grade']} | rows={result['rows']} | "
                            f"abra_rows={result['abra_rows']} | filename={item['filename']} | "
                            f"latest_report_date={item['report_date_max']}",
                        )
                except Exception as exc:
                    failures.append(f"{item['grade']} ({item['filename']}): {exc}")
                    if audit_callback:
                        audit_callback(
                            supabase,
                            f"VaccTrack import failed | grade={item['grade']} | filename={item['filename']} | {type(exc).__name__}",
                        )
                progress.progress(idx / total, text=f"Processed {idx} of {total} file(s)")

            st.cache_data.clear()
            if successes:
                st.success("Imported:\n\n" + "\n".join(f"- {item}" for item in successes))
            if failures:
                st.error("Failed:\n\n" + "\n".join(f"- {item}" for item in failures))
            if successes and not failures:
                imported_grades = ", ".join(sorted({item.split(":", 1)[0] for item in successes}))
                st.session_state["admin_vacctrack_import_success_notice"] = (
                    f"VaccTrack import complete for {imported_grades}. The uploaded files were cleared; "
                    "the dashboard will now use the saved snapshots."
                )
                st.session_state["admin_vacctrack_upload_reset"] = upload_reset + 1
                st.toast("VaccTrack snapshots imported successfully.")
                st.rerun()

    st.divider()
    st.markdown("#### VaccTrack Import History")
    history = _history_dataframe(supabase, 30)
    if history.empty:
        st.write("No direct VaccTrack extracts have been imported yet. The dashboard continues using the Google Sheet fallback.")
    else:
        display_cols = [
            "Grade",
            "Filename",
            "Rows",
            "Abra Rows",
            "Latest Report Date",
            "Imported By",
            "Imported At",
            "Status",
        ]
        st.dataframe(
            history[[col for col in display_cols if col in history.columns]],
            width="stretch",
            hide_index=True,
        )
