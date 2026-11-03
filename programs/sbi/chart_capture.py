"""Collect existing SBI Plotly chart figures for selectable PNG reporting.

Streamlit displays all tabs on each run; capture the exact Plotly trace data while
rendering so Reporting Center can offer every on-screen SBI chart without cloning
its query or arithmetic.  Server-side PNG rendering uses Pillow, so no Chrome or
Kaleido dependency is needed.  It reproduces figures by *data and chart type*,
not pixel-for-pixel Plotly screenshots (particularly the geographic maps).
"""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
from html import unescape
from io import BytesIO
from math import ceil, isfinite
import re

import numpy as np
import streamlit as st
from PIL import Image, ImageDraw

from programs.sbi.reporting_bundle import (
    _BG, _NAVY, _TEAL, _MUTED, _GRID, _BLUE, _ORANGE,
    _font, _txt, _nice_tick, _fmt_count,
)

_CAPTURE_ACTIVE: ContextVar[dict | None] = ContextVar("sbi_chart_capture", default=None)
_ORIGINAL = getattr(st.plotly_chart, "_sbi_capture_original", st.plotly_chart)
_PALETTE = ["#17688b", "#ec722c", "#49a37a", "#9b71b4", "#d8a346", "#3a8495"]


def _clear_html(value: object) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).replace("\n", " ").strip()


def _chart_label(key: str, figure) -> str:
    """Prefer the chart's own title, fall back to its stable Streamlit key."""
    overrides = {
        "sbi_exec_gauge_mr": "Executive Summary — MR coverage gauge",
        "sbi_exec_gauge_td": "Executive Summary — Td coverage gauge",
        "sbi_exec_gauge_hpv": "Executive Summary — HPV coverage gauge",
        "sbi_exec_geo_coverage": "Executive Summary — Coverage by municipality",
        "sbi_exec_school_coverage": "Executive Summary — Coverage by school",
        "sbi_tgt_geo_bar": "Baseline targets by municipality",
        "sbi_actual_reporting_status_chart": "Actual targets — RHU reporting status",
        "sbi_actual_geo_chart": "Actual targets by municipality",
        "sbi_compare_grade_chart": "Baseline vs actual — by grade",
        "sbi_compare_geo_chart": "Baseline vs actual — by municipality",
        "sbi_hpv_geo": "VaccTrack — Grade 4 HPV municipality coverage",
        "sbi_hpv_trend": "VaccTrack — Grade 4 HPV cumulative trend",
        "sbi_def_ref_summary": "VaccTrack — Deferrals and refusals",
        "sbi_def_ref_reasons": "VaccTrack — Missed reasons",
        "sbi_def_ref_geo": "VaccTrack — Missed by municipality",
        "sbi_g5_coverage_by_municipality": "G5 — Coverage by municipality",
        "sbi_g5_cumulative": "G5 — Cumulative trend",
        "sbi_workbook_hpv_geo": "Workbook — HPV coverage by municipality",
        "sbi_workbook_hpv_trend": "Workbook — HPV cumulative trend",
        "sbi_workbook_def_ref_summary": "Workbook — Deferrals and refusals",
        "sbi_workbook_def_ref_reasons": "Workbook — Missed reasons",
        "sbi_workbook_def_ref_geo": "Workbook — Missed by municipality",
    }
    if key in overrides:
        return overrides[key]
    try:
        title = _clear_html(figure.layout.title.text)
    except Exception:
        title = ""
    if title:
        title = re.sub(r"\s+", " ", title)
        return f"{title[:90]} [{key.replace('_', ' ')}]"
    return key.replace("sbi_", "").replace("_", " ").strip().title()


