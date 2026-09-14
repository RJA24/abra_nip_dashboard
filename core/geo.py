"""Geographic boundary loading and normalization helpers."""

import json
import logging
import os
import re
import unicodedata

import numpy as np
import pandas as pd
import requests
import streamlit as st

from .config import BARANGAY_GEOJSON_PATH

logger = logging.getLogger("abra_nip_dashboard.geo")

@st.cache_data(ttl="24h")
def fetch_abra_geojson():
    urls = [
        "https://raw.githubusercontent.com/macoymejia/geojsonph/master/MuniCities/MuniCities.json",
        "https://raw.githubusercontent.com/faeldon/philippines-json-maps/master/2023/geojson/municities-lowres.json"
    ]
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'} 
    
    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                abra_features = []
                
                for feature in data.get('features', []):
                    props = feature.get('properties', {})
                    # Convert all property values to uppercase to easily search them
                    props_upper = {str(k).upper(): str(v).upper() for k, v in props.items()}
                    
                    # If ANY of the properties say "ABRA", this shape belongs to us
                    if 'ABRA' in props_upper.values():
                        # Standard keys where municipality names are usually hidden
                        muni_keys = ['ADM3_EN', 'NAME_3', 'MUN_NAME', 'NAME_2', 'MUNICIPALITY']
                        muni_name = ""
                        
                        for k in muni_keys:
                            if k in props_upper and props_upper[k] not in ['ABRA', 'PHILIPPINES']:
                                muni_name = props_upper[k]
                                break
                                
                        clean_name = str(muni_name).strip().upper()
                        
                        # Inject the clean name explicitly into properties so Plotly can guarantee a match
                        feature['properties']['Standard_Name'] = clean_name
                        abra_features.append(feature)
                
                if abra_features:
                    return {"type": "FeatureCollection", "features": abra_features}
        except Exception:
            logger.exception("Abra municipality GeoJSON source failed: %s", url)
            continue 
            
    return None


