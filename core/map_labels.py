from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
import unicodedata

import pandas as pd
import streamlit as st

from core.config import ABRA_MUNIS
from core.geo import fetch_abra_geojson, get_polygon_centroid


MAP_LABEL_TABLE = "map_label_positions"
SESSION_CACHE_KEY = "_nip_map_label_nudges"


DEFAULT_LABEL_NUDGES = {
    "BANGUED": {"lat": +0.015, "lon": -0.015},
    "BOLINEY": {"lat": -0.015, "lon": -0.015},
    "BUCAY": {"lat": -0.025, "lon": -0.020},
    "BUCLOC": {"lat": 0.000, "lon": 0.000},
    "DAGUIOMAN": {"lat": -0.015, "lon": -0.015},
    "DANGLAS": {"lat": -0.015, "lon": -0.015},
    "DOLORES": {"lat": 0.000, "lon": 0.000},
    "LAPAZ": {"lat": -0.015, "lon": -0.015},
    "LACUB": {"lat": -0.015, "lon": -0.015},
    "LAGANGILANG": {"lat": -0.015, "lon": +0.015},
    "LAGAYAN": {"lat": 0.000, "lon": 0.000},
    "LANGIDEN": {"lat": +0.015, "lon": -0.025},
    "LICUANBAAY": {"lat": -0.015, "lon": -0.015},
    "LUBA": {"lat": 0.000, "lon": 0.000},
    "MALIBCONG": {"lat": 0.000, "lon": 0.000},
    "MANABO": {"lat": -0.005, "lon": -0.020},
    "PENARRUBIA": {"lat": -0.010, "lon": -0.010},
    "PIDIGAN": {"lat": 0.000, "lon": 0.000},
    "PILAR": {"lat": -0.015, "lon": -0.020},
    "SALLAPADAN": {"lat": +0.020, "lon": +0.015},
    "SANISIDRO": {"lat": +0.015, "lon": -0.015},
    "SANJUAN": {"lat": 0.000, "lon": +0.015},
    "SANQUINTIN": {"lat": -0.015, "lon": 0.000},
    "TAYUM": {"lat": -0.015, "lon": 0.000},
    "TINEG": {"lat": -0.060, "lon": -0.025},
    "TUBO": {"lat": +0.060, "lon": +0.025},
    "VILLAVICIOSA": {"lat": -0.020, "lon": +0.015},
}


def normalize_municipality_key(value: object) -> str:
    text = str(value or "").upper().strip()
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII")
    key = re.sub(r"[^A-Z0-9]", "", text)
    aliases = {
        "SALAPADAN": "SALLAPADAN",
        "LICUANBAAYLICUAN": "LICUANBAAY",
        "BANGUEDCAPITAL": "BANGUED",
    }
    return aliases.get(key, key)


def _canonical_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for muni in ABRA_MUNIS:
        raw = str(muni).strip()
        key = normalize_municipality_key(raw)
        display = raw.title()
        if key == "PENARRUBIA":
            display = "Peñarrubia"
        elif key == "LICUANBAAY":
            display = "Licuan-Baay"
        elif key == "SALLAPADAN":
            display = "Sallapadan"
        elif key == "LAPAZ":
            display = "La Paz"
        elif key == "SANISIDRO":
            display = "San Isidro"
        elif key == "SANJUAN":
            display = "San Juan"
        elif key == "SANQUINTIN":
            display = "San Quintin"
        names[key] = display
    return names


CANONICAL_NAMES = _canonical_names()


def canonical_municipality_name(value: object) -> str:
    key = normalize_municipality_key(value)
    return CANONICAL_NAMES.get(key, str(value or "").strip().title())


def get_default_label_nudges() -> dict[str, dict[str, float]]:
    return deepcopy(DEFAULT_LABEL_NUDGES)


def get_label_nudge(
    label_nudges: dict[str, dict[str, float]] | None,
    municipality: object,
) -> dict[str, float]:
    source = label_nudges or DEFAULT_LABEL_NUDGES
    key = normalize_municipality_key(municipality)
    nudge = source.get(key, {"lat": 0.0, "lon": 0.0})
    return {
        "lat": float(nudge.get("lat", 0.0) or 0.0),
        "lon": float(nudge.get("lon", 0.0) or 0.0),
    }


def table_available(supabase) -> bool:
    try:
        supabase.table(MAP_LABEL_TABLE).select("municipality_key").limit(1).execute()
        return True
    except Exception:
        return False


