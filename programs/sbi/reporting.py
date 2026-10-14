"""Reusable SBI reporting visuals.

These helpers adapt the reporting patterns already used by the MR SIA dashboard
for the SBI VaccTrack schema: daily activity, cumulative campaign progress,
monthly tally sheets, geographic coverage tables, and municipality choropleths.
"""

from __future__ import annotations

from calendar import monthrange
from copy import deepcopy
import re
import unicodedata

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.config import ABRA_MUNIS
from core.geo import fetch_abra_geojson, fetch_barangay_geojson, get_polygon_centroid
from core.map_labels import get_label_nudge, get_runtime_label_nudges


def _geo_key(value: object) -> str:
    text = str(value or "").upper().strip()
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII")
    key = re.sub(r"[^A-Z0-9]", "", text)
    aliases = {
        "SALAPADAN": "SALLAPADAN",
        "LICUANBAAYLICUAN": "LICUANBAAY",
        "BANGUEDCAPITAL": "BANGUED",
    }
    return aliases.get(key, key)



def _barangay_key(value: object) -> str:
    """Normalize barangay labels to the same join key used by local GeoJSON."""
    text = str(value or "").upper().strip()
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII")
    text = re.sub(r"\(.*?\)", "", text)
    text = text.replace("BARANGAY", "").replace("BRGY", "")
    text = text.replace("POBLACION", "POB").replace("POB.", "POB")
    return re.sub(r"[^A-Z0-9]", "", text)

def _ordered_abra_names() -> list[str]:
    names = []
    for muni in ABRA_MUNIS:
        name = str(muni).title()
        if _geo_key(name) == _geo_key("PEÑARRUBIA"):
            name = "Peñarrubia"
        names.append(name)
    return names


def _daily_summary(events: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    if events is None or events.empty or "Report Date" not in events.columns:
        return pd.DataFrame()

    cols = [c for c in value_cols if c in events.columns]
    if not cols:
        return pd.DataFrame()

    work = events.dropna(subset=["Report Date"]).copy()
    if work.empty:
        return pd.DataFrame()

    work["Report Date"] = pd.to_datetime(work["Report Date"], errors="coerce").dt.normalize()
    work = work.dropna(subset=["Report Date"])
    for col in cols:
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0)

    daily = work.groupby("Report Date", as_index=False)[cols].sum().sort_values("Report Date")
    if daily.empty:
        return daily

    full_dates = pd.DataFrame({
        "Report Date": pd.date_range(daily["Report Date"].min(), daily["Report Date"].max(), freq="D")
    })
    return full_dates.merge(daily, on="Report Date", how="left").fillna({c: 0 for c in cols})


def render_daily_trend(
    events: pd.DataFrame,
    series: list[tuple[str, str]],
    title: str,
    key: str,
    colors: list[str] | None = None,
) -> pd.DataFrame:
    """Render daily SBI vaccination activity using VaccTrack Report Date."""
    value_cols = [col for col, _ in series]
    daily = _daily_summary(events, value_cols)

    st.markdown(
        f'''<h4 style="margin-bottom:0.25rem;">
        <i class="fa-solid fa-calendar-day" style="color:#0033A0; margin-right:8px;"></i>
        {title}
        </h4>''',
        unsafe_allow_html=True,
    )
    if daily.empty:
        st.info("No valid report dates are available for the selected reporting period.")
        return daily

    rename_map = {col: label for col, label in series}
    chart = daily.rename(columns=rename_map).melt(
        id_vars=["Report Date"],
        value_vars=list(rename_map.values()),
        var_name="Series",
        value_name="Vaccinated",
    )
    fig = px.line(
        chart,
        x="Report Date",
        y="Vaccinated",
        color="Series",
        markers=True,
        color_discrete_sequence=colors,
    )
    fig.update_layout(
        dragmode=False,
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis_title="",
        yaxis_title="Students vaccinated",
        height=420,
        margin=dict(l=10, r=20, t=25, b=60),
        legend=dict(orientation="h", yanchor="top", y=-0.16, xanchor="center", x=0.5),
        legend_title_text="",
        hovermode="x unified",
    )
    st.plotly_chart(
        fig,
        width="stretch",
        key=key,
        config={
            "scrollZoom": False,
            "displayModeBar": True,
            "toImageButtonOptions": {"format": "png", "filename": key, "scale": 2},
        },
    )
    return daily