def _capture_plotly_chart(figure_or_data, *args, **kwargs):
    collector = _CAPTURE_ACTIVE.get()
    if collector is not None:
        try:
            import plotly.graph_objects as go
            fig = figure_or_data if isinstance(figure_or_data, go.Figure) else go.Figure(figure_or_data)
            key = str(kwargs.get("key") or f"sbi_chart_{len(collector)}")
            config = kwargs.get("config") or {}
            # Only charts with an image download in Plotly's mode bar qualify.
            supported = {"bar", "pie", "indicator", "choroplethmap", "choroplethmapbox", "choropleth", "scatter", "scattergl"}
            if (not (isinstance(config, dict) and config.get("displayModeBar") is False)
                    and any(t.type in supported for t in fig.data)):
                collector[key] = {"figure": fig, "label": _chart_label(key, fig)}
        except Exception:
            # Chart capture can NEVER prevent a dashboard chart from displaying.
            pass
    return _ORIGINAL(figure_or_data, *args, **kwargs)


# Install a single dispatch wrapper for all sessions. ContextVar keeps each
# Streamlit run isolated without swapping a global function at render time.
_capture_plotly_chart._sbi_capture_wrapper = True
_capture_plotly_chart._sbi_capture_original = _ORIGINAL
st.plotly_chart = _capture_plotly_chart


def capture_sbi_charts(render):
    """Scope chart recording to one SBI render, without cross-session patching."""
    @wraps(render)
    def wrapped(*args, **kwargs):
        collected: dict = {}
        token = _CAPTURE_ACTIVE.set(collected)
        try:
            return render(*args, **kwargs)
        finally:
            _CAPTURE_ACTIVE.reset(token)
            # Keep latest chart state for optional inspection; the Reporting
            # Center at the end of this same run uses the current collection.
            if collected:
                st.session_state["sbi_png_figures"] = collected
    return wrapped


def _values(v):
    if v is None:
        return []
    if isinstance(v, (tuple, list, np.ndarray)):
        return list(v)
    return [v]


def _numeric(v) -> float:
    try:
        x = float(v)
        return x if isfinite(x) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _color(value, fallback: str):
    """Convert Plotly CSS colors into Pillow-safe solid colors."""
    if not isinstance(value, str):
        return fallback
    if value.startswith("#"):
        return value
    match = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*[\d.]+)?\s*\)", value)
    if match:
        return tuple(max(0, min(255, int(x))) for x in match.groups())
    return fallback


def _save(im):
    data = BytesIO()
    im.save(data, format="PNG", dpi=(300, 300), optimize=True)
    return data.getvalue()


def _title_header(title: str, scope: str, period: str, width: int, height: int):
    im = Image.new("RGB", (width, height), _BG)
    d = ImageDraw.Draw(im)
    _txt(d, title, 68, 39, size=37, bold=True, max_width=width-136)
    _txt(d, f"Scope: {scope}  |  Reporting period: {period}", 68, 103, size=24, color=_MUTED, max_width=width-136)
    return im, d


