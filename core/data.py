"""Cached data-access layer for Supabase and Google Sheets.

Network failures are allowed to escape cached functions so transient failures are not cached as successful empty reads. Public wrappers convert failures to graceful empty DataFrames and log details.
"""

import logging
import time

import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from supabase import create_client

from .config import SIA_SHEET_URL, SBI_SHEET_URL
from .geo import standardize_geo_names

logger = logging.getLogger("abra_nip_dashboard.data")

@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


@st.cache_data(ttl="1h")
def _fetch_sbi_vacctrack_cached():
    """Fetch SBI VaccTrack sheets. Exceptions escape so Streamlit never caches a failed read."""
    conn = st.connection("gsheets", type=GSheetsConnection)
    df_g1 = conn.read(spreadsheet=SBI_SHEET_URL, worksheet="VaccTrackG1", ttl="1h")
    df_g4 = conn.read(spreadsheet=SBI_SHEET_URL, worksheet="VaccTrackG4", ttl="1h")
    df_g7 = conn.read(spreadsheet=SBI_SHEET_URL, worksheet="VaccTrackG7", ttl="1h")

    if not df_g7.empty and 'Facility Name.1' in df_g7.columns:
        df_g7 = df_g7.rename(columns={'Facility Name.1': 'Updated date'})

    for df in [df_g1, df_g4, df_g7]:
        if not df.empty:
            df.columns = [str(c).strip() for c in df.columns]

    return df_g1, df_g4, df_g7


def fetch_sbi_vacctrack():
    try:
        return _fetch_sbi_vacctrack_cached()
    except Exception:
        logger.exception("Failed to fetch SBI VaccTrack data; failure was not cached")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


@st.cache_data(ttl="1h")
def _fetch_sbi_targets_cached():
    """Fetch all SBI targets. Only successful results are cached."""
    supabase = init_supabase()
    last_error = None
    for attempt in range(3):
        try:
            all_data = []
            offset = 0
            limit = 1000
            while True:
                res = supabase.table('sbi_targets').select('*').range(offset, offset + limit - 1).execute()
                if res.data:
                    all_data.extend(res.data)
                    if len(res.data) < limit:
                        break
                    offset += limit
                else:
                    break

            if not all_data:
                return pd.DataFrame()

            df = pd.DataFrame(all_data)
            col_mapping = {
                'municipality': 'Municipality', 'barangay': 'Barangay', 'school_id': 'School ID',
                'school_name': 'School Name', 'g1_male': 'G1 Male', 'g1_female': 'G1 Female',
                'g4_female': 'G4 Female', 'g7_male': 'G7 Male', 'g7_female': 'G7 Female',
                'g1_total': 'G1 Total', 'g7_total': 'G7 Total'
            }
            df = df.rename(columns=col_mapping)
            if 'Municipality' in df.columns:
                df['Municipality'] = df['Municipality'].astype(str).str.title().str.strip()
            if 'Barangay' in df.columns:
                df['Barangay'] = df['Barangay'].astype(str).str.title().str.strip()
            return df
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1)

    raise RuntimeError("Failed to fetch SBI targets after 3 attempts") from last_error


def fetch_sbi_targets():
    try:
        return _fetch_sbi_targets_cached()
    except Exception:
        logger.exception("Failed to fetch SBI targets; failure was not cached")
        return pd.DataFrame()