def render_campaign_burnup(
    g1_events: pd.DataFrame,
    g7_events: pd.DataFrame,
    hpv_events: pd.DataFrame,
    mr_td_target: float,
    hpv_target: float,
    key: str,
) -> pd.DataFrame:
    """Render cumulative MR, Td, and HPV first-dose campaign progress."""
    mrtd = pd.concat([g1_events, g7_events], ignore_index=True, sort=False)
    mrtd_daily = _daily_summary(mrtd, ["MR Doses", "Td Doses"])
    hpv_daily = _daily_summary(hpv_events, ["HPV Dose 1"])

    if mrtd_daily.empty and hpv_daily.empty:
        st.info("No valid report dates are available for a campaign burn-up chart.")
        return pd.DataFrame()

    pieces = []
    if not mrtd_daily.empty:
        pieces.append(mrtd_daily)
    if not hpv_daily.empty:
        pieces.append(hpv_daily)

    daily = pieces[0]
    for part in pieces[1:]:
        daily = daily.merge(part, on="Report Date", how="outer")
    daily = daily.sort_values("Report Date").fillna(0)

    for col in ["MR Doses", "Td Doses", "HPV Dose 1"]:
        if col not in daily.columns:
            daily[col] = 0

    daily["MR Cumulative"] = daily["MR Doses"].cumsum()
    daily["Td Cumulative"] = daily["Td Doses"].cumsum()
    daily["HPV 1st Dose Cumulative"] = daily["HPV Dose 1"].cumsum()

    long = daily.melt(
        id_vars=["Report Date"],
        value_vars=["MR Cumulative", "Td Cumulative", "HPV 1st Dose Cumulative"],
        var_name="Series",
        value_name="Cumulative vaccinated",
    )
    fig = px.line(
        long,
        x="Report Date",
        y="Cumulative vaccinated",
        color="Series",
        markers=True,
        color_discrete_sequence=["#1E88E5", "#43A047", "#D81B60"],
    )

    mr_td_goal = float(mr_td_target or 0) * 0.95
    hpv_goal = float(hpv_target or 0) * 0.90
    if mr_td_goal > 0:
        fig.add_hline(
            y=mr_td_goal,
            line_dash="dash",
            line_color="rgba(0,51,160,0.60)",
            annotation_text="95% MR/Td target",
            annotation_position="top left",
        )
    if hpv_goal > 0:
        fig.add_hline(
            y=hpv_goal,
            line_dash="dot",
            line_color="rgba(216,27,96,0.65)",
            annotation_text="90% HPV target",
            annotation_position="bottom right",
        )

    fig.update_layout(
        dragmode=False,
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis_title="",
        yaxis_title="Cumulative vaccinated students",
        height=470,
        margin=dict(l=10, r=25, t=35, b=65),
        legend=dict(orientation="h", yanchor="top", y=-0.16, xanchor="center", x=0.5),
        legend_title_text="",
        hovermode="x unified",
    )
    st.plotly_chart(
        fig,
        width="stretch",
        key=key,
        config={
            "scrollZoom": False,
            "displayModeBar": True,
            "toImageButtonOptions": {"format": "png", "filename": key, "scale": 2},
        },
    )
    return daily


