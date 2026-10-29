"""Grade 5 HPV catch-up dashboard sourced from RHU aggregate workbooks.

Grade 5 is intentionally kept separate from the official VaccTrack G1/G4/G7
analytics.  The denominator is the RHU-entered ``Unvaccinated G5 Female``
column from the Actual Targets worksheet; accomplishments come only from G5
rows in the offline RHU workbook.
"""

from __future__ import annotations

from datetime import date
from html import escape

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.config import ABRA_MUNIS
from core.map_labels import canonical_municipality_name, normalize_municipality_key
from programs.sbi.aggregate_workbook import REASON_LABELS
from programs.sbi.png_exports import ultra_png_export_config
from programs.sbi.reporting import render_daily_trend, render_municipality_choropleth
from programs.sbi.workbook_dashboard import prepare_workbook_entries


def _clean_school_id(value: object) -> str:
    text = str(value or "").strip()
    return text[:-2] if text.endswith(".0") else text


def _safe_pct(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) * 100.0 if float(denominator or 0) > 0 else np.nan


def _prepare_g5_targets(actual_targets: pd.DataFrame, municipality: str | None) -> tuple[pd.DataFrame, int]:
    columns = [
        "Municipality", "Barangay", "School ID", "School Name",
        "Unvaccinated G5 Female", "G5 Target Entry Status",
    ]
    if actual_targets is None or actual_targets.empty:
        return pd.DataFrame(columns=["Municipality", "Barangay", "School ID", "School Name", "G5 Target"]), 0

    work = actual_targets.copy()
    for col in columns:
        if col not in work.columns:
            work[col] = pd.NA if col == "Unvaccinated G5 Female" else ""

    work["Municipality"] = work["Municipality"].map(lambda v: canonical_municipality_name(str(v or "").strip()))
    work["School ID"] = work["School ID"].map(_clean_school_id)
    work["School Name"] = work["School Name"].astype("string").fillna("").str.strip()
    work["Barangay"] = work["Barangay"].astype("string").fillna("").str.strip()

    if municipality:
        key = normalize_municipality_key(municipality)
        work = work.loc[work["Municipality"].map(normalize_municipality_key).eq(key)].copy()

    status = work["G5 Target Entry Status"].astype(str).str.casefold().str.strip()
    complete = status.eq("complete")
    # Backward-compatible fallback if the status helper is absent in an older frame.
    if not complete.any() and "Unvaccinated G5 Female" in work.columns:
        complete = pd.to_numeric(work["Unvaccinated G5 Female"], errors="coerce").notna()

    pending_count = int((~complete).sum())
    work = work.loc[complete].copy()
    work["G5 Target"] = pd.to_numeric(work["Unvaccinated G5 Female"], errors="coerce")
    work = work.loc[work["G5 Target"].notna() & work["School ID"].ne("")].copy()

    if work.empty:
        return pd.DataFrame(columns=["Municipality", "Barangay", "School ID", "School Name", "G5 Target"]), pending_count

    grouped = (
        work.groupby(["Municipality", "Barangay", "School ID", "School Name"], dropna=False, as_index=False)["G5 Target"]
        .sum(min_count=1)
        .sort_values(["Municipality", "School Name"])
        .reset_index(drop=True)
    )
    return grouped, pending_count