@st.cache_data(ttl="5m")
def _fetch_sbi_actual_targets_cached():
    """Fetch RHU-entered school-level actual targets from the SBI Google Sheet.

    Identity fields are kept from columns A-D. Target-entry status is determined
    before blank numeric cells are converted to zero, so a genuine zero entered
    by an RHU still counts as a reported value.
    """
    conn = st.connection("gsheets", type=GSheetsConnection)
    df = conn.read(spreadsheet=SBI_SHEET_URL, worksheet="Actual Targets", ttl="5m")

    if df.empty:
        return pd.DataFrame()

    df.columns = [str(c).strip() for c in df.columns]

    identity_cols = [
        'Municipality', 'Barangay', 'School ID', 'School Name'
    ]
    target_cols = [
        'G1 Male', 'G1 Female', 'G1 Total', 'G4 Female',
        'G7 Male', 'G7 Female', 'G7 Total', 'Total Eligible'
    ]

    # Preserve a stable schema even when an entire target column is still blank.
    for col in identity_cols + target_cols:
        if col not in df.columns:
            df[col] = pd.NA

    # Remove unused blank rows while keeping every school copied from the baseline.
    school_id_raw = df['School ID'].astype('string').str.strip()
    school_name_raw = df['School Name'].astype('string').str.strip()
    valid_school = (school_id_raw.notna() & school_id_raw.ne('')) | (school_name_raw.notna() & school_name_raw.ne(''))
    df = df.loc[valid_school].copy()

    # Clean school identity fields without turning missing cells into the string 'nan'.
    for col in ['Municipality', 'Barangay', 'School Name']:
        df[col] = df[col].astype('string').fillna('').str.strip()
    df['Municipality'] = df['Municipality'].str.title()
    df['Barangay'] = df['Barangay'].str.title()
    df['School ID'] = (
        df['School ID']
        .astype('string')
        .fillna('')
        .str.strip()
        .str.replace(r'\.0$', '', regex=True)
    )

    # Capture which target cells were actually entered before numeric conversion.
    raw_targets = df[target_cols].copy()
    raw_targets = raw_targets.replace(r'^\s*$', pd.NA, regex=True)
    entered_mask = raw_targets.notna()
    entered_count = entered_mask.sum(axis=1)

    df['Target Fields Entered'] = entered_count.astype(int)
    df['Target Entry Status'] = 'Pending'
    df.loc[entered_count.between(1, len(target_cols) - 1), 'Target Entry Status'] = 'Partial'
    df.loc[entered_count.eq(len(target_cols)), 'Target Entry Status'] = 'Complete'
    df['Actual Target Updated'] = entered_count.gt(0)

    # Convert entered targets to numbers. Pending blanks become zero only after the
    # reporting status has been recorded.
    for col in target_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Recalculate totals when detailed sex-disaggregated values were supplied.
    g1_detail_entered = entered_mask['G1 Male'] & entered_mask['G1 Female']
    g7_detail_entered = entered_mask['G7 Male'] & entered_mask['G7 Female']

    calculated_g1 = df['G1 Male'] + df['G1 Female']
    calculated_g7 = df['G7 Male'] + df['G7 Female']

    df.loc[g1_detail_entered, 'G1 Total'] = calculated_g1.loc[g1_detail_entered]
    df.loc[g7_detail_entered, 'G7 Total'] = calculated_g7.loc[g7_detail_entered]

    # Overall total is always derived from the grade-level actual targets so the
    # dashboard stays internally consistent even if the sheet total was omitted.
    df['Total Eligible'] = df['G1 Total'] + df['G4 Female'] + df['G7 Total']

    return df


def fetch_sbi_actual_targets():
    try:
        return _fetch_sbi_actual_targets_cached()
    except Exception:
        logger.exception("Failed to fetch SBI Actual Targets; failure was not cached")
        return pd.DataFrame()