def render_daily_tally(
    events: pd.DataFrame,
    value_col: str,
    label: str,
    geo_col: str,
    key_prefix: str,
    location_label: str,
) -> None:
    """Render a month/day tally grid patterned after the MR SIA tally sheet."""
    if events is None or events.empty or value_col not in events.columns:
        st.info(f"No {label} data are available for the tally sheet.")
        return

    work = events.dropna(subset=["Report Date"]).copy()
    if work.empty:
        st.info("No valid report dates are available for the tally sheet.")
        return

    work["Report Date"] = pd.to_datetime(work["Report Date"], errors="coerce")
    work = work.dropna(subset=["Report Date"])
    work[value_col] = pd.to_numeric(work[value_col], errors="coerce").fillna(0)
    work["Month Period"] = work["Report Date"].dt.to_period("M")

    months = sorted(work["Month Period"].dropna().unique())
    if not months:
        st.info("No valid report dates are available for the tally sheet.")
        return

    month_labels = {period: period.to_timestamp().strftime("%B %Y") for period in months}
    selected = st.selectbox(
        "Select month:",
        months,
        format_func=lambda p: month_labels[p],
        key=f"{key_prefix}_month",
    )
    work = work[work["Month Period"] == selected].copy()
    work["Day"] = work["Report Date"].dt.day.astype(int)

    days_in_month = monthrange(selected.year, selected.month)[1]
    day_cols = list(range(1, days_in_month + 1))
    tally = pd.pivot_table(
        work,
        values=value_col,
        index=geo_col,
        columns="Day",
        aggfunc="sum",
        fill_value=0,
    )

    if geo_col == "Municipality":
        tally = tally.reindex(_ordered_abra_names(), fill_value=0)
    else:
        tally = tally.sort_index()

    tally = tally.reindex(columns=day_cols, fill_value=0)
    tally["Total"] = tally.sum(axis=1)
    tally = tally[["Total"] + day_cols].reset_index()
    tally.columns = [str(c) for c in tally.columns]

    numeric_cols = [c for c in tally.columns if c != geo_col]
    for col in numeric_cols:
        tally[col] = pd.to_numeric(tally[col], errors="coerce").fillna(0).astype(int)

    total_row = {geo_col: "TOTAL"}
    total_row.update({col: int(tally[col].sum()) for col in numeric_cols})
    export = pd.concat([pd.DataFrame([total_row]), tally], ignore_index=True)

    st.dataframe(
        export,
        width="stretch",
        hide_index=True,
        height=min(720, 95 + max(len(export), 5) * 35),
    )
    st.download_button(
        label=f"Download {label} Tally Sheet (CSV)",
        data=export.to_csv(index=False).encode("utf-8-sig"),
        file_name=(
            f"SBI_{key_prefix}_Daily_Tally_{selected.strftime('%Y_%m')}_"
            f"{location_label.replace(', ', '_').replace(' ', '_')}.csv"
        ),
        mime="text/csv",
        key=f"{key_prefix}_download",
    )


def render_tally_tabs(
    events: pd.DataFrame,
    series: list[tuple[str, str]],
    geo_col: str,
    key_prefix: str,
    location_label: str,
) -> None:
    st.markdown(
        '''<h4 style="margin-bottom:0.25rem;">
        <i class="fa-solid fa-calendar-days" style="color:#0033A0; margin-right:8px;"></i>
        Daily Tally Sheets
        </h4>''',
        unsafe_allow_html=True,
    )
    tabs = st.tabs([label for _, label in series])
    for tab, (col, label) in zip(tabs, series):
        with tab:
            render_daily_tally(
                events=events,
                value_col=col,
                label=label,
                geo_col=geo_col,
                key_prefix=f"{key_prefix}_{_geo_key(label).lower()}",
                location_label=location_label,
            )