def _bar_plot(fig, title: str, scope: str, period: str) -> bytes:
    traces = [t for t in fig.data if t.type == "bar"]
    horiz = str(traces[0].orientation or "v") == "h"
    cats: list[str] = []
    trace_data = []
    for index, t in enumerate(traces):
        names = _values(t.y if horiz else t.x)
        values = _values(t.x if horiz else t.y)
        pairs = []
        for nm, v in zip(names, values):
            cat = str(nm)
            if cat not in cats:
                cats.append(cat)
            pairs.append((cat, _numeric(v)))
        color = _color(getattr(t.marker, "color", None), _PALETTE[index % len(_PALETTE)])
        trace_data.append((str(t.name or f"Series {index+1}"), pairs, color))
    if not cats:
        return _empty_image(title, scope, period, "No plottable categories")
    # Plotly categoryarray has ordering precedence when explicitly set.
    try:
        order = list(fig.layout.yaxis.categoryarray if horiz else fig.layout.xaxis.categoryarray)
        if order:
            cats = [str(x) for x in order if str(x) in cats] + [x for x in cats if x not in set(map(str,order))]
    except (TypeError, AttributeError):
        pass
    n = len(cats)
    width = 2600
    height = max(860, 350 + n * (65 if horiz else 30)) if horiz else 1140
    height = min(height, 7500)
    im, d = _title_header(title, scope, period, width, height)
    x0, x1, y0, y1 = (475, width - 110, 290, height - 130) if horiz else (190, width - 110, 290, height-155)
    all_values = {cat: [0.0]*len(trace_data) for cat in cats}
    for j, (_, pairs, _) in enumerate(trace_data):
        for cat, value in pairs:
            if cat in all_values: all_values[cat][j] += max(0, value)
    stacked = getattr(fig.layout, "barmode", "group") in ("stack", "relative")
    vmax = max([sum(x) if stacked else max(x) for x in all_values.values()] or [1.0])
    vmax = max(vmax, 1.0)
    tick = _nice_tick(vmax)
    xmax = max(tick, ceil(vmax/tick)*tick)
    # Legend for each series.
    legend_x = x0
    for name, _, color in trace_data:
        if legend_x > width - 340: break
        d.rectangle((legend_x, 222, legend_x+18, 240), fill=color)
        _txt(d, name[:35], legend_x+26, 219, size=20)
        legend_x += max(180, len(name[:35])*13+60)
    for k in range(int(xmax / tick) + 1):
        value = k*tick
        pos = (x0 + round((x1-x0)*value/xmax)) if horiz else (y1-round((y1-y0)*value/xmax))
        if horiz:
            d.line((pos,y0,pos,y1), fill=_GRID, width=2)
            _txt(d, f"{value:g}", pos, y1+22, size=20, anchor="ma", color=_MUTED)
        else:
            d.line((x0,pos,x1,pos), fill=_GRID, width=2)
            _txt(d, f"{value:g}", x0-22, pos, size=20, anchor="rm", color=_MUTED)
    pitch = (y1-y0)/max(n,1) if horiz else (x1-x0)/max(n,1)
    for i, cat in enumerate(cats):
        if horiz:
            cy = round(y0 + (i+0.5)*pitch)
            _txt(d, cat[:55], x0-23, cy, size=20, anchor="rm", color=_MUTED, max_width=x0-40)
            cursor = x0
            for j, (_, _, color) in enumerate(trace_data):
                value = all_values[cat][j]
                if value <= 0: continue
                if stacked:
                    w = round((x1-x0)*value/xmax)
                    top, bottom = round(cy - pitch*0.31), round(cy+pitch*0.31)
                    left, right = cursor, cursor+w
                    cursor += w
                else:
                    left=x0; right=x0+round((x1-x0)*value/xmax)
                    top=round(cy-pitch*0.38+(j/len(trace_data))*pitch*.76)
                    bottom=round(cy-pitch*0.38+((j+1)/len(trace_data))*pitch*.76-2)
                if right>left and bottom>top:
                    d.rectangle((left,top,right,bottom),fill=color)
                    if right-left>88:
                        _txt(d, f"{value:,.0f}", (left+right)//2,(top+bottom)//2,size=18,bold=True,color="white",anchor="mm")
                    else:
                        _txt(d, f"{value:,.0f}",min(x1,right+8),max(top,y0),size=18,color=_TEAL)
        else:
            cx=round(x0+(i+0.5)*pitch)
            cursor=y1
            for j, (_, _, color) in enumerate(trace_data):
                v=all_values[cat][j]
                if v<=0: continue
                w=round((y1-y0)*v/xmax)
                if stacked:
                    top=cursor-w; bottom=cursor; cursor-=w
                    left=round(cx-pitch*.29);right=round(cx+pitch*.29)
                else:
                    top=y1-w;bottom=y1
                    left=round(cx-pitch*.40+(j/len(trace_data))*pitch*.80)
                    right=round(cx-pitch*.40+((j+1)/len(trace_data))*pitch*.80-2)
                if right>left and bottom>top:
                    d.rectangle((left,top,right,bottom),fill=color)
            if n<=30:
                _txt(d, cat[:17], cx, y1+17, size=17, color=_MUTED,anchor="ma")
    return _save(im)


def _line_plot(fig, title: str, scope: str, period: str) -> bytes:
    traces = [t for t in fig.data if t.type in ("scatter", "scattergl")]
    width, height = 2600, 1170
    im,d = _title_header(title, scope, period, width, height)
    x0,y0,x1,y1 = 180,285,width-100,height-150
    series=[]; xs=[]; ys=[]
    for i,t in enumerate(traces):
        x=_values(t.x);y=_values(t.y)
        points=[(str(a),_numeric(b)) for a,b in zip(x,y) if b is not None]
        xs.extend([a for a,b in points]); ys.extend([b for a,b in points])
        color=_color(getattr(t.line,"color",None),_PALETTE[i%len(_PALETTE)])
        series.append((str(t.name or f"Series {i+1}"),points,color))
    if not xs: return _empty_image(title,scope,period,"No series data")
    categories=list(dict.fromkeys(xs)); indices={x:i for i,x in enumerate(categories)}
    max_y=max(ys+[0]);min_y=min(ys+[0]); max_y=max(1.0,max_y)
    rng=max_y-min_y or 1
    for i in range(6):
        v=min_y+i*rng/5; yy=round(y1-(y1-y0)*(v-min_y)/rng)
        d.line((x0,yy,x1,yy),fill=_GRID,width=2)
        _txt(d,f"{v:,.0f}",x0-20,yy,anchor="rm",size=19,color=_MUTED)
    mark_step=max(1,ceil(len(categories)/12))
    for i,c in enumerate(categories):
        if i % mark_step:continue
        xx=x0+(x1-x0)*i/max(len(categories)-1,1)
        _txt(d,c[:15],round(xx),y1+21,size=17,anchor="ma",color=_MUTED)
    legend_x=x0
    for name,points,color in series:
        d.line((legend_x,205,legend_x+35,205),fill=color,width=7)
        _txt(d,name[:35],legend_x+44,194,size=21)
        legend_x+=max(200,17*len(name[:35])+75)
        pts=[(round(x0+(x1-x0)*indices[a]/max(len(categories)-1,1)),round(y1-(y1-y0)*(b-min_y)/rng)) for a,b in points]
        if len(pts)>1:d.line(pts,fill=color,width=6,joint="curve")
        for x,y in pts: d.ellipse((x-5,y-5,x+5,y+5),fill=color)
    return _save(im)


def _pie_plot(fig,title,scope,period):
    t=next(t for t in fig.data if t.type=="pie")
    labs=[str(v) for v in _values(t.labels)];vals=[max(0,_numeric(v)) for v in _values(t.values)]
    total=sum(vals)
    if total<=0:return _empty_image(title,scope,period,"No nonzero pie values")
    width,height=2200,1180
    im,d=_title_header(title,scope,period,width,height)
    radius=375; x0=150;y0=285
    theta=0
    for i,(lab,val) in enumerate(zip(labs,vals)):
        angle=val/total*360
        color=_PALETTE[i%len(_PALETTE)]
        d.pieslice((x0,y0,x0+radius*2,y0+radius*2),theta,theta+angle,fill=color,outline="white",width=3)
        theta+=angle
        if i<28:
            d.rectangle((1120,270+i*28,1138,288+i*28),fill=color)
            _txt(d,f"{lab[:36]} — {val:,.0f} ({val/total:.1%})",1150,269+i*28,size=20)
    return _save(im)


def _gauge_plot(fig,title,scope,period):
    indicators=[t for t in fig.data if t.type=="indicator"]
    width,height=2100,750
    im,d=_title_header(title,scope,period,width,height)
    for i,t in enumerate(indicators):
        value=_numeric(t.value)
        label=_clear_html(t.title.text) if t.title else title
        _txt(d,label,185,235+i*190,size=36,bold=True)
        x0,y0,x1,y1=185,345+i*190,1850,430+i*190
        d.rounded_rectangle((x0,y0,x1,y1),radius=14,fill="#e6edf3")
        d.rounded_rectangle((x0,y0,x0+round((x1-x0)*min(max(value,0),100)/100),y1),radius=14,fill=_BLUE)
        _txt(d,f"{value:.1f}%",x1+28,(y0+y1)//2,size=40,bold=True,anchor="lm")
    return _save(im)


def _map_plot(fig,title,scope,period):
    """Render the chart's embedded GeoJSON polygons (without external map tiles)."""
    width,height=2200,1550
    im,d=_title_header(title,scope,period,width,height)
    tr=next((t for t in fig.data if t.type in {"choroplethmap","choroplethmapbox","choropleth"}),None)
    if tr is None: return _empty_image(title,scope,period,"Geographic chart has no map trace")
    geo=tr.geojson if isinstance(tr.geojson,dict) else None
    if not geo or "features" not in geo:
        return _empty_image(title,scope,period,"Map geometry unavailable in this chart")
    locations=[str(v) for v in _values(tr.locations)];z=[_numeric(v) for v in _values(tr.z)]
    data=dict(zip(locations,z)); feature_key=str(tr.featureidkey or "id")
    polygons=[]
    def iter_rings(geom):
        ty=geom.get("type"); coords=geom.get("coordinates") or []
        if ty=="Polygon":return coords[:1]
        if ty=="MultiPolygon":return [r[0] for r in coords if r]
        return []
    for ft in geo.get("features",[]):
        ident=ft
        for name in feature_key.split("."):
            if isinstance(ident,dict):ident=ident.get(name)
            else:ident=None
        val=data.get(str(ident))
        for ring in iter_rings(ft.get("geometry") or {}):
            pointlist=[(p[0],p[1]) for p in ring if len(p)>=2]
            if len(pointlist)>=3:
                polygons.append((pointlist,val))
    if not polygons:return _empty_image(title,scope,period,"Map polygons unavailable")
    xx=[x for poly,_ in polygons for x,y in poly]; yy=[y for poly,_ in polygons for x,y in poly]
    left,right,bottom,top=min(xx),max(xx),min(yy),max(yy)
    dx=right-left or 1;dy=top-bottom or 1
    plot_x0,plot_x1,plot_y0,plot_y1=190,1940,220,1400
    scale=min((plot_x1-plot_x0)/dx,(plot_y1-plot_y0)/dy)
    offx=plot_x0+(plot_x1-plot_x0-dx*scale)/2
    offy=plot_y0+(plot_y1-plot_y0-dy*scale)/2
    nums=[val for _,val in polygons if val is not None]; mn=min(nums) if nums else 0;mx=max(nums) if nums else 1
    def col(v):
        if v is None:return "#e2e8ef"
        k=max(0,min(1,(v-mn)/(mx-mn or 1)))
        # Sequential light-to-dark teal, deterministic high-contrast outline.
        a=(216,238,241);b=(15,85,112)
        return tuple(round(a[i]*(1-k)+b[i]*k) for i in range(3))
    for poly,v in polygons:
        pts=[(round(offx+(x-left)*scale),round(offy+(top-y)*scale)) for x,y in poly]
        d.polygon(pts,fill=col(v));d.line(pts+[pts[0]],fill="#758e9e",width=2)
    _txt(d,f"Map values: {mn:,.1f} to {mx:,.1f}  |  Gray = unavailable",200,1435,size=22,color=_MUTED)
    return _save(im)


def _empty_image(title,scope,period,message):
    im,d=_title_header(title,scope,period,2100,670)
    _txt(d,message,130,260,size=32,color=_MUTED)
    return _save(im)


def render_captured_png(record: dict, scope: str, period: str) -> bytes:
    """Export the recorded data for a native SBI chart as a shareable PNG."""
    fig=record["figure"]
    title=record["label"]
    trace_types={t.type for t in fig.data}
    if "bar" in trace_types:return _bar_plot(fig,title,scope,period)
    if "pie" in trace_types:return _pie_plot(fig,title,scope,period)
    if "indicator" in trace_types:return _gauge_plot(fig,title,scope,period)
    if trace_types & {"choroplethmap","choroplethmapbox","choropleth"}:
        return _map_plot(fig,title,scope,period)
    if "scatter" in trace_types or "scattergl" in trace_types:
        return _line_plot(fig,title,scope,period)
    raise ValueError(f"Unsupported chart type for {title}: {sorted(trace_types)}")


def get_captured_reports() -> dict:
    """Prefer the charts already captured in this render; otherwise last render."""
    current = _CAPTURE_ACTIVE.get()
    return current if current is not None else st.session_state.get("sbi_png_figures", {})