@st.cache_data(ttl="1h")
def _fetch_targets_from_supabase_cached():
    supabase = init_supabase()
    # 🛑 FIX: Added a 3-attempt retry loop to wake up a sleeping Supabase server
    for attempt in range(3):
        try:
            # 🛑 FIX: Bypass the Supabase 1,000 row limit using a pagination loop!
            all_data = []
            offset = 0
            limit = 1000
            
            while True:
                # Fetch chunks of 1000 rows until we secure the entire database
                res = supabase.table('targets').select('*').range(offset, offset + limit - 1).execute()
                
                if res.data:
                    all_data.extend(res.data)
                    # If we receive fewer than 1000 rows, we've hit the end of the database
                    if len(res.data) < limit:
                        break
                    offset += limit
                else:
                    break
                    
            # SAFETY NET: If data is successfully retrieved, process it!
            if all_data and len(all_data) >= 27: 
                df = pd.DataFrame(all_data)
                
                col_mapping = {
                    'code': 'Code', 'location': 'Location', 'level': 'Level',
                    'parent_province': 'Parent_Province', 'parent_municipality': 'Parent_Municipality',
                    'grand_total_6_59m': 'MR_6-59m_Total', 'mr_6_59m_m': 'MR_6-59m_M', 'mr_6_59m_f': 'MR_6-59m_F',
                    'grand_total_6_12m': 'MR_6-12m_Total', 'mr_6_12m_m': 'MR_6-12m_M', 'mr_6_12m_f': 'MR_6-12m_F',
                    'grand_total_13_23m': 'MR_13-23m_Total', 'mr_13_23m_m': 'MR_13-23m_M', 'mr_13_23m_f': 'MR_13-23m_F',
                    'grand_total_24_59m': 'MR_24-59m_Total', 'mr_24_59m_m': 'MR_24-59m_M', 'mr_24_59m_f': 'MR_24-59m_F',
                    'vita_total': 'VitA_Total', 'vita_total_m': 'VitA_Total_M', 'vita_total_f': 'VitA_Total_F',
                    'vita_6_11m': 'VitA_6-11m_Total', 'vita_6_11m_m': 'VitA_6-11m_M', 'vita_6_11m_f': 'VitA_6-11m_F',
                    'vita_12_59m': 'VitA_12-59m_Total', 'vita_12_59m_m': 'VitA_12-59m_M', 'vita_12_59m_f': 'VitA_12-59m_F',
                    # ACTUALS TARGETS
                    'actual_mr_6_59m_total': 'Act_MR_6-59m_Total', 'actual_mr_6_59m_m': 'Act_MR_6-59m_M', 'actual_mr_6_59m_f': 'Act_MR_6-59m_F',
                    'actual_mr_6_12m_total': 'Act_MR_6-12m_Total', 'actual_mr_6_12m_m': 'Act_MR_6-12m_M', 'actual_mr_6_12m_f': 'Act_MR_6-12m_F',
                    'actual_mr_13_23m_total': 'Act_MR_13-23m_Total', 'actual_mr_13_23m_m': 'Act_MR_13-23m_M', 'actual_mr_13_23m_f': 'Act_MR_13-23m_F',
                    'actual_mr_24_59m_total': 'Act_MR_24-59m_Total', 'actual_mr_24_59m_m': 'Act_MR_24-59m_M', 'actual_mr_24_59m_f': 'Act_MR_24-59m_F',
                    'actual_vita_6_11m_total': 'Act_VitA_6-11m_Total', 'actual_vita_6_11m_m': 'Act_VitA_6-11m_M', 'actual_vita_6_11m_f': 'Act_VitA_6-11m_F',
                    'actual_vita_12_59m_total': 'Act_VitA_12-59m_Total', 'actual_vita_12_59m_m': 'Act_VitA_12-59m_M', 'actual_vita_12_59m_f': 'Act_VitA_12-59m_F',
                    'actual_vita_total': 'Act_VitA_Total', 'actual_vita_total_m': 'Act_VitA_Total_M', 'actual_vita_total_f': 'Act_VitA_Total_F'
                }
                
                for db_col in col_mapping.keys():
                    if db_col not in df.columns:
                        df[db_col] = 0
                        
                df = df.rename(columns=col_mapping)
                
                # ==========================================
                # FIX: CLEAN TARGET LOCATIONS
                # ==========================================
                if 'Location' in df.columns:
                    # Pass the raw target locations through the master cleaner
                    df['Location'] = standardize_geo_names(df['Location'])
                    
                    # Keep explicit overrides for completely different spellings
                    df['Location'] = df['Location'].replace({
                        'Salapadan': 'Sallapadan',
                        'Licuan-Baay (Licuan)': 'Licuan-Baay'
                    })
                # ==========================================
                
                num_cols = [c for c in df.columns if c not in ['Code', 'Location', 'Level', 'Parent_Province', 'Parent_Municipality']]
                for c in num_cols:
                    df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

                df['Act_VitA_6-11m_Total'] = df['Act_VitA_6-11m_M'] + df['Act_VitA_6-11m_F']
                df['Act_VitA_12-59m_Total'] = df['Act_VitA_12-59m_M'] + df['Act_VitA_12-59m_F']
                df['Act_VitA_Total_M'] = df['Act_VitA_6-11m_M'] + df['Act_VitA_12-59m_M']
                df['Act_VitA_Total_F'] = df['Act_VitA_6-11m_F'] + df['Act_VitA_12-59m_F']
                df['Act_VitA_Total'] = df['Act_VitA_6-11m_Total'] + df['Act_VitA_12-59m_Total']

                df['VitA_6-11m_Total'] = df['VitA_6-11m_M'] + df['VitA_6-11m_F']
                df['VitA_12-59m_Total'] = df['VitA_12-59m_M'] + df['VitA_12-59m_F']
                df['VitA_Total_M'] = df['VitA_6-11m_M'] + df['VitA_12-59m_M']
                df['VitA_Total_F'] = df['VitA_6-11m_F'] + df['VitA_12-59m_F']
                df['VitA_Total'] = df['VitA_6-11m_Total'] + df['VitA_12-59m_Total']

                return df
                
        except Exception as e:
            # Sleep for 1 second to give Supabase time to wake up, then try again
            time.sleep(1) 

    # A failed cached call must raise; otherwise Streamlit may cache an empty DataFrame for an hour.
    raise RuntimeError("Failed to fetch target database after 3 attempts")