def render_municipality_choropleth(
    summary: pd.DataFrame,
    coverage_col: str,
    title: str,
    key: str,
    target_col: str | None = None,
    vaccinated_col: str | None = None,
    remaining_col: str | None = None,
    color_scale: str | list = "RdYlGn",
    range_color: tuple[float, float] | list[float] | None = (0, 100),
    value_format: str = ".1f",
    value_suffix: str = "%",
    hover_format: str = ":.1f",
    colorbar_title: str = "Coverage %",
    map_opacity: float = 0.70,
    show_zero_target_as_na: bool = False,
) -> None:
    """Render an Abra municipality choropleth with robust accent-insensitive joins.

    When ``show_zero_target_as_na`` is enabled and ``target_col`` is supplied,
    municipalities with a confirmed zero target stay visible on the map but are
    shown as not applicable instead of being treated as 0% coverage.
    """
    if summary is None or summary.empty or "Municipality" not in summary.columns:
        st.info("Municipality coverage data are unavailable for the map.")
        return

    abra_geo = fetch_abra_geojson()
    if not abra_geo:
        st.warning("Abra municipality boundary data could not be loaded.")
        return

    geojson = deepcopy(abra_geo)
    for feature in geojson.get("features", []):
        props = feature.setdefault("properties", {})
        props["SBI_Key"] = _geo_key(props.get("Standard_Name", ""))

    map_data = summary.copy()
    map_data["SBI_Key"] = map_data["Municipality"].map(_geo_key)
    map_data[coverage_col] = pd.to_numeric(map_data[coverage_col], errors="coerce")

    zero_target_mask = pd.Series(False, index=map_data.index)
    if show_zero_target_as_na and target_col and target_col in map_data.columns:
        zero_target_mask = pd.to_numeric(map_data[target_col], errors="coerce").fillna(0).le(0)

    # Existing dashboards historically render missing coverage as 0. Preserve that
    # behavior unless a caller explicitly requests zero-target municipalities as N/A.
    if not show_zero_target_as_na:
        map_data[coverage_col] = map_data[coverage_col].fillna(0)

    hover_data: dict[str, object] = {"SBI_Key": False, coverage_col: hover_format}
    for col in [target_col, vaccinated_col, remaining_col]:
        if col and col in map_data.columns:
            hover_data[col] = ":,.0f"

    color_data = map_data.loc[~zero_target_mask].copy() if show_zero_target_as_na else map_data.copy()
    fig = px.choropleth_map(
        color_data,
        geojson=geojson,
        locations="SBI_Key",
        featureidkey="properties.SBI_Key",
        color=coverage_col,
        color_continuous_scale=color_scale,
        range_color=range_color,
        map_style="white-bg",
        zoom=9.2,
        center={"lat": 17.58, "lon": 120.80},
        opacity=map_opacity,
        hover_name="Municipality",
        hover_data=hover_data,
    )

    if show_zero_target_as_na and zero_target_mask.any():
        no_target = map_data.loc[zero_target_mask].copy()
        no_target["Target Status"] = "No G5 target"
        no_target_fig = px.choropleth_map(
            no_target,
            geojson=geojson,
            locations="SBI_Key",
            featureidkey="properties.SBI_Key",
            color="Target Status",
            color_discrete_map={"No G5 target": "#D9D9D9"},
            map_style="white-bg",
            zoom=9.2,
            center={"lat": 17.58, "lon": 120.80},
            opacity=map_opacity,
            hover_name="Municipality",
            hover_data={
                "SBI_Key": False,
                "Target Status": True,
                **({target_col: ":,.0f"} if target_col and target_col in no_target.columns else {}),
            },
        )
        for trace in no_target_fig.data:
            trace.showlegend = False
            fig.add_trace(trace)

    label_nudges = get_runtime_label_nudges()

    label_lons: list[float] = []
    label_lats: list[float] = []
    label_text: list[str] = []
    by_key = map_data.set_index("SBI_Key")
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        key_value = props.get("SBI_Key", "")
        if key_value not in by_key.index:
            continue
        row = by_key.loc[key_value]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        lon, lat = get_polygon_centroid(feature.get("geometry", {}))
        if lon is None or lat is None:
            continue

        nudge = get_label_nudge(label_nudges, key_value)
        lat += nudge["lat"]
        lon += nudge["lon"]

        label_lons.append(lon)
        label_lats.append(lat)
        coverage_value = pd.to_numeric(pd.Series([row[coverage_col]]), errors="coerce").iloc[0]
        target_is_zero = False
        if show_zero_target_as_na and target_col and target_col in row.index:
            target_value = pd.to_numeric(pd.Series([row[target_col]]), errors="coerce").fillna(0).iloc[0]
            target_is_zero = float(target_value) <= 0
        if target_is_zero or pd.isna(coverage_value):
            label_text.append(f"{row['Municipality']}<br>N/A")
        else:
            label_value = format(float(coverage_value), value_format)
            label_text.append(f"{row['Municipality']}<br>{label_value}{value_suffix}")

    if label_lons:
        fig.add_trace(go.Scattermap(
            lon=label_lons,
            lat=label_lats,
            mode="text",
            text=label_text,
            textfont=dict(size=9, color="black"),
            hoverinfo="skip",
            showlegend=False,
        ))

    fig.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=600,
        coloraxis_colorbar=dict(title=colorbar_title),
        map=dict(
            layers=[
                dict(
                    sourcetype="raster",
                    source=[
                        "https://server.arcgisonline.com/ArcGIS/rest/services/"
                        "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
                    ],
                    below="traces",
                )
            ]
        ),
    )
    st.markdown(
        f'''<h4 style="margin-bottom:0.25rem;">
        <i class="fa-solid fa-map" style="color:#0033A0; margin-right:8px;"></i>
        {title}
        </h4>''',
        unsafe_allow_html=True,
    )
    st.plotly_chart(
        fig,
        width="stretch",
        key=key,
        config={
            "scrollZoom": False,
            "displayModeBar": True,
            "toImageButtonOptions": {"format": "png", "filename": key, "scale": 3},
        },
    )