def load_label_nudges(supabase) -> dict[str, dict[str, float]]:
    nudges = get_default_label_nudges()
    try:
        response = supabase.table(MAP_LABEL_TABLE).select(
            "municipality_key,lat_nudge,lon_nudge"
        ).execute()
        for row in response.data or []:
            key = normalize_municipality_key(row.get("municipality_key"))
            if not key:
                continue
            nudges[key] = {
                "lat": float(row.get("lat_nudge") or 0.0),
                "lon": float(row.get("lon_nudge") or 0.0),
            }
    except Exception:
        pass
    return nudges


def get_runtime_label_nudges(supabase=None, force: bool = False) -> dict[str, dict[str, float]]:
    if not force and SESSION_CACHE_KEY in st.session_state:
        return deepcopy(st.session_state[SESSION_CACHE_KEY])

    if supabase is None:
        try:
            from core.data import init_supabase

            supabase = init_supabase()
        except Exception:
            nudges = get_default_label_nudges()
            st.session_state[SESSION_CACHE_KEY] = nudges
            return deepcopy(nudges)

    nudges = load_label_nudges(supabase)
    st.session_state[SESSION_CACHE_KEY] = nudges
    return deepcopy(nudges)


def invalidate_runtime_label_cache() -> None:
    st.session_state.pop(SESSION_CACHE_KEY, None)


def _geo_feature_index() -> dict[str, dict]:
    geojson = fetch_abra_geojson()
    if not geojson:
        return {}

    index: dict[str, dict] = {}
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        key = normalize_municipality_key(props.get("Standard_Name", ""))
        if key:
            index[key] = feature
    return index


def build_label_records(supabase) -> pd.DataFrame:
    features = _geo_feature_index()
    nudges = load_label_nudges(supabase)

    saved: dict[str, dict] = {}
    try:
        response = supabase.table(MAP_LABEL_TABLE).select("*").execute()
        for row in response.data or []:
            key = normalize_municipality_key(row.get("municipality_key"))
            if key:
                saved[key] = row
    except Exception:
        pass

    records: list[dict] = []
    for key, display_name in CANONICAL_NAMES.items():
        feature = features.get(key)
        if not feature:
            continue

        lon, lat = get_polygon_centroid(feature.get("geometry", {}))
        if lon is None or lat is None:
            continue

        row = saved.get(key, {})
        nudge = nudges.get(key, {"lat": 0.0, "lon": 0.0})

        label_lat = row.get("label_lat")
        label_lon = row.get("label_lon")
        if label_lat is None:
            label_lat = float(lat) + float(nudge.get("lat", 0.0))
        if label_lon is None:
            label_lon = float(lon) + float(nudge.get("lon", 0.0))

        lat_nudge = float(label_lat) - float(lat)
        lon_nudge = float(label_lon) - float(lon)

        records.append(
            {
                "municipality_key": key,
                "municipality_name": display_name,
                "centroid_lat": float(lat),
                "centroid_lon": float(lon),
                "label_lat": float(label_lat),
                "label_lon": float(label_lon),
                "lat_nudge": lat_nudge,
                "lon_nudge": lon_nudge,
                "source": "Saved" if key in saved else "Default",
            }
        )

    result = pd.DataFrame(records)
    if result.empty:
        return result
    return result.sort_values("municipality_name").reset_index(drop=True)


def save_label_records(supabase, records: pd.DataFrame, updated_by: str) -> None:
    if records is None or records.empty:
        return

    now = datetime.now(timezone.utc).isoformat()
    payload = []
    for _, row in records.iterrows():
        key = normalize_municipality_key(row.get("municipality_key"))
        if not key:
            continue
        payload.append(
            {
                "municipality_key": key,
                "municipality_name": canonical_municipality_name(key),
                "label_lat": float(row["label_lat"]),
                "label_lon": float(row["label_lon"]),
                "lat_nudge": float(row["lat_nudge"]),
                "lon_nudge": float(row["lon_nudge"]),
                "updated_at": now,
                "updated_by": str(updated_by or "System Admin"),
            }
        )

    if payload:
        supabase.table(MAP_LABEL_TABLE).upsert(payload).execute()
    invalidate_runtime_label_cache()


def reset_label_position(supabase, municipality_key: str) -> None:
    key = normalize_municipality_key(municipality_key)
    if not key:
        return
    supabase.table(MAP_LABEL_TABLE).delete().eq("municipality_key", key).execute()
    invalidate_runtime_label_cache()


def reset_all_label_positions(supabase) -> None:
    supabase.table(MAP_LABEL_TABLE).delete().neq("municipality_key", "").execute()
    invalidate_runtime_label_cache()