def fetch_targets_from_supabase():
    try:
        return _fetch_targets_from_supabase_cached()
    except Exception:
        logger.exception("Failed to fetch target database; failure was not cached")
        return pd.DataFrame()


@st.cache_data(ttl="1h")
def _fetch_live_accomplishments_cached():
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_mr = conn.read(spreadsheet=SIA_SHEET_URL, worksheet="MR", skiprows=1)
        df_vita = conn.read(spreadsheet=SIA_SHEET_URL, worksheet="VitA", skiprows=1)
        
        if df_mr.empty or df_vita.empty:
            logger.warning("MR or Vitamin A worksheet returned no data")
            return pd.DataFrame(), pd.DataFrame()
            
        # ==========================================
        # CLUTTER FIX: DROP BLANK DROPDOWNS & DATES
        # ==========================================
        if 'Barangay' in df_mr.columns:
            df_mr = df_mr.dropna(subset=['Barangay'])
            df_mr['Barangay'] = standardize_geo_names(df_mr['Barangay'])
            
        if 'Vaccination Date' in df_mr.columns:
            df_mr = df_mr.dropna(subset=['Vaccination Date'])
            
        if 'Barangay' in df_vita.columns:
            df_vita = df_vita.dropna(subset=['Barangay'])
            df_vita['Barangay'] = standardize_geo_names(df_vita['Barangay'])
            
        if 'Vaccination Date' in df_vita.columns:
            df_vita = df_vita.dropna(subset=['Vaccination Date'])
            
        # ==========================================
        #  UNIVERSAL MATH CALCULATION
        # ==========================================
        
        # MR Math: Sum all 6 demographic columns
        if not df_mr.empty:
            mr_cols = ['MR 6-12 Male', 'MR 6-12 Female', 'MR 13-23 Male', 'MR 13-23 Female', 'MR 24-59 Male', 'MR 24-59 Female']
            for c in mr_cols:
                if c in df_mr.columns:
                    df_mr[c] = pd.to_numeric(df_mr[c].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
            # Inject the calculated Total Doses column
            df_mr['Total Doses'] = df_mr[[c for c in mr_cols if c in df_mr.columns]].sum(axis=1)

        # Vit A Math: Sum all 4 demographic columns
        if not df_vita.empty:
            va_cols = ['VitA 6-11 Male', 'VitA 6-11 Female', 'VitA 12-59 Male', 'VitA 12-59 Female']
            for c in va_cols:
                if c in df_vita.columns:
                    df_vita[c] = pd.to_numeric(df_vita[c].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
            # Inject the calculated Total Doses column
            df_vita['Total Doses'] = df_vita[[c for c in va_cols if c in df_vita.columns]].sum(axis=1)

        return df_mr, df_vita
    except Exception:
        logger.exception("Live accomplishment read failed inside cached fetch")
        raise


def fetch_live_accomplishments():
    try:
        return _fetch_live_accomplishments_cached()
    except Exception:
        logger.exception("Failed to fetch live accomplishment data; failure was not cached")
        return pd.DataFrame(), pd.DataFrame()


@st.cache_data(ttl="1h")
def _fetch_vacctrack_data_cached():
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_vt = conn.read(spreadsheet=SIA_SHEET_URL, worksheet="VaccTrack", ttl="1h")
        
        if df_vt.empty:
            logger.warning("VaccTrack worksheet returned no data")
            return pd.DataFrame()
            
        return df_vt
    except Exception:
        logger.exception("VaccTrack read failed inside cached fetch")
        raise


def fetch_vacctrack_data():
    try:
        return _fetch_vacctrack_data_cached()
    except Exception:
        logger.exception("Failed to fetch VaccTrack data; failure was not cached")
        return pd.DataFrame()


@st.cache_data(ttl="1h")
def _fetch_opt_data_cached():
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        # Pulls exactly from the new 2026 OPT sheet you created
        df_opt = conn.read(spreadsheet=SIA_SHEET_URL, worksheet="2026 OPT", ttl="1h")
        
        if df_opt.empty:
            logger.warning("2026 OPT worksheet returned no data")
            return pd.DataFrame()
            
        return df_opt
    except Exception:
        logger.exception("OPT read failed inside cached fetch")
        raise


def fetch_opt_data():
    try:
        return _fetch_opt_data_cached()
    except Exception:
        logger.exception("Failed to fetch OPT data; failure was not cached")
        return pd.DataFrame()