def render_barangay_choropleth(
    summary: pd.DataFrame,
    municipality: str,
    coverage_col: str,
    title: str,
    key: str,
    barangay_col: str = "Barangay",
    target_col: str | None = None,
    vaccinated_col: str | None = None,
    remaining_col: str | None = None,
    color_scale: str | list = "RdYlGn",
    range_color: tuple[float, float] | list[float] | None = (0, 100),
    value_format: str = ".1f",
    value_suffix: str = "%",
    hover_format: str = ":.1f",
    colorbar_title: str = "Coverage %",
    map_opacity: float = 0.72,
    show_zero_target_as_na: bool = True,
) -> None:
    '''Render a barangay-level choropleth for one Abra municipality.

    All barangay polygons from ``abra_barangays.geojson`` remain visible.
    Barangays with a confirmed zero target can be shown as N/A while
    positive-target barangays with no accomplishments remain at 0% coverage.
    '''
    if not municipality:
        st.info("Select a municipality to view the barangay choropleth map.")
        return

    brgy_geo = fetch_barangay_geojson(municipality)
    if not brgy_geo:
        st.warning(f"Barangay boundary data for {municipality} could not be loaded.")
        return

    base_rows: list[dict[str, str]] = []
    geojson = deepcopy(brgy_geo)
    for feature in geojson.get("features", []):
        props = feature.setdefault("properties", {})
        join_key = str(props.get("Standard_Name", "") or "").strip()
        display_name = str(props.get("Original_Name", "") or join_key).strip().title()
        props["SBI_Barangay_Key"] = join_key
        base_rows.append({"SBI_Barangay_Key": join_key, "Barangay": display_name})

    if not base_rows:
        st.warning(f"Barangay boundary data for {municipality} contain no usable polygons.")
        return

    base = pd.DataFrame(base_rows).drop_duplicates("SBI_Barangay_Key")

    if summary is None or summary.empty or barangay_col not in summary.columns:
        data = base.copy()
    else:
        work = summary.copy()
        work["SBI_Barangay_Key"] = work[barangay_col].map(_barangay_key)
        work = work.loc[work["SBI_Barangay_Key"].ne("")].copy()
        if work["SBI_Barangay_Key"].duplicated().any():
            agg: dict[str, str] = {}
            for col in work.columns:
                if col in {"SBI_Barangay_Key", barangay_col, coverage_col}:
                    continue
                if col in {target_col, vaccinated_col, remaining_col}:
                    agg[col] = "sum"
                else:
                    agg[col] = "first"
            grouped = work.groupby("SBI_Barangay_Key", as_index=False).agg(agg) if agg else work[["SBI_Barangay_Key"]].drop_duplicates()
            work = grouped
        else:
            work = work.drop(columns=[barangay_col], errors="ignore")
        data = base.merge(work, on="SBI_Barangay_Key", how="left")

    numeric_fields = [coverage_col, target_col, vaccinated_col, remaining_col]
    for col in [c for c in numeric_fields if c and c in data.columns]:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    if coverage_col not in data.columns:
        data[coverage_col] = np.nan

    zero_target_mask = pd.Series(False, index=data.index)
    if show_zero_target_as_na and target_col and target_col in data.columns:
        target_values = pd.to_numeric(data[target_col], errors="coerce").fillna(0)
        zero_target_mask = target_values.le(0)

    if target_col and target_col in data.columns and vaccinated_col and vaccinated_col in data.columns:
        target_values = pd.to_numeric(data[target_col], errors="coerce").fillna(0)
        vaccinated_values = pd.to_numeric(data[vaccinated_col], errors="coerce").fillna(0)
        calculated = vaccinated_values.div(target_values.mask(target_values.le(0))).mul(100)
        data[coverage_col] = pd.to_numeric(data[coverage_col], errors="coerce").fillna(calculated)

    if not show_zero_target_as_na:
        data[coverage_col] = pd.to_numeric(data[coverage_col], errors="coerce").fillna(0)

    hover_data: dict[str, object] = {"SBI_Barangay_Key": False, coverage_col: hover_format}
    for col in [target_col, vaccinated_col, remaining_col]:
        if col and col in data.columns:
            hover_data[col] = ":,.0f"

    color_data = data.loc[~zero_target_mask].copy() if show_zero_target_as_na else data.copy()

    all_lons: list[float] = []
    all_lats: list[float] = []
    for feature in geojson.get("features", []):
        lon, lat = get_polygon_centroid(feature.get("geometry", {}))
        if lon is not None and lat is not None:
            all_lons.append(lon)
            all_lats.append(lat)
    center = {
        "lat": float(np.mean(all_lats)) if all_lats else 17.58,
        "lon": float(np.mean(all_lons)) if all_lons else 120.80,
    }

    if color_data.empty:
        fig = go.Figure()
    else:
        fig = px.choropleth_map(
            color_data,
            geojson=geojson,
            locations="SBI_Barangay_Key",
            featureidkey="properties.SBI_Barangay_Key",
            color=coverage_col,
            color_continuous_scale=color_scale,
            range_color=range_color,
            map_style="white-bg",
            zoom=11.3,
            center=center,
            opacity=map_opacity,
            hover_name="Barangay",
            hover_data=hover_data,
        )

    if show_zero_target_as_na and zero_target_mask.any():
        no_target = data.loc[zero_target_mask].copy()
        no_target["Target Status"] = "No target / N/A"
        no_target_fig = px.choropleth_map(
            no_target,
            geojson=geojson,
            locations="SBI_Barangay_Key",
            featureidkey="properties.SBI_Barangay_Key",
            color="Target Status",
            color_discrete_map={"No target / N/A": "#D9D9D9"},
            map_style="white-bg",
            zoom=11.3,
            center=center,
            opacity=map_opacity,
            hover_name="Barangay",
            hover_data={
                "SBI_Barangay_Key": False,
                "Target Status": True,
                **({target_col: ":,.0f"} if target_col and target_col in no_target.columns else {}),
            },
        )
        for trace in no_target_fig.data:
            trace.showlegend = False
            fig.add_trace(trace)

    label_lons: list[float] = []
    label_lats: list[float] = []
    label_text: list[str] = []
    by_key = data.set_index("SBI_Barangay_Key")
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        join_key = props.get("SBI_Barangay_Key", "")
        if join_key not in by_key.index:
            continue
        row = by_key.loc[join_key]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        lon, lat = get_polygon_centroid(feature.get("geometry", {}))
        if lon is None or lat is None:
            continue
        label_lons.append(lon)
        label_lats.append(lat)
        coverage_value = pd.to_numeric(pd.Series([row.get(coverage_col)]), errors="coerce").iloc[0]
        target_is_zero = False
        if show_zero_target_as_na and target_col and target_col in row.index:
            target_value = pd.to_numeric(pd.Series([row.get(target_col)]), errors="coerce").fillna(0).iloc[0]
            target_is_zero = float(target_value) <= 0
        if target_is_zero or pd.isna(coverage_value):
            label_text.append(f"{row['Barangay']}<br>N/A")
        else:
            label_text.append(f"{row['Barangay']}<br>{format(float(coverage_value), value_format)}{value_suffix}")

    if label_lons:
        font_size = 8 if len(label_lons) > 20 else 9
        fig.add_trace(go.Scattermap(
            lon=label_lons,
            lat=label_lats,
            mode="text",
            text=label_text,
            textfont=dict(size=font_size, color="black"),
            hoverinfo="skip",
            showlegend=False,
        ))

    fig.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=600,
        coloraxis_colorbar=dict(title=colorbar_title),
        map=dict(
            style="white-bg",
            zoom=11.3,
            center=center,
            layers=[
                dict(
                    sourcetype="raster",
                    source=[
                        "https://server.arcgisonline.com/ArcGIS/rest/services/"
                        "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
                    ],
                    below="traces",
                )
            ]
        ),
    )
    st.markdown(
        f'''<h4 style="margin-bottom:0.25rem;">
        <i class="fa-solid fa-map" style="color:#0033A0; margin-right:8px;"></i>
        {title}
        </h4>''',
        unsafe_allow_html=True,
    )
    st.plotly_chart(
        fig,
        width="stretch",
        key=key,
        config={
            "scrollZoom": False,
            "displayModeBar": True,
            "toImageButtonOptions": {"format": "png", "filename": key, "scale": 3},
        },
    )

def render_raw_export(
    events: pd.DataFrame,
    title: str,
    filename: str,
    key: str,
) -> None:
    if events is None or events.empty:
        return
    with st.expander(title, expanded=False):
        export = events.copy().sort_values(
            [c for c in ["Report Date", "Municipality", "School Name"] if c in events.columns],
            ascending=True,
        )
        st.dataframe(export, width="stretch", hide_index=True)
        st.download_button(
            label="Download Data (CSV)",
            data=export.to_csv(index=False).encode("utf-8-sig"),
            file_name=filename,
            mime="text/csv",
            key=key,
        )
        


        