def _targeted_entries(entries: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Count HPV1 toward target coverage only for this RHU's positive-target schools."""
    if entries is None or entries.empty or targets is None or targets.empty:
        return pd.DataFrame(columns=entries.columns if isinstance(entries, pd.DataFrame) else None)
    t = targets.copy()
    t["_key"] = (
        t["Municipality"].map(normalize_municipality_key).astype(str)
        + "|" + t["School ID"].map(_clean_school_id).astype(str)
    )
    positive = pd.to_numeric(t["G5 Target"], errors="coerce").gt(0)
    matched_keys = set(t.loc[positive, "_key"])
    e_key = (
        entries["municipality"].map(normalize_municipality_key).astype(str)
        + "|" + entries["school_id"].map(_clean_school_id).astype(str)
    )
    return entries.loc[e_key.isin(matched_keys)].copy()


def _g5_unmatched_school_details(entries: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Identify genuine HPV1 doses not eligible for school target-based coverage."""
    columns = ["Municipality", "School ID", "School Name", "HPV Vaccinated",
               "G5 Target", "Target Status"]
    if entries is None or entries.empty:
        return pd.DataFrame(columns=columns)
    eligible = _targeted_entries(entries, targets)
    unmatched = entries.loc[~entries.index.isin(eligible.index)].copy()
    if unmatched.empty:
        return pd.DataFrame(columns=columns)
    unmatched["hpv_dose1"] = pd.to_numeric(unmatched["hpv_dose1"], errors="coerce").fillna(0)
    unmatched = unmatched.loc[unmatched["hpv_dose1"].gt(0)].copy()
    if unmatched.empty:
        return pd.DataFrame(columns=columns)
    unmatched["school_id"] = unmatched["school_id"].map(_clean_school_id)
    detail = (
        unmatched.groupby(["municipality", "school_id", "school_name"], dropna=False,
                          as_index=False)["hpv_dose1"].sum()
        .rename(columns={"municipality": "Municipality", "school_id": "School ID",
                         "school_name": "School Name", "hpv_dose1": "HPV Vaccinated"})
    )
    if targets is not None and not targets.empty:
        reference = targets[["Municipality", "School ID", "G5 Target"]].copy()
        reference["School ID"] = reference["School ID"].map(_clean_school_id)
        reference["_municipality_key"] = reference["Municipality"].map(normalize_municipality_key)
        reference = reference.drop_duplicates(["_municipality_key", "School ID"])
        detail["_municipality_key"] = detail["Municipality"].map(normalize_municipality_key)
        detail = detail.merge(
            reference[["_municipality_key", "School ID", "G5 Target"]],
            on=["_municipality_key", "School ID"], how="left",
        ).drop(columns="_municipality_key")
    else:
        detail["G5 Target"] = np.nan
    detail["Target Status"] = np.where(
        detail["G5 Target"].isna(), "Missing/unreported G5 target",
        "Entered target is 0 (verify against RHU)",
    )
    return detail[columns].sort_values(
        ["Municipality", "HPV Vaccinated", "School Name"],
        ascending=[True, False, True],
    ).reset_index(drop=True)


def _municipality_summary(entries: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Distinguish raw reported HPV1 from target-matched HPV1 for all 27 RHUs."""
    rows: list[dict] = []
    for muni in ABRA_MUNIS:
        key = normalize_municipality_key(muni)
        t = targets.loc[targets["Municipality"].map(normalize_municipality_key).eq(key)].copy() if not targets.empty else pd.DataFrame()
        e = entries.loc[entries["municipality"].map(normalize_municipality_key).eq(key)].copy() if not entries.empty else pd.DataFrame()
        ec = _targeted_entries(e, t)
        target = float(pd.to_numeric(t.get("G5 Target", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        vaccinated = float(pd.to_numeric(ec.get("hpv_dose1", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        reported = float(pd.to_numeric(e.get("hpv_dose1", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        rows.append({
            "Municipality": muni,
            "G5 Target": target,
            "HPV Vaccinated": vaccinated,  # Coverage-eligible; legacy map contract.
            "Total Vaccinated": reported,
            "Unmatched Vaccinations": max(reported - vaccinated, 0),
            "HPV Coverage %": _safe_pct(vaccinated, target),
            "Remaining": max(target - vaccinated, 0),
            "Schools Reporting": int(e["school_id"].replace("", pd.NA).dropna().nunique()) if not e.empty else 0,
        })
    return pd.DataFrame(rows)


def _school_summary(entries: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Show targeted and untargeted schools, retaining the raw HPV1 accomplishments."""
    target_columns = ["Municipality", "Barangay", "School ID", "School Name", "G5 Target"]
    grouped = (targets[target_columns].copy() if targets is not None and not targets.empty
               else pd.DataFrame(columns=target_columns))
    if entries is not None and not entries.empty:
        e = entries.copy()
        e["school_id"] = e["school_id"].map(_clean_school_id)
        for column in ("school_name", "barangay"):
            if column not in e.columns:
                e[column] = ""
        e["hpv_dose1"] = pd.to_numeric(e["hpv_dose1"], errors="coerce").fillna(0)
        counts = (e.groupby(["municipality", "school_id"], dropna=False, as_index=False)
                  ["hpv_dose1"].sum()
                  .rename(columns={"municipality": "Municipality", "school_id": "School ID",
                                   "hpv_dose1": "Total Vaccinated"}))
        school_labels = (e.groupby(["municipality", "school_id"], dropna=False, as_index=False)
                         .agg({"school_name": "first", "barangay": "first"})
                         .rename(columns={"municipality": "Municipality", "school_id": "School ID",
                                          "school_name": "Reported School Name", "barangay": "Reported Barangay"}))
        counts = counts.merge(school_labels, on=["Municipality", "School ID"], how="left")
        # Merge on both municipality and school ID; the same numeric ID must
        # never make another RHU's vaccinations eligible for coverage.
        grouped = grouped.merge(counts, on=["Municipality", "School ID"], how="outer")
        for label, backup in (("School Name", "Reported School Name"),
                              ("Barangay", "Reported Barangay")):
            grouped[label] = grouped[label].fillna(grouped[backup])
        grouped = grouped.drop(columns=["Reported School Name", "Reported Barangay"])
    else:
        grouped["Total Vaccinated"] = 0
    if grouped.empty:
        return grouped
    grouped["Total Vaccinated"] = pd.to_numeric(grouped["Total Vaccinated"], errors="coerce").fillna(0)
    grouped["G5 Target"] = pd.to_numeric(grouped["G5 Target"], errors="coerce")
    valid = grouped["G5 Target"].gt(0)
    grouped["HPV Vaccinated"] = grouped["Total Vaccinated"].where(valid, 0)
    grouped["Unmatched Vaccinations"] = grouped["Total Vaccinated"].where(~valid, 0)
    grouped["HPV Coverage %"] = grouped["HPV Vaccinated"].div(grouped["G5 Target"].where(valid)).mul(100)
    grouped["Remaining"] = (grouped["G5 Target"] - grouped["HPV Vaccinated"]).clip(lower=0)
    grouped["Target Status"] = np.select(
        [grouped["G5 Target"].isna(), grouped["G5 Target"].eq(0)],
        ["Missing/unreported G5 target", "Entered target is 0"],
        default="Positive G5 target",
    )
    return grouped.sort_values(
        ["HPV Coverage %", "Total Vaccinated", "School Name"],
        ascending=[False, False, True], na_position="last",
    ).reset_index(drop=True)

def _reason_summary(entries: pd.DataFrame) -> pd.DataFrame:
    counts = {code: 0 for code in REASON_LABELS}
    if entries is not None and not entries.empty and "reason_counts" in entries.columns:
        for value in entries["reason_counts"]:
            if not isinstance(value, dict):
                continue
            for code in counts:
                counts[code] += int(pd.to_numeric(pd.Series([value.get(code, 0)]), errors="coerce").fillna(0).iloc[0])
    return pd.DataFrame([
        {"Code": code, "Reason": label, "Count": counts[code]}
        for code, label in REASON_LABELS.items()
        if counts[code] > 0
    ])



def _build_g5_remaining_rows(
    entries: pd.DataFrame, targets: pd.DataFrame, selected_muni: str | None,
) -> pd.DataFrame:
    """Build current G5 HPV1/Actual Target figures, excluding un-targeted doses.

    Municipal view includes all 27 RHUs; selecting an RHU switches to schools.
    Missing or zero targets remain N/A, not a falsely reported 0% coverage.
    """
    if selected_muni is None:
        summary = _municipality_summary(entries, targets)
        rows = summary.rename(columns={
            "Municipality": "Category", "G5 Target": "Total Target",
            "HPV Vaccinated": "Vaccinated", "HPV Coverage %": "Coverage %",
        })[["Category", "Total Target", "Vaccinated", "Coverage %",
            "Total Vaccinated", "Unmatched Vaccinations"]].copy()
    else:
        # Include schools without positive targets so unmatched recorded
        # vaccinations stay visible without being counted in coverage.
        summary = _school_summary(entries, targets)
        if summary.empty:
            return pd.DataFrame(columns=["Category", "Total Target", "Vaccinated", "Coverage %", "Remaining Target", "Above Target"])
        rows = summary.rename(columns={
            "School Name": "Category", "G5 Target": "Total Target",
            "HPV Vaccinated": "Vaccinated", "HPV Coverage %": "Coverage %",
        })[["Category", "School ID", "Total Target", "Vaccinated", "Coverage %",
            "Total Vaccinated", "Unmatched Vaccinations"]].copy()
        # Prevent two schools with identical names from appearing as one label.
        ambiguous = rows["Category"].duplicated(keep=False)
        rows.loc[ambiguous, "Category"] = (
            rows.loc[ambiguous, "Category"].astype(str)
            + " [" + rows.loc[ambiguous, "School ID"].astype(str) + "]"
        )
        rows = rows.drop(columns=["School ID"])

    rows["Total Target"] = pd.to_numeric(rows["Total Target"], errors="coerce")
    rows["Vaccinated"] = pd.to_numeric(rows["Vaccinated"], errors="coerce").fillna(0)
    rows["Total Vaccinated"] = pd.to_numeric(rows["Total Vaccinated"], errors="coerce").fillna(0)
    rows["Unmatched Vaccinations"] = pd.to_numeric(rows["Unmatched Vaccinations"], errors="coerce").fillna(0)
    # 0 is a valid entered G5 target; it still cannot serve as a denominator.
    has_target = rows["Total Target"].gt(0)
    rows["Coverage %"] = rows["Vaccinated"].div(rows["Total Target"].where(has_target)).mul(100)
    rows["Remaining Target"] = (rows["Total Target"] - rows["Vaccinated"]).clip(lower=0).fillna(0)
    rows["Above Target"] = (rows["Vaccinated"] - rows["Total Target"]).clip(lower=0).fillna(0)
    rows.loc[~has_target, "Category"] = rows.loc[~has_target, "Category"].astype(str) + " (N/A)"
    return rows.sort_values(
        ["Total Vaccinated", "Coverage %", "Category"],
        ascending=[False, False, True], na_position="last",
    ).reset_index(drop=True)


def _render_g5_vaccinated_remaining(
    entries: pd.DataFrame,
    targets: pd.DataFrame,
    selected_muni: str | None,
    start_date: date | None,
    end_date: date | None,
) -> None:
    """DMO-style stacked counts/coverage chart for single-dose Grade 5 HPV."""
    st.markdown("#### Grade 5 HPV — Vaccinated vs Remaining Target")
    st.markdown(
        '<div style="font-size:22px;font-weight:800;color:#124f61;'
        'margin:0.35rem 0 0.7rem 0;line-height:1.3;">'
        'Antigen: HPV (Grade 5)</div>',
        unsafe_allow_html=True,
    )
    display_as = st.selectbox(
        "Display as:",
        ("Vaccination Count", "Coverage (%)"),
        key="sbi_g5_remaining_display",
        help="Compare Grade 5 HPV vaccinated and remaining target as counts or as percentages.",
    )
    rows = _build_g5_remaining_rows(entries, targets, selected_muni)
    if rows.empty:
        st.info("No Grade 5 target records are available for this selection.")
        return
    missing = rows["Total Target"].isna() | rows["Total Target"].le(0)
    if missing.any():
        st.caption(f"{int(missing.sum()):,} municipality/school row(s) lack a positive G5 target and are shown as N/A.")
    unmatched_total = int(rows["Unmatched Vaccinations"].sum())
    if unmatched_total:
        st.warning(
            f"{unmatched_total:,} reported G5 HPV vaccination(s) do not match a school with a "
            "positive G5 target. They appear separately in the chart and review table, "
            "but are NOT counted toward coverage until the school targets are verified."
        )
    if rows["Above Target"].gt(0).any():
        st.caption("Target-matched vaccinated counts can exceed the selected target; remaining target stays at zero.")

    as_pct = display_as == "Coverage (%)"
    view = rows.copy()
    if as_pct:
        view["Vaccinated Plot"] = view["Coverage %"].clip(lower=0).fillna(0)
        view["Remaining Plot"] = (100.0 - view["Coverage %"]).clip(lower=0).fillna(0)
        view = view.sort_values(
            ["Coverage %", "Vaccinated", "Category"],
            ascending=[False, False, True], na_position="last",
        ).reset_index(drop=True)
        legend = ("Target-matched Coverage (%)", "Remaining Target (%)")
        axis_label, suffix = "Coverage of G5 target (%)", "_Coverage"
        explanation = (
            "Coverage = Vaccinated / G5 Target × 100; "
            "Remaining (%) = max(100 − Coverage, 0)."
        )
    else:
        view["Vaccinated Plot"] = view["Vaccinated"]
        view["Remaining Plot"] = view["Remaining Target"]
        legend = ("Vaccinated (matched to target)", "Remaining Target")
        axis_label, suffix = "Number of students", "_Count"
        explanation = "Remaining Target = max(G5 Target − matched vaccinations, 0)."

    # Unmatched counts are a distinct grey segment beyond the selected target
    # in Count mode; never insert counts into the percentage denominator.
    maximum = float((view["Vaccinated Plot"] + view["Remaining Plot"]
                     + (0 if as_pct else view["Unmatched Vaccinations"])).max())
    axis_max = max(100.0 if as_pct else 1.0, maximum)
    threshold = axis_max * 0.035
    ylabels = view["Category"].tolist()

    def num_text(value: float) -> str:
        return f"{value:.1f}%" if as_pct else f"{value:,.0f}"

    def fmt_int(value: object) -> str:
        return f"{float(value):,.0f}" if pd.notna(value) else "N/A"

    meta = np.array([
        [fmt_int(row["Vaccinated"]), fmt_int(row["Remaining Target"]),
         fmt_int(row["Total Target"]) if pd.notna(row["Total Target"]) and row["Total Target"] > 0 else "N/A",
         f"{row['Coverage %']:.1f}%" if pd.notna(row["Coverage %"]) else "N/A",
         fmt_int(row["Total Vaccinated"]), fmt_int(row["Unmatched Vaccinations"])]
        for _, row in view.iterrows()
    ], dtype=object)
    fig = go.Figure()
    for column, label, color in (
        ("Vaccinated Plot", legend[0], "#17688b"),
        ("Remaining Plot", legend[1], "#ec722c"),
    ):
        fig.add_trace(go.Bar(
            name=label,
            x=view[column].to_list(),
            y=ylabels,
            orientation="h",
            marker_color=color,
            text=[num_text(float(n)) if float(n) > 0 and float(n) >= threshold else ""
                  for n in view[column]],
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(size=12, color="white"),
            customdata=meta,
            hovertemplate=(
                "<b>%{y}</b><br>Reported vaccinated: %{customdata[4]}"
                "<br>Target-matched vaccinated: %{customdata[0]}"
                "<br>Unmatched (needs target review): %{customdata[5]}"
                "<br>Remaining Target: %{customdata[1]}"
                "<br>G5 Target: %{customdata[2]}"
                "<br>Target-based coverage: %{customdata[3]}<extra></extra>"
            ),
        ))

    if not as_pct:
        fig.add_trace(go.Bar(
            name="Unmatched vaccinations (not in coverage)",
            x=view["Unmatched Vaccinations"].tolist(),
            y=ylabels, orientation="h", marker_color="#8065a8",
            text=[fmt_int(v) if float(v) >= threshold and float(v) > 0 else ""
                  for v in view["Unmatched Vaccinations"]],
            textposition="inside", insidetextanchor="middle",
            textfont=dict(size=12, color="white"),
            customdata=meta,
            hovertemplate=(
                "<b>%{y}</b><br>Unmatched vaccinations: %{customdata[5]}"
                "<br>Reported vaccinations: %{customdata[4]}"
                "<br>Matched to positive targets: %{customdata[0]}"
                "<br>Target-based coverage: %{customdata[3]}<extra></extra>"
            ),
        ))

    # Small positive values must be visible in the PNG, even next to big targets.
    for _, row in view.iterrows():
        vaccinated = float(row["Vaccinated Plot"])
        remaining = float(row["Remaining Plot"])
        tiny_vaccinated = 0 < vaccinated < threshold
        tiny_remaining = 0 < remaining < threshold
        if tiny_vaccinated and tiny_remaining:
            callouts = [(
                vaccinated + remaining,
                f"<b>Vaccinated: {num_text(vaccinated)} | Remaining: {num_text(remaining)}</b>",
                "#124b69",
            )]
        else:
            callouts = []
            if tiny_vaccinated:
                callouts.append((vaccinated, f"<b>{num_text(vaccinated)}</b>", "#124b69"))
            if tiny_remaining:
                callouts.append((vaccinated + remaining, f"<b>{num_text(remaining)}</b>", "#9c3f15"))
        if as_pct and float(row["Unmatched Vaccinations"]) > 0:
            # Percentage is still 0..100 target-based; display unmatched as a
            # count next to the bar, not as an invalid percentage segment.
            callouts.append((
                max(vaccinated + remaining, 100.0),
                f"<b>+{fmt_int(row['Unmatched Vaccinations'])} unmatched</b>",
                "#69448b",
            ))
        elif not as_pct and 0 < float(row["Unmatched Vaccinations"]) < threshold:
            callouts.append((
                vaccinated + remaining + float(row["Unmatched Vaccinations"]),
                f"<b>+{fmt_int(row['Unmatched Vaccinations'])} unmatched</b>",
                "#69448b",
            ))
        for xpos, txt, color in callouts:
            fig.add_annotation(
                x=xpos, y=str(row["Category"]), xref="x", yref="y",
                text=txt, showarrow=False, xanchor="left", xshift=5,
                font=dict(size=12, color=color),
                bgcolor="rgba(255,255,255,0.97)",
                bordercolor="rgba(148,163,184,0.6)", borderwidth=1, borderpad=2,
            )

    if start_date and end_date:
        reporting_period = f"{start_date:%b %d, %Y} to {end_date:%b %d, %Y}"
    elif start_date:
        reporting_period = f"From {start_date:%b %d, %Y}"
    elif end_date:
        reporting_period = f"Through {end_date:%b %d, %Y}"
    else:
        reporting_period = "All available dates"
    scope = "All Municipalities (Abra)" if selected_muni is None else f"{selected_muni}, Abra — by School"
    header = (
        f"<b>Display:</b> {escape(display_as)}"
        "<br><b>Accomplishment source:</b> RHU Workbook (operational/provisional)"
        "<br><b>Target basis:</b> Actual Targets (Unvaccinated G5 Female)"
        f"<br><b>Scope:</b> {escape(scope)}"
        f"<br><b>Reporting period:</b> {escape(reporting_period)}"
        f"<br>{escape(explanation)} Unmatched HPV1 doses are reported separately, not included in coverage."
        "<br>Grade 5 accomplishments come from RHU workbooks, not VaccTrack."
    )
    fig.update_layout(
        title=None,
        barmode="stack",
        height=max(650, min(1900, len(view) * 40 + 345)),
        plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False,
        xaxis_title=axis_label, yaxis_title="",
        legend=dict(orientation="h", yanchor="bottom", y=1.025, xanchor="right", x=1),
        margin=dict(l=15, r=140 if as_pct else 95, t=300, b=60),
        bargap=0.20,
    )
    fig.add_annotation(
        xref="paper", yref="paper", x=0, y=1,
        yshift=255, xanchor="left", yanchor="top",
        text="<b>SBI 2026 | Grade 5 HPV Vaccinated vs Remaining Target</b>",
        font=dict(size=19, color="#172e43"),
        showarrow=False, align="left",
    )
    fig.add_annotation(
        xref="paper", yref="paper", x=0, y=1,
        yshift=222, xanchor="left", yanchor="top",
        text="<b>Antigen: HPV (Grade 5)</b>",
        font=dict(size=22, color="#124f61"),
        showarrow=False, align="left",
    )
    fig.add_annotation(
        xref="paper", yref="paper", x=0, y=1,
        yshift=185, xanchor="left", yanchor="top",
        text=header,
        font=dict(size=13, color="#334155"),
        showarrow=False, align="left",
    )
    fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(ylabels)), automargin=True)
    fig.update_xaxes(range=[0, axis_max * (1.24 if as_pct else 1.15)], ticksuffix="%" if as_pct else "")

    st.plotly_chart(
        fig, width="stretch", key="sbi_g5_vaccinated_remaining",
        config=ultra_png_export_config(
            f"SBI_G5_Vaccinated_vs_Remaining{suffix}",
            scale=8, width=1450,
            height=max(860, min(2000, len(view) * 46 + 380)),
        ),
    )
    with st.expander("View and download Grade 5 vaccinated vs remaining figures", expanded=False):
        table = view[[
            "Category", "Total Vaccinated", "Vaccinated", "Unmatched Vaccinations",
            "Remaining Target", "Total Target", "Coverage %", "Above Target",
        ]].rename(columns={"Category": "Municipality" if selected_muni is None else "School"})
        st.dataframe(table.rename(columns={"Vaccinated": "Target-matched Vaccinated"}), width="stretch", hide_index=True)
        st.download_button(
            "Download Grade 5 Vaccinated vs Remaining (CSV)",
            table.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"SBI_G5_Vaccinated_vs_Remaining{suffix}.csv",
            mime="text/csv", key="sbi_g5_remaining_csv",
        )


def render_g5_dashboard(
    entries: pd.DataFrame,
    actual_targets: pd.DataFrame,
    start_date: date | None,
    end_date: date | None,
    selected_muni: str | None,
) -> None:
    """Render the standalone Grade 5 HPV catch-up dashboard."""
    st.markdown("### Grade 5 HPV Catch-up Dashboard")
    st.caption(
        "Operational Grade 5 accomplishments from RHU workbooks. The denominator is "
        "Unvaccinated G5 Female from Actual Targets. Grade 5 is intentionally excluded "
        "from VaccTrack G1/G4/G7 sheets and VaccTrack reconciliation. "
        "Under the current G5 guidance, encode G5 HPV vaccinations under "
        "**HPV Dose 1** in the shared workbook. This is the **HPV1** value submitted to "
        "the Central Office G5 Google reporting platform; do not use the HPV Dose 2 fields for G5."
    )

    target_view, pending_targets = _prepare_g5_targets(actual_targets, selected_muni)
    if pending_targets:
        st.warning(
            f"{pending_targets:,} school(s) in this scope do not yet have an entered Unvaccinated G5 Female target. "
            "They are excluded from G5 coverage denominators until a value is entered; a confirmed 0 remains valid."
        )

    entries_view, legacy_count = prepare_workbook_entries(
        entries,
        start_date=start_date,
        end_date=end_date,
        municipality=selected_muni,
    )
    if legacy_count:
        st.info(f"{legacy_count:,} legacy/manual accomplishment row(s) are excluded. Only workbook-sourced records are shown.")
    g5 = entries_view.loc[entries_view["grade_level"].eq("G5")].copy() if not entries_view.empty else pd.DataFrame()

    for col in ["hpv_dose1", "hpv_dose2", "hpv1_deferred", "hpv2_deferred", "hpv1_refused", "hpv2_refused"]:
        if col not in g5.columns:
            g5[col] = 0
        g5[col] = pd.to_numeric(g5[col], errors="coerce").fillna(0)

    coverage_entries = _targeted_entries(g5, target_view)
    target_total = float(pd.to_numeric(target_view.get("G5 Target", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not target_view.empty else 0.0
    eligible_vaccinated = float(pd.to_numeric(coverage_entries.get("hpv_dose1", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    all_vaccinated = int(pd.to_numeric(g5.get("hpv_dose1", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    legacy_hpv2 = int(pd.to_numeric(g5.get("hpv_dose2", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    legacy_missed = int(sum(pd.to_numeric(g5.get(col, pd.Series(dtype=float)), errors="coerce").fillna(0).sum() for col in ["hpv2_deferred", "hpv2_refused"]))

    # Use the same municipality + school eligibility result as the chart,
    # including when different RHUs happen to share a school identifier.
    unmatched = (g5.loc[~g5.index.isin(coverage_entries.index)].copy()
                 if not g5.empty else pd.DataFrame())
    unmatched_doses = int(pd.to_numeric(unmatched.get("hpv_dose1", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    if unmatched_doses > 0:
        st.warning(
            f"{unmatched_doses:,} reported G5 HPV vaccination(s) need target verification. "
            "These records are preserved and shown in the chart, but are excluded "
            "from coverage until their schools have positive G5 targets in Actual Targets."
        )
        detail = _g5_unmatched_school_details(g5, target_view)
        with st.expander(f"Review {unmatched_doses:,} G5 vaccinations needing target verification", expanded=False):
            st.caption(
                "Check the Unvaccinated G5 Female target for each listed school in Actual Targets. "
                "Do not change a verified zero just to increase coverage; investigate whether "
                "the target or vaccination record needs correction."
            )
            st.dataframe(detail, width="stretch", hide_index=True)
            st.download_button(
                "Download G5 Target Verification List (CSV)",
                data=detail.to_csv(index=False).encode("utf-8-sig"),
                file_name="SBI_G5_Target_Verification.csv", mime="text/csv",
                key="sbi_g5_unmatched_export",
            )
    if legacy_hpv2 or legacy_missed:
        st.warning(
            f"G5 has {legacy_hpv2:,} legacy HPV Dose 2 vaccination(s) and {legacy_missed:,} "
            "legacy HPV2 deferred/refused count(s). These are NOT included in current "
            "HPV coverage and were not reassigned to HPV Dose 1. "
            "Please review and correct the RHU workbook if these were encoded in the wrong field."
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Unvaccinated G5 Female Target", f"{int(target_total):,}")
    c2.metric("HPV Vaccinated", f"{all_vaccinated:,}", help="Grade 5 HPV1 / HPV Dose 1 accomplishments; does not include the legacy HPV Dose 2 field.")
    c3.metric("HPV Coverage", "N/A" if pd.isna(_safe_pct(eligible_vaccinated, target_total)) else f"{_safe_pct(eligible_vaccinated, target_total):.1f}%")
    c4.metric("Remaining", f"{int(max(target_total - eligible_vaccinated, 0)):,}")

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Matched to G5 Targets", f"{int(eligible_vaccinated):,}",
              help="Only vaccinations at schools with a positive G5 target are included in coverage.")
    s2.metric("Needs Target Verification", f"{unmatched_doses:,}",
              help="Reported vaccinations at schools with zero or missing G5 targets; not included in coverage.")
    s3.metric("Schools Reporting", f"{int(g5['school_id'].replace('', pd.NA).dropna().nunique()) if not g5.empty else 0:,}")
    s4.metric("RHU(s) Reporting", f"{int(g5['municipality'].nunique()) if not g5.empty else 0:,}")

    if g5.empty:
        st.info("No Grade 5 RHU workbook accomplishments are available for this selection and reporting period yet.")
        # Even before the first upload, RHUs can review the remaining G5
        # target workload from the established Actual Targets roster.
        if not target_view.empty:
            st.divider()
            _render_g5_vaccinated_remaining(
                g5, target_view, selected_muni, start_date, end_date,
            )
        return

    if target_view.empty:
        st.warning("No entered Unvaccinated G5 Female targets are available in this scope, so coverage percentages cannot yet be calculated.")

    st.divider()
    location_label = "Abra Province" if not selected_muni else f"{selected_muni}, Abra"

    muni = _municipality_summary(g5, target_view)
    if selected_muni is None:
        st.markdown("#### Municipality Progress")
        st.dataframe(
            muni, width="stretch", hide_index=True,
            column_config={
                "G5 Target": st.column_config.NumberColumn(format="%d"),
                "HPV Vaccinated": st.column_config.NumberColumn("Target-matched Vaccinated", format="%d"),
                "Total Vaccinated": st.column_config.NumberColumn("Total Reported Vaccinated", format="%d"),
                "Unmatched Vaccinations": st.column_config.NumberColumn("Needs Target Verification", format="%d"),
                "HPV Coverage %": st.column_config.NumberColumn(format="%.1f%%"),
                "Remaining": st.column_config.NumberColumn(format="%d"),
            },
        )
        render_municipality_choropleth(
            muni,
            coverage_col="HPV Coverage %",
            title="Grade 5 HPV Coverage by Municipality",
            key="sbi_g5_map_single_dose_hpv",
            target_col="G5 Target",
            vaccinated_col="HPV Vaccinated",
            remaining_col="Remaining",
            show_zero_target_as_na=True,
            export_context="Target basis: Actual Targets (Unvaccinated G5 Female)",
        )

        chart_rows = muni.copy()
        chart_rows["HPV Coverage %"] = pd.to_numeric(chart_rows["HPV Coverage %"], errors="coerce")
        chart_rows = chart_rows.sort_values(["HPV Coverage %", "Municipality"], ascending=[True, False], na_position="first")
        st.markdown("#### Coverage by Municipality")
        fig_muni = px.bar(
            chart_rows,
            x="HPV Coverage %",
            y="Municipality",
            orientation="h",
            text=chart_rows["HPV Coverage %"].map(lambda v: "N/A" if pd.isna(v) else f"{float(v):.1f}%"),
            custom_data=["G5 Target", "HPV Vaccinated", "Remaining"],
        )
        fig_muni.update_traces(
            textposition="outside",
            hovertemplate=(
                "<b>%{y}</b><br>Coverage: %{x:.1f}%<br>"
                "Target: %{customdata[0]:,.0f}<br>"
                "Vaccinated: %{customdata[1]:,.0f}<br>"
                "Remaining: %{customdata[2]:,.0f}<extra></extra>"
            ),
            cliponaxis=False,
        )
        fig_muni.add_vline(
            x=90,
            line_dash="dash",
            line_color="rgba(216,27,96,0.70)",
            annotation_text="90% target",
            annotation_position="top right",
        )
        fig_muni.update_layout(
            dragmode=False,
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="Coverage %",
            yaxis_title="",
            height=max(500, len(chart_rows) * 28),
            margin=dict(l=10, r=35, t=15, b=60),
            showlegend=False,
        )
        # Horizontal bar category order is bottom-to-top, so ascending values
        # display the highest coverage at the top and N/A at the bottom.
        fig_muni.update_yaxes(
            categoryorder="array", categoryarray=chart_rows["Municipality"].tolist(),
        )
        fig_muni.update_xaxes(range=[0, max(100, float(pd.to_numeric(chart_rows["HPV Coverage %"], errors="coerce").max() or 0) + 5)])
        st.plotly_chart(
            fig_muni,
            width="stretch",
            key="sbi_g5_coverage_by_municipality",
            config=ultra_png_export_config("sbi_g5_coverage_by_municipality", height=max(1200, len(chart_rows) * 55)),
        )
    else:
        st.caption("SBI Grade 5 monitoring is school-based. For a specific municipality, performance is shown by school rather than by barangay.")

    st.divider()
    _render_g5_vaccinated_remaining(
        g5, target_view, selected_muni, start_date, end_date,
    )

    trend = g5.rename(columns={"activity_date": "Report Date", "hpv_dose1": "HPV Vaccinated"})
    render_daily_trend(
        trend,
        [("HPV Vaccinated", "HPV Vaccinated")],
        "Daily Grade 5 HPV Accomplishments",
        key="sbi_g5_daily_trend",
    )

    daily = (
        trend.dropna(subset=["Report Date"])
        .groupby("Report Date", as_index=False)["HPV Vaccinated"]
        .sum()
        .sort_values("Report Date")
    )
    if not daily.empty:
        daily["Cumulative HPV Vaccinated"] = daily["HPV Vaccinated"].cumsum()
        fig = px.line(daily, x="Report Date", y="Cumulative HPV Vaccinated", markers=True)
        if target_total > 0:
            fig.add_hline(y=target_total, line_dash="dash", annotation_text="G5 target", annotation_position="top left")
        fig.update_layout(
            dragmode=False,
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="",
            yaxis_title="Cumulative vaccinated students",
            height=430,
            margin=dict(l=10, r=20, t=35, b=65),
            hovermode="x unified",
        )
        st.markdown("#### Cumulative Grade 5 HPV Progress")
        st.plotly_chart(
            fig,
            width="stretch",
            key="sbi_g5_cumulative",
            config=ultra_png_export_config("sbi_g5_cumulative", height=1200),
        )

    st.divider()
    st.markdown("#### School-Level Grade 5 Performance")
    schools = _school_summary(g5, target_view)
    if schools.empty:
        st.info("No school-level G5 target rows are available for this selection.")
    else:
        st.dataframe(
            schools[["Municipality", "Barangay", "School ID", "School Name", "G5 Target",
                     "Total Vaccinated", "HPV Vaccinated", "Unmatched Vaccinations",
                     "HPV Coverage %", "Remaining", "Target Status"]],
            width="stretch",
            hide_index=True,
            column_config={
                "G5 Target": st.column_config.NumberColumn(format="%d"),
                "HPV Vaccinated": st.column_config.NumberColumn("Target-matched Vaccinated", format="%d"),
                "Total Vaccinated": st.column_config.NumberColumn("Total Reported Vaccinated", format="%d"),
                "Unmatched Vaccinations": st.column_config.NumberColumn("Needs Target Verification", format="%d"),
                "HPV Coverage %": st.column_config.NumberColumn(format="%.1f%%"),
                "Remaining": st.column_config.NumberColumn(format="%d"),
            },
        )

    st.divider()
    st.markdown("#### Grade 5 HPV Deferrals & Refusals")
    d1, d2 = st.columns(2)
    d1.metric("HPV Deferred", f"{int(g5['hpv1_deferred'].sum()):,}")
    d2.metric("HPV Refused", f"{int(g5['hpv1_refused'].sum()):,}")

    reasons = _reason_summary(g5)
    if reasons.empty:
        st.caption("No Grade 5 Reason 01–19 counts are recorded in this selection.")
    else:
        st.dataframe(reasons, width="stretch", hide_index=True)

    st.divider()
    with st.expander("Legacy HPV2 fields (historical review only)", expanded=False):
        st.caption("These Grade 5 HPV2 fields remain in stored records for audit compatibility. They do not contribute to the current one-dose HPV coverage figures.")
        l1, l2, l3 = st.columns(3)
        l1.metric("Legacy HPV Dose 2", f"{legacy_hpv2:,}")
        l2.metric("Legacy HPV2 Deferred", f"{int(g5['hpv2_deferred'].sum()):,}")
        l3.metric("Legacy HPV2 Refused", f"{int(g5['hpv2_refused'].sum()):,}")

    with st.expander("View and download Grade 5 workbook records", expanded=False):
        export_cols = [
            "municipality", "activity_date", "school_id", "school_name", "barangay",
            "hpv_dose1", "hpv_dose2", "hpv1_deferred", "hpv2_deferred", "hpv1_refused", "hpv2_refused",
            "updated_by", "updated_at",
        ]
        raw = g5[[c for c in export_cols if c in g5.columns]].copy()
        raw = raw.rename(columns={
            "municipality": "Municipality", "activity_date": "Activity Date", "school_id": "School ID",
            "school_name": "School Name", "barangay": "Barangay", "hpv_dose1": "HPV Vaccinated (HPV1 field)",
            "hpv_dose2": "HPV Dose 2", "hpv1_deferred": "HPV1 Deferred", "hpv2_deferred": "HPV2 Deferred",
            "hpv1_refused": "HPV1 Refused", "hpv2_refused": "HPV2 Refused", "updated_by": "Updated By", "updated_at": "Updated At",
        })
        st.dataframe(raw, width="stretch", hide_index=True)
        st.download_button(
            "Download Grade 5 Records (CSV)",
            data=raw.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"SBI_G5_Accomplishments_{location_label.replace(', ', '_').replace(' ', '_')}.csv",
            mime="text/csv",
            key="sbi_g5_raw_download",
        )