@st.cache_data(ttl="24h")
def fetch_barangay_geojson(target_muni):
    """
    Fetches and prepares local barangay boundaries using the EXACT 
    robust matching engine from the Dengue Surveillance App.
    """
    import os
    import json
    import re
    import unicodedata
    
    ALL_ABRA_MUNICIPALITIES = [
        "BANGUED", "BOLINEY", "BUCAY", "BUCLOC", "DAGUIOMAN", "DANGLAS", "DOLORES",
        "LA PAZ", "LACUB", "LAGANGILANG", "LAGAYAN", "LANGIDEN", "LICUAN-BAAY",
        "LUBA", "MALIBCONG", "MANABO", "PEÑARRUBIA", "PIDIGAN", "PILAR",
        "SALLAPADAN", "SAN ISIDRO", "SAN JUAN", "SAN QUINTIN", "TAYUM", "TINEG",
        "TUBO", "VILLAVICIOSA"
    ]

    def clean_muni_name(raw_name):
        if not isinstance(raw_name, str): return ""
        raw = str(raw_name).upper()
        raw = unicodedata.normalize('NFKD', raw).encode('ASCII', 'ignore').decode('utf-8')
        raw_alpha = re.sub(r'[^A-Z]', '', raw)
        
        # --- THE SAFETY NET ---
        # If it has a direction, it's definitely a barangay, so protect it!
        is_brgy_leak = any(x in raw_alpha for x in ["NORTE", "SUR", "EAST", "WEST", "PROPER", "POBLACION"])
        
        if "LICUAN" in raw_alpha or "BAAY" in raw_alpha: return "LICUAN-BAAY"
        if "PENAR" in raw_alpha or "RUBIA" in raw_alpha: return "PEÑARRUBIA"
        if "PAZ" in raw_alpha: return "LA PAZ"
        
        # --- YOUR RESTORED PREVIOUS CODE (Protected!) ---
        # Only apply these strict rules if it's NOT a barangay
        if not is_brgy_leak:
            if "JUAN" in raw_alpha: return "SAN JUAN"
            if "ISIDRO" in raw_alpha: return "SAN ISIDRO"
            if "QUINTIN" in raw_alpha: return "SAN QUINTIN"
            
        for muni in ALL_ABRA_MUNICIPALITIES:
            if re.sub(r'[^A-Z]', '', muni.replace("Ñ", "N")) in raw_alpha:
                if is_brgy_leak: 
                    continue # Skip it if it's a hijacked barangay
                return muni
                
        return raw_name

    def clean_brgy_name(raw_name):
        if not isinstance(raw_name, str): return ""
        raw = str(raw_name).upper()
        raw = unicodedata.normalize('NFKD', raw).encode('ASCII', 'ignore').decode('utf-8')
        raw = re.sub(r'\(.*?\)', '', raw) 
        raw = raw.replace("BARANGAY", "").replace("BRGY", "").replace("POBLACION", "POB").replace("POB.", "POB")
        return re.sub(r'[^A-Z0-9]', '', raw)

    def get_muni_name_from_props(props):
        keys = ['ADM3_EN', 'MUN_NAME', 'NAME_3', 'MUNICIPALITY']
        upper_props = {str(k).upper(): str(v) for k, v in props.items()}
        for k in keys:
            if k in upper_props:
                std = clean_muni_name(upper_props[k])
                if std in ALL_ABRA_MUNICIPALITIES: return std
        for val in props.values():
            std = clean_muni_name(str(val))
            if std in ALL_ABRA_MUNICIPALITIES: return std
        return None

    def extract_brgy_name(props):
        keys = ['ADM4_EN', 'BGY_NAME', 'BRGY_NAME', 'BARANGAY', 'NAME_4', 'NAME_3']
        upper_props = {str(k).upper(): v for k, v in props.items()}
        for k in keys:
            if k in upper_props: return str(upper_props[k])
        for val in props.values():
            v_str = str(val).upper().strip()
            if v_str not in ["ABRA", "PHILIPPINES"] and clean_muni_name(v_str) not in ALL_ABRA_MUNICIPALITIES:
                if len(v_str) > 2: return v_str
        return "UNKNOWN"

    if not os.path.exists(BARANGAY_GEOJSON_PATH):
        return None
        
    try:
        with open(BARANGAY_GEOJSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            features = []
            target = clean_muni_name(target_muni)
            for feat in data.get('features', []):
                if get_muni_name_from_props(feat.get('properties', {})) == target:
                    raw_brgy = extract_brgy_name(feat.get('properties', {}))
                    feat['properties']['Original_Name'] = str(raw_brgy).title()
                    feat['properties']['Standard_Name'] = clean_brgy_name(raw_brgy)
                    features.append(feat)
            if features: 
                return {"type": "FeatureCollection", "features": features}
            return None
    except Exception:
        logger.exception("Failed to read local barangay GeoJSON")
        return None


@st.cache_data(ttl="24h")
def fetch_car_geojson():
    # Use multiple fallback URLs to guarantee we get the data
    prov_urls = [
        "https://raw.githubusercontent.com/faeldon/philippines-json-maps/master/2023/geojson/provinces-lowres.json",
        "https://raw.githubusercontent.com/macoymejia/geojsonph/master/Province/Provinces.json"
    ]
    muni_urls = [
        "https://raw.githubusercontent.com/faeldon/philippines-json-maps/master/2023/geojson/municities-lowres.json",
        "https://raw.githubusercontent.com/macoymejia/geojsonph/master/MuniCities/MuniCities.json"
    ]
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    car_features = []
    car_provinces = ['ABRA', 'APAYAO', 'BENGUET', 'IFUGAO', 'KALINGA', 'MOUNTAIN PROVINCE', 'MT. PROVINCE']
    
    try:
        # 1. Fetch Provinces (Scanning all property keys safely)
        for url in prov_urls:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                for f in r.json().get('features', []):
                    props = f.get('properties', {})
                    props_upper = {str(k).upper(): str(v).upper() for k, v in props.items()}
                    
                    for p_name in car_provinces:
                        if p_name in props_upper.values():
                            # Standardize Mountain Province
                            clean_name = "Mountain Province" if "MT" in p_name else p_name.title()
                            f['properties']['Standard_Name'] = clean_name
                            car_features.append(f)
                            break
                if car_features:
                    break # Stop looking if we found the provinces
                    
        # 2. Fetch Baguio City (HUC)
        for url in muni_urls:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                for f in r.json().get('features', []):
                    props = f.get('properties', {})
                    props_upper = {str(k).upper(): str(v).upper() for k, v in props.items()}
                    
                    if 'BAGUIO CITY' in props_upper.values() or 'CITY OF BAGUIO' in props_upper.values():
                        f['properties']['Standard_Name'] = 'Baguio City'
                        car_features.append(f)
                        break
                # Check if Baguio was successfully added
                if any(f.get('properties', {}).get('Standard_Name') == 'Baguio City' for f in car_features):
                    break
                    
        if car_features:
            return {"type": "FeatureCollection", "features": car_features}
            
    except Exception:
        logger.exception("Failed to fetch CAR GeoJSON")
        
    return None


def clean_and_process_car_data(df, col_names):
    df['Code'] = df['Code'].astype(str).str.split('.').str[0]
    df = df[df['Code'] != 'nan']
    df = df[df['Code'] != 'None']
    df = df[df['Code'] != '']
    numeric_cols = col_names[2:] 
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    df['Level'] = 'Barangay'
    df.loc[df['Code'].str.endswith('00000000'), 'Level'] = 'Region'
    df.loc[(df['Code'].str.endswith('00000')) & (~df['Code'].str.endswith('00000000')), 'Level'] = 'Province'
    df.loc[(df['Code'].str.endswith('000')) & (~df['Code'].str.endswith('00000')), 'Level'] = 'Municipality'
    
    df['Parent_Province'] = df.apply(lambda row: row['Location'] if row['Level'] == 'Province' else np.nan, axis=1).ffill()
    df['Parent_Municipality'] = df.apply(lambda row: row['Location'] if row['Level'] == 'Municipality' else np.nan, axis=1).ffill()
    df.loc[df['Level'] == 'Region', 'Parent_Province'] = None
    df.loc[df['Level'].isin(['Region', 'Province']), 'Parent_Municipality'] = None
    return df


def get_polygon_centroid(geometry):
    """
    Calculates the central coordinate of a geographic polygon.
    This allows us to accurately place dynamic text labels on the map.
    """
    try:
        coords = []
        if geometry['type'] == 'Polygon':
            for ring in geometry['coordinates']:
                coords.extend(ring)
        elif geometry['type'] == 'MultiPolygon':
            for poly in geometry['coordinates']:
                for ring in poly:
                    coords.extend(ring)
        if not coords:
            return None, None
        
        coords = np.array(coords)
        return float(np.mean(coords[:, 0])), float(np.mean(coords[:, 1]))
    except Exception:
        return None, None


def standardize_geo_names(series):
    """
    Universally cleans and standardizes geographic names to prevent 
    Pandas merge failures due to typos, encoding glitches, or abbreviations.
    """
    # 1. Convert to string, remove outer spaces, and apply Title Case
    s = series.astype(str).str.strip().str.title()
    
    # 2. Fix encoding glitches (tablets often replace an enye with a question mark)
    s = s.str.replace('?', 'ñ', regex=False)
    
    # 3. Fix the awkward capitalization caused by .title() after a special character
    s = s.str.replace('ñA', 'ña', regex=False)
    s = s.str.replace('ñE', 'ñe', regex=False)
    s = s.str.replace('ñI', 'ñi', regex=False)
    s = s.str.replace('ñO', 'ño', regex=False)
    s = s.str.replace('ñU', 'ñu', regex=False)
    
    # 4. Handle erratic "Pob" and "Poblacion" suffixes (e.g., Caupasan (Pob.) -> Caupasan)
    # First, remove parentheticals entirely
    s = s.str.replace(r'\s*\([Pp]ob.*?\)', '', regex=True, case=False)
    
    # Create masks to protect legitimate "Zone X Pob" and exact "Poblacion" names
    mask_zone = s.str.contains(r'^Zone\s*\d+', regex=True, case=False)
    mask_exact_pob = s.str.lower() == 'poblacion'
    
    # Strip trailing " Pob", " Pob.", or " Poblacion" from all other names
    s.loc[~mask_zone & ~mask_exact_pob] = s.loc[~mask_zone & ~mask_exact_pob].str.replace(r'\s+Pob\.?$', '', regex=True, case=False)
    s.loc[~mask_zone & ~mask_exact_pob] = s.loc[~mask_zone & ~mask_exact_pob].str.replace(r'\s+Poblacion$', '', regex=True, case=False)

    # 5. Expand common Philippine local government abbreviations to official full names
    s = s.str.replace(r'\bPob\.\b', 'Poblacion', regex=True)
    s = s.str.replace(r'\bPob\b', 'Poblacion', regex=True)
    s = s.str.replace(r'\bSta\.\b', 'Santa', regex=True)
    s = s.str.replace(r'\bSta\b', 'Santa', regex=True)
    s = s.str.replace(r'\bSto\.\b', 'Santo', regex=True)
    s = s.str.replace(r'\bSto\b', 'Santo', regex=True)
    
    # Final cleanup of any accidental double spaces created during typing
    s = s.str.replace('  ', ' ', regex=False)
    
    return s.str.strip()

