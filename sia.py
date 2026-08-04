from streamlit_autorefresh import st_autorefresh
import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import datetime
import pytz
import time
import hashlib
from supabase import create_client, Client
import requests
import json

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
            continue 
            
    return None

# # ==========================================
# 1. PAGE CONFIGURATION & UI/UX STYLING
# ==========================================
st.set_page_config(page_title="Abra SIA 2026 Tracker", page_icon="💉", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    /* 1. Pull the dashboard to the very top */
    .block-container {
        padding-top: 0.5rem !important; 
    }
    header[data-testid="stHeader"] { background-color: transparent !important; }
    footer {visibility: hidden;}
    
    /* 2. MASSIVE KPI CARDS */
    [data-testid="stMetric"] {
        background-color: #ffffff !important;
        border: 1px solid #e2e8f0 !important;
        border-bottom: 6px solid #0033A0 !important; 
        border-radius: 8px !important;
        padding: 15px 10px !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05) !important;
        height: 140px !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        align-items: center !important;
    }
    [data-testid="stMetricLabel"] * {
        font-size: 15px !important;
        font-weight: 700 !important;
        color: #475569 !important;
        text-align: center !important;
        width: 100% !important;
    }
    [data-testid="stMetricValue"] * {
        font-size: 42px !important;
        font-weight: 900 !important;
        color: #0033A0 !important; 
        text-align: center !important;
        width: 100% !important;
        line-height: 1.2 !important;
    }

    /* 3. TABS: LARGER AND CENTER ALIGNED */
    [data-testid="stTabs"] > div[data-baseweb="tab-list"], 
    [data-testid="stTabs"] > div[role="tablist"] {
        border-top: 1px solid #cbd5e1 !important;
        border-bottom: 1px solid #cbd5e1 !important;
        padding: 15px 0 !important;
        margin-bottom: 25px !important;
        display: flex !important;
        width: 100% !important;
        justify-content: center !important;
    }
    
    /* Target the hidden inner scroll wrapper and force it to center */
    [data-testid="stTabs"] > div > div[data-baseweb="tab-list"] > div, 
    [data-testid="stTabs"] > div[role="tablist"] > div {
        display: flex !important;
        justify-content: center !important;
        margin: 0 auto !important; 
        width: fit-content !important;
    }
    
    button[data-testid="stTab"], button[data-baseweb="tab"] {
        background-color: transparent !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 8px !important;
        padding: 15px 30px !important; 
        flex: 0 1 auto !important; 
        margin: 0 5px !important;
        transition: all 0.2s ease-in-out !important;
    }
    
    button[data-testid="stTab"] p, button[data-baseweb="tab"] p {
        font-size: 18px !important; 
        font-weight: 700 !important;
        color: #475569 !important;
        margin: 0 !important;
    }
    
    /* The Active Highlighted Tab */
    button[data-testid="stTab"][aria-selected="true"], button[data-baseweb="tab"][aria-selected="true"] {
        background-color: #0033A0 !important; 
        border-color: #0033A0 !important;
        box-shadow: 0 4px 10px rgba(0, 51, 160, 0.3) !important;
    }
    button[data-testid="stTab"][aria-selected="true"] p, button[data-baseweb="tab"][aria-selected="true"] p {
        color: #ffffff !important;
    }
    
    div[data-testid="stTabIndicator"], div[data-baseweb="tab-highlight"] { display: none !important; }

    /* 4. PROTECT THE INNER SUB-TABS */
    .stTabs .stTabs button[data-testid="stTab"], .stTabs .stTabs button[data-baseweb="tab"] {
        background-color: transparent !important;
        border: none !important;
        border-bottom: 2px solid transparent !important;
        border-radius: 0 !important;
        padding: 8px 15px !important;
    }
    .stTabs .stTabs button[data-testid="stTab"] p, .stTabs .stTabs button[data-baseweb="tab"] p {
        font-size: 14px !important;
        color: #64748b !important;
    }
    .stTabs .stTabs button[data-testid="stTab"][aria-selected="true"], .stTabs .stTabs button[data-baseweb="tab"][aria-selected="true"] {
        border-bottom: 3px solid #0033A0 !important; 
        background-color: transparent !important;
        box-shadow: none !important;
    }
    .stTabs .stTabs button[data-testid="stTab"][aria-selected="true"] p, .stTabs .stTabs button[data-baseweb="tab"][aria-selected="true"] p {
        color: #0033A0 !important; 
    }

    [data-testid="stExpander"], div[data-testid="stExpanderDetails"] { overflow: visible !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. SUPABASE INITIALIZATION
# ==========================================
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_supabase()
except Exception as e:
    st.error("⚠️ Supabase Connection Error: Please ensure SUPABASE_URL and SUPABASE_KEY are set in Streamlit Secrets.")
    st.stop()

sheet_url = "https://docs.google.com/spreadsheets/d/1hM0yhzLY5uCh-bxFRPV7u6MYAzimfG0f4uluUGkLogU"

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    return make_hashes(password) == hashed_text

# ==========================================
# 3. SECURITY, SESSION STATE & TIMEOUT
# ==========================================
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'username' not in st.session_state: 
    st.session_state['username'] = ""
if 'user_name' not in st.session_state:
    st.session_state['user_name'] = ""
if 'user_role' not in st.session_state:
    st.session_state['user_role'] = ""
if 'assigned_muni' not in st.session_state:
    st.session_state['assigned_muni'] = "None"
if 'last_active' not in st.session_state:
    st.session_state['last_active'] = time.time()

if st.session_state['logged_in']:
    current_time = time.time()
    timeout_seconds = 30 * 60 
    
    if current_time - st.session_state['last_active'] > timeout_seconds:
        st.session_state['logged_in'] = False
        st.session_state['username'] = ""
        st.session_state['user_name'] = ""
        st.session_state['user_role'] = ""
        st.session_state['assigned_muni'] = "None"
        st.warning("⏱️ You have been automatically logged out due to 30 minutes of inactivity.")
        time.sleep(2)
        st.rerun()
    else:
        st.session_state['last_active'] = current_time

# ==========================================
# 4. THE GATEWAY (Simplified Login)
# ==========================================
abra_munis = ["Bangued", "Boliney", "Bucay", "Bucloc", "Daguioman", "Danglas", "Dolores", "La Paz", "Lacub", "Lagangilang", "Lagayan", "Langiden", "Licuan-Baay", "Luba", "Malibcong", "Manabo", "Peñarrubia", "Pidigan", "Pilar", "Sallapadan", "San Isidro", "San Juan", "San Quintin", "Tayum", "Tineg", "Tubo", "Villaviciosa"]

if not st.session_state['logged_in']:
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.title("Abra SIA 2026")
        st.markdown("##### Secure Provincial Command Center")
        st.divider()
        
        with st.form("login_form"):
            input_username = st.text_input("Username").strip()
            input_password = st.text_input("Password", type="password")
            
            st.markdown("<br>", unsafe_allow_html=True)
            st.caption("📌 **For RHU Viewers Only:** Please select your municipality so we can log your visit.")
            viewer_rhu = st.selectbox("Your Municipality", ["Select Municipality..."] + abra_munis)
            
            submit_login = st.form_submit_button("Log In", type="primary", use_container_width=True)
            
            if submit_login:
                if not input_username or not input_password:
                    st.warning("Please enter both username and password.")
                else:
                    try:
                        res = supabase.table('user_accounts').select('*').eq('username', input_username).execute()
                        user_records = res.data
                        
                        if user_records:
                            user_data = user_records[0]
                            stored_hash = user_data.get('password_hash', '')
                            db_role = user_data.get('role', '')
                            
                            # Enforce RHU selection for non-admins
                            if db_role != "System Admin" and viewer_rhu == "Select Municipality...":
                                st.error("🚨 You must select your Municipality to log in.")
                            else:
                                if check_hashes(input_password, stored_hash):
                                    
                                    # Override name and muni for visitors based on their dropdown selection
                                    if db_role != "System Admin":
                                        db_name = f"RHU Visitor ({viewer_rhu})"
                                        db_muni = viewer_rhu
                                    else:
                                        db_name = user_data['name']
                                        db_muni = "Abra Province"
                                    
                                    manila_tz = pytz.timezone('Asia/Manila')
                                    current_time_str = datetime.now(manila_tz).strftime("%Y-%m-%d %I:%M:%S %p")
                                    
                                    # Insert the login record AND capture the response
                                    log_response = supabase.table('access_logs').insert({
                                        'timestamp': current_time_str, 
                                        'name': db_name, 
                                        'role': db_role, 
                                        'action': 'Active Session'
                                    }).execute()
                                    
                                    st.session_state['logged_in'] = True
                                    st.session_state['username'] = input_username 
                                    st.session_state['user_name'] = db_name
                                    st.session_state['user_role'] = db_role
                                    st.session_state['assigned_muni'] = db_muni
                                    st.session_state['last_active'] = time.time()
                                    
                                    # Capture exact login time AND the database Row ID
                                    st.session_state['login_time'] = time.time()
                                    if log_response.data:
                                        st.session_state['log_id'] = log_response.data[0]['id'] 
                                        
                                    st.toast(f"Welcome, {db_name}!", icon="👋")
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error("❌ Incorrect Password.")
                        else:
                            st.error("❌ Username not found.")
                    except Exception as e:
                        st.error(f"System Error: {e}")

    st.stop()

# ==========================================
# MAIN DASHBOARD CODE (Only runs if logged in)
# ==========================================
st.title("Abra Supplemental Immunization Activity (SIA) 2026")

@st.cache_data(ttl="15s")
def get_last_updated_time():
    tz = pytz.timezone('Asia/Manila')
    return datetime.now(tz).strftime("%B %d, %Y | %I:%M %p")

last_updated = get_last_updated_time()
is_admin = st.session_state['user_role'] == "System Admin"

# Automatically refresh the dashboard every 1 hour (3,600,000 ms)
st_autorefresh(interval=3600000, limit=None, key="hourly_data_refresh")

# ==========================================
# CONTINUOUS SESSION TRACKING
# ==========================================
if 'login_time' in st.session_state and 'log_id' in st.session_state:
    try:
        # Calculate duration so far
        session_duration_seconds = time.time() - st.session_state['login_time']
        minutes, seconds = divmod(int(session_duration_seconds), 60)
        hours, minutes = divmod(minutes, 60)
        formatted_duration = f"{hours}h {minutes}m {seconds}s"
        
        # Continuously update the existing row in Supabase
        supabase.table('access_logs').update({
            'action': f'Session Duration: {formatted_duration}'
        }).eq('id', st.session_state['log_id']).execute()
    except Exception as e:
        pass # Silently fail if Supabase connection blips so it doesn't crash the app

with st.sidebar:
    # 1. PROFILE CARD
    user_territory = st.session_state.get('assigned_muni', 'None')
    
    st.markdown(f"""
    <div style="text-align: center; padding: 10px 0px 15px 0px;">
        <img src="https://upload.wikimedia.org/wikipedia/commons/1/1a/Abra_provincial_seal.png" width="90" style="margin-bottom: 15px; filter: drop-shadow(0px 4px 6px rgba(0,0,0,0.1));">
        <h3 style="margin: 0; padding: 0; font-size: 1.15rem; font-weight: 700;">{st.session_state['user_name']}</h3>
        <p style="margin: 2px 0 12px 0; font-size: 0.85rem; opacity: 0.8; font-style: italic;">{st.session_state['user_role']}</p>
        <span style="background-color: rgba(128,128,128,0.15); border: 1px solid rgba(128,128,128,0.3); color: inherit; padding: 6px 16px; border-radius: 20px; font-size: 0.85rem; font-weight: 600; letter-spacing: 0.5px;">
            📍 {user_territory.upper()}
        </span>
    </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    # 2. Dynamic Filters (Expanded by default to prevent dropdown cutoffs)
    with st.expander("🎛️ DASHBOARD FILTERS", expanded=True):
        view_mode = st.radio("Geographic Level:", ["All Municipalities (Abra)", "Specific Municipality"])
        
        if view_mode == "Specific Municipality":
            selected_muni = st.selectbox("Select Municipality:", abra_munis)
        else:
            selected_muni = "None"
            
        st.write("")
        # The Universal Program & Age Filter
        age_filter = st.selectbox("Program & Age Group:", [
            "MR: 6 - 59 months (Total)", "MR: 6 - 12 months", "MR: 13 - 23 months", "MR: 24 - 59 months",
            "Vit A: 6 - 59 months (Total)", "Vit A: 6 - 11 months", "Vit A: 12 - 59 months"
        ])

        # Universal Gender Filter
        gender_filter = st.selectbox("Target Gender:", ["Total (Both)", "Male", "Female"])
        
    # 3. System Actions
    with st.expander("🛠️ SYSTEM ACTIONS", expanded=False):
        if st.button("🔄 Refresh Data", use_container_width=True):
            st.cache_data.clear()
            st.toast("Dashboard Interface Refreshed!", icon="🔄")
            time.sleep(0.5)
            st.rerun()
            
        if st.button("🚪 Logout", type="primary", use_container_width=True):
            
            # The continuous tracker handles the logging now, so we just clear the session variables
            st.session_state['logged_in'] = False
            st.session_state['username'] = ""
            st.session_state['user_name'] = ""
            st.session_state['user_role'] = ""
            st.session_state['assigned_muni'] = ""
            
            # Optional cleanup: remove the tracking variables as well
            if 'login_time' in st.session_state:
                del st.session_state['login_time']
            if 'log_id' in st.session_state:
                del st.session_state['log_id']
                
            st.rerun()
            
        st.caption(f"🕒 Last Sync: {last_updated}")

# --- DATA HELPER FUNCTIONS ---
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

@st.cache_data(ttl="1h")
def fetch_targets_from_supabase():
    res = supabase.table('targets').select('*').execute()
    if not res.data: return pd.DataFrame()
    df = pd.DataFrame(res.data)
    
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
    
    # Ensure all numeric columns are floats/ints
    num_cols = [c for c in df.columns if c not in ['Code', 'Location', 'Level', 'Parent_Province', 'Parent_Municipality']]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    # RE-CALCULATE ACTUAL VITAMIN A TOTALS (Male + Female) TO ENSURE ACCURACY
    df['Act_VitA_6-11m_Total'] = df['Act_VitA_6-11m_M'] + df['Act_VitA_6-11m_F']
    df['Act_VitA_12-59m_Total'] = df['Act_VitA_12-59m_M'] + df['Act_VitA_12-59m_F']
    df['Act_VitA_Total_M'] = df['Act_VitA_6-11m_M'] + df['Act_VitA_12-59m_M']
    df['Act_VitA_Total_F'] = df['Act_VitA_6-11m_F'] + df['Act_VitA_12-59m_F']
    df['Act_VitA_Total'] = df['Act_VitA_6-11m_Total'] + df['Act_VitA_12-59m_Total']

    # RE-CALCULATE NATIONAL VITAMIN A TOTALS
    df['VitA_6-11m_Total'] = df['VitA_6-11m_M'] + df['VitA_6-11m_F']
    df['VitA_12-59m_Total'] = df['VitA_12-59m_M'] + df['VitA_12-59m_F']
    df['VitA_Total_M'] = df['VitA_6-11m_M'] + df['VitA_12-59m_M']
    df['VitA_Total_F'] = df['VitA_6-11m_F'] + df['VitA_12-59m_F']
    df['VitA_Total'] = df['VitA_6-11m_Total'] + df['VitA_12-59m_Total']

    return df

@st.cache_data(ttl="1h")
def fetch_live_accomplishments():
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_mr = conn.read(spreadsheet=sheet_url, worksheet="MR", skiprows=1)
        df_vita = conn.read(spreadsheet=sheet_url, worksheet="VitA", skiprows=1)
        
        if 'Barangay' in df_mr.columns:
            df_mr = df_mr.dropna(subset=['Barangay'])
        if 'Barangay' in df_vita.columns:
            df_vita = df_vita.dropna(subset=['Barangay'])
            
        return df_mr, df_vita
    except Exception as e:
        return pd.DataFrame(), pd.DataFrame()

# ==========================================
# THE DASHBOARD (Tabs and Filters)
# ==========================================
tab_names = ["Executive Summary", "Target Overview", "MR Accomplishment", "Vit A Accomplishment", "Deferral & Refusal Analysis"]
if is_admin:
    tab_names.append("Admin Panel")
    
tabs = st.tabs(tab_names)

if is_admin:
    tab_total, tab_target, tab_mr, tab_vita, tab_def_ref, tab_admin = tabs
else:
    tab_total, tab_target, tab_mr, tab_vita, tab_def_ref = tabs

try:
    # ==========================================
    # EXECUTIVE SUMMARY TAB
    # ==========================================
    with tab_total:
        st.markdown("### Executive Summary")
        
        df_targets = fetch_targets_from_supabase()
        df_mr_live, df_vita_live = fetch_live_accomplishments()
        
        if df_targets.empty:
            st.warning("⚠️ The Targets Database is empty. Please sync the Target Database in the Admin Panel.")
        else:
            # 1. Geographic Filtering
            if view_mode == "All Municipalities (Abra)":
                df_view = df_targets[(df_targets['Level'] == 'Municipality') & (df_targets['Parent_Province'] == 'Abra')]
                location_label = "Abra Province"
                df_view_va = df_view
                geo_col = 'Municipality'
            else:
                df_view = df_targets[(df_targets['Level'] == 'Barangay') & (df_targets['Parent_Municipality'] == selected_muni)]
                location_label = f"{selected_muni}, Abra"
                df_view_va = df_targets[(df_targets['Level'] == 'Municipality') & (df_targets['Location'] == selected_muni)]
                geo_col = 'Barangay'
                
            c_title, c_drop = st.columns([6, 4])
            with c_title:
                st.markdown(f"#### Overall Performance: {location_label} ({gender_filter})")
            with c_drop:
                exec_target_mode = st.selectbox("Target Baseline for Calculations:", ["Projected Population Target", "Actual RHU Target", "Comparison View"], label_visibility="collapsed")

            # --- DYNAMIC FILTER LOGIC FOR MR ---
            mr_genders = ["Male"] if gender_filter == "Male" else ["Female"] if gender_filter == "Female" else ["Male", "Female"]
            mr_t_gen = "_M" if gender_filter == "Male" else "_F" if gender_filter == "Female" else "_Total"
            
            if "6 - 12 months" in age_filter:
                mr_ages, mr_t_age = ["MR 6-12"], "6-12m"
            elif "13 - 23 months" in age_filter:
                mr_ages, mr_t_age = ["MR 13-23"], "13-23m"
            elif "24 - 59 months" in age_filter:
                mr_ages, mr_t_age = ["MR 24-59"], "24-59m"
            else:
                mr_ages, mr_t_age = ["MR 6-12", "MR 13-23", "MR 24-59"], "6-59m"

            mr_dose_cols = [f"{age} {gen}" for age in mr_ages for gen in mr_genders]
            mr_target_col_geo = f'MR_{mr_t_age}{mr_t_gen}'
            act_mr_target_col_geo = f'Act_MR_{mr_t_age}{mr_t_gen}'
            
            nat_target_mr = df_view[mr_target_col_geo].sum()
            act_target_mr = df_view[act_mr_target_col_geo].sum() if act_mr_target_col_geo in df_view.columns else 0

            # --- DYNAMIC FILTER LOGIC FOR VIT A ---
            va_genders = ["Male"] if gender_filter == "Male" else ["Female"] if gender_filter == "Female" else ["Male", "Female"]
            va_t_gen = "_M" if gender_filter == "Male" else "_F" if gender_filter == "Female" else "_Total"
            
            if "6 - 11 months" in age_filter:
                va_ages, va_t_age = ["VitA 6-11"], "6-11m"
            elif "12 - 59 months" in age_filter:
                va_ages, va_t_age = ["VitA 12-59"], "12-59m"
            else:
                va_ages, va_t_age = ["VitA 6-11", "VitA 12-59"], "Total"
            
            va_dose_cols = [f"{age} {gen}" for age in va_ages for gen in va_genders]
            
            if va_t_age == "Total":
                va_nat_col = f'VitA_Total{va_t_gen.replace("_Total", "")}'
                va_act_col = f'Act_VitA_Total{va_t_gen.replace("_Total", "")}'
            else:
                va_nat_col = f'VitA_{va_t_age}{va_t_gen}'
                va_act_col = f'Act_VitA_{va_t_age}{va_t_gen}'

            va_target_col_geo = va_nat_col
            act_va_target_col_geo = va_act_col
            
            nat_target_va = df_view_va[va_nat_col].sum() if not df_view_va.empty else 0
            act_target_va = df_view_va[va_act_col].sum() if (not df_view_va.empty and va_act_col in df_view_va.columns) else 0

            # 2. Process Accomplishments (With simplified geographic filter logic to prevent empty tables)
            total_mr_doses = 0
            df_mr_trend = pd.DataFrame()
            if not df_mr_live.empty and 'Municipality' in df_mr_live.columns:
                df_mr_filtered = df_mr_live.copy()
                if view_mode == "All Municipalities (Abra)":
                    df_mr_filtered = df_mr_filtered[df_mr_filtered['Municipality'].isin(df_view['Location'].tolist())]
                else:
                    df_mr_filtered = df_mr_filtered[df_mr_filtered['Municipality'] == selected_muni]
                
                for col in mr_dose_cols:
                    if col in df_mr_filtered.columns:
                        df_mr_filtered[col] = pd.to_numeric(df_mr_filtered[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
                
                df_mr_filtered['Total Doses'] = df_mr_filtered[[c for c in mr_dose_cols if c in df_mr_filtered.columns]].sum(axis=1).astype(int)
                total_mr_doses = df_mr_filtered['Total Doses'].sum()
                
                if 'Vaccination Date' in df_mr_filtered.columns:
                    df_mr_filtered['Vaccination Date'] = pd.to_datetime(df_mr_filtered['Vaccination Date'], errors='coerce')
                    df_mr_trend = df_mr_filtered.groupby(df_mr_filtered['Vaccination Date'].dt.date)['Total Doses'].sum().reset_index()
                    df_mr_trend.rename(columns={'Total Doses': 'MR Doses'}, inplace=True)

            total_vita_doses = 0
            df_va_trend = pd.DataFrame()
            if not df_vita_live.empty and 'Municipality' in df_vita_live.columns:
                df_vita_filtered = df_vita_live.copy()
                if view_mode == "All Municipalities (Abra)":
                    df_vita_filtered = df_vita_filtered[df_vita_filtered['Municipality'].isin(df_view['Location'].tolist())]
                else:
                    df_vita_filtered = df_vita_filtered[df_vita_filtered['Municipality'] == selected_muni]
                
                for col in va_dose_cols:
                    if col in df_vita_filtered.columns:
                        df_vita_filtered[col] = pd.to_numeric(df_vita_filtered[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
                
                df_vita_filtered['Total Doses'] = df_vita_filtered[[c for c in va_dose_cols if c in df_vita_filtered.columns]].sum(axis=1).astype(int)
                total_vita_doses = df_vita_filtered['Total Doses'].sum()
                
                if 'Vaccination Date' in df_vita_filtered.columns:
                    df_vita_filtered['Vaccination Date'] = pd.to_datetime(df_vita_filtered['Vaccination Date'], errors='coerce')
                    df_va_trend = df_vita_filtered.groupby(df_vita_filtered['Vaccination Date'].dt.date)['Total Doses'].sum().reset_index()
                    df_va_trend.rename(columns={'Total Doses': 'Vit A Doses'}, inplace=True)

            import plotly.graph_objects as go

            if exec_target_mode in ["Projected Population Target", "Actual RHU Target"]:
                if exec_target_mode == "Projected Population Target":
                    active_target_mr = nat_target_mr
                    active_target_va = nat_target_va
                    target_label = "Projected Target"
                    mr_target_col_geo_active = mr_target_col_geo
                    va_target_col_geo_active = va_target_col_geo
                else:
                    active_target_mr = act_target_mr
                    active_target_va = act_target_va
                    target_label = "Actual Target"
                    mr_target_col_geo_active = act_mr_target_col_geo
                    va_target_col_geo_active = act_va_target_col_geo

                mr_cov_pct = (total_mr_doses / active_target_mr * 100) if active_target_mr > 0 else 0
                va_cov_pct = (total_vita_doses / active_target_va * 100) if active_target_va > 0 else 0

                # 4. KPI Cards
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("MR Doses Administered", f"{total_mr_doses:,.0f}", f"{target_label}: {active_target_mr:,.0f}", delta_color="off")
                k2.metric("MR Coverage %", f"{mr_cov_pct:.1f}%")
                k3.metric("Vit A Doses Administered", f"{total_vita_doses:,.0f}", f"{target_label}: {active_target_va:,.0f}", delta_color="off")
                k4.metric("Vit A Coverage %", f"{va_cov_pct:.1f}%")
                
                st.divider()
                
                c1, c2 = st.columns(2)
                
                with c1:
                    st.markdown("#### Campaign Progress")
                    # MR Gauge
                    fig_gauge_mr = go.Figure(go.Indicator(
                        mode = "gauge+number+delta", value = mr_cov_pct, title = {'text': f"MR Coverage ({exec_target_mode.split()[0]})"},
                        delta = {'reference': 95, 'increasing': {'color': "green"}, 'decreasing': {'color': "red"}},
                        gauge = {'axis': {'range': [None, 100]}, 'bar': {'color': "#1E88E5"}, 'bgcolor': "rgba(128,128,128,0.2)", 'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 95}}
                    ))
                    fig_gauge_mr.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_gauge_mr, use_container_width=True)

                    # Vit A Gauge
                    fig_gauge_va = go.Figure(go.Indicator(
                        mode = "gauge+number+delta", value = va_cov_pct, title = {'text': f"Vit A Coverage ({exec_target_mode.split()[0]})"},
                        delta = {'reference': 95, 'increasing': {'color': "green"}, 'decreasing': {'color': "red"}},
                        gauge = {'axis': {'range': [None, 100]}, 'bar': {'color': "#F4511E"}, 'bgcolor': "rgba(128,128,128,0.2)", 'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 95}}
                    ))
                    fig_gauge_va.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_gauge_va, use_container_width=True)

                with c2:
                    st.markdown("#### Daily Vaccination Trend")
                    if not df_mr_trend.empty or not df_va_trend.empty:
                        if not df_mr_trend.empty and not df_va_trend.empty:
                            df_trend = pd.merge(df_mr_trend, df_va_trend, on='Vaccination Date', how='outer').fillna(0)
                        elif not df_mr_trend.empty:
                            df_trend = df_mr_trend.copy()
                            df_trend['Vit A Doses'] = 0
                        else:
                            df_trend = df_va_trend.copy()
                            df_trend['MR Doses'] = 0
                            
                        df_trend = df_trend.sort_values('Vaccination Date')
                        
                        fig_trend = px.line(df_trend, x='Vaccination Date', y=['MR Doses', 'Vit A Doses'], markers=True, color_discrete_sequence=['#1E88E5', '#F4511E'])
                        fig_trend.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis_title="", yaxis_title="Doses Administered", legend_title_text="Program", height=500, margin=dict(l=0, r=0, t=40, b=0))
                        st.plotly_chart(fig_trend, use_container_width=True)
                        
                st.divider()
                st.markdown(f"#### Geographic Coverage Breakdown ({geo_col})")
                
                # Combine targets and doses for geographic table/chart
                if not df_mr_live.empty and geo_col in df_mr_filtered.columns:
                    mr_geo_doses = df_mr_filtered.groupby(geo_col)['Total Doses'].sum().reset_index()
                    mr_geo_doses.rename(columns={'Total Doses': 'MR Administered'}, inplace=True)
                else:
                    mr_geo_doses = pd.DataFrame(columns=[geo_col, 'MR Administered'])
                    
                mr_geo_targets = df_view.groupby('Location')[mr_target_col_geo_active].sum().reset_index()
                mr_geo_targets.rename(columns={'Location': geo_col, mr_target_col_geo_active: 'MR Target'}, inplace=True)
                
                df_geo_summary = pd.merge(mr_geo_targets, mr_geo_doses, on=geo_col, how='left').fillna(0)
                # Prevent division by zero if target is 0
                df_geo_summary['MR Coverage %'] = df_geo_summary.apply(lambda row: (row['MR Administered'] / row['MR Target'] * 100) if row['MR Target'] > 0 else 0, axis=1)                
                
                # Do the same for Vit A
                if not df_vita_live.empty and geo_col in df_vita_filtered.columns:
                    va_geo_doses = df_vita_filtered.groupby(geo_col)['Total Doses'].sum().reset_index()
                    va_geo_doses.rename(columns={'Total Doses': 'Vit A Administered'}, inplace=True)
                else:
                    va_geo_doses = pd.DataFrame(columns=[geo_col, 'Vit A Administered'])
                
                va_geo_targets = df_view_va.groupby('Location')[va_target_col_geo_active].sum().reset_index()
                va_geo_targets.rename(columns={'Location': geo_col, va_target_col_geo_active: 'Vit A Target'}, inplace=True)
                
                df_geo_summary = pd.merge(df_geo_summary, va_geo_targets, on=geo_col, how='left').fillna(0)
                df_geo_summary = pd.merge(df_geo_summary, va_geo_doses, on=geo_col, how='left').fillna(0)
                
                # Prevent division by zero if target is 0
                df_geo_summary['Vit A Coverage %'] = df_geo_summary.apply(
                    lambda row: (row['Vit A Administered'] / row['Vit A Target'] * 100) if row['Vit A Target'] > 0 else 0, axis=1
                )
                
                # Sort ascending so highest coverage sits at the top of the portrait chart
                df_geo_summary = df_geo_summary.sort_values('MR Coverage %', ascending=True)
                
                # --- MELT FOR GROUPED BAR CHART (MR vs Vit A) ---
                # dynamically remove Vit A from the chart if specific municipality is chosen
                if view_mode == "All Municipalities (Abra)":
                    vars_to_melt = ['MR Coverage %', 'Vit A Coverage %']
                    color_seq = ['#1E88E5', '#F4511E']
                    bar_multiplier = 70  # Taller height to fit 2 bars
                else:
                    vars_to_melt = ['MR Coverage %']
                    color_seq = ['#1E88E5']
                    bar_multiplier = 35  # Shorter height since it's only 1 bar
                    
                df_melt_geo = df_geo_summary.melt(id_vars=[geo_col], value_vars=vars_to_melt, var_name='Program', value_name='Coverage %')
                
                # Dynamic height: Adjusts based on whether we have 1 or 2 programs showing
                chart_height = max(400, len(df_geo_summary) * bar_multiplier)
                
                fig_geo_cov = px.bar(
                    df_melt_geo, 
                    x='Coverage %', 
                    y=geo_col, 
                    color='Program', 
                    barmode='group', 
                    orientation='h', 
                    text_auto='.1f', 
                    title=f"Coverage % by {geo_col}", 
                    color_discrete_sequence=color_seq
                )
                
                # Force large labels on the outside of the bars
                fig_geo_cov.update_traces(
                    textfont=dict(size=16),
                    insidetextfont=dict(size=16),
                    outsidetextfont=dict(size=16),
                    textposition="outside", 
                    cliponaxis=False 
                )
                
                fig_geo_cov.add_vline(x=95, line_dash="dash", line_color="red", annotation_text="95% Target")
                
                fig_geo_cov.update_layout(
                    plot_bgcolor='rgba(0,0,0,0)', 
                    xaxis_title="Coverage (%)", 
                    yaxis_title="", 
                    height=chart_height, 
                    margin=dict(l=0, r=50, t=40, b=0), 
                    legend_title_text="",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1) 
                )
                st.plotly_chart(fig_geo_cov, use_container_width=True)

                # ==========================================
                # 🗺️ CHOROPLETH COVERAGE MAP
                # ==========================================
                # ONLY show the map if looking at the whole province
                if view_mode == "All Municipalities (Abra)":

                    st.divider()
                    st.markdown(f"#### 🗺️ Provincial Coverage Map")
                    
                    abra_geo = fetch_abra_geojson()
                    
                    if abra_geo and not df_geo_summary.empty:
                        
                        df_geo_summary['Map_Location'] = df_geo_summary[geo_col].str.upper().str.strip()
                        
                        # Force our Google Sheet names to perfectly match the Map File's hidden names
                        df_geo_summary['Map_Location'] = df_geo_summary['Map_Location'].replace({
                            'SALAPADAN': 'SALLAPADAN',
                            'PENARRUBIA': 'PEŃARRUBIA',  
                            'PEÑARRUBIA': 'PEŃARRUBIA',  # Map uses a weird Ń character
                            'LICUAN-BAAY (LICUAN)': 'LICUAN-BAAY'
                        })
                                                        
                        map_c1, map_c2 = st.columns(2)
                        
                        with map_c1:
                            st.markdown("**Measles-Rubella (MR) Coverage**")
                            fig_map_mr = px.choropleth_mapbox(
                                df_geo_summary,
                                geojson=abra_geo,
                                locations='Map_Location',
                                featureidkey="properties.Standard_Name", # Look specifically at the name we cleaned
                                color='MR Coverage %',
                                color_continuous_scale="RdYlGn", 
                                range_color=[0, 100],
                                mapbox_style="carto-positron",
                                zoom=8.5,
                                center={"lat": 17.58, "lon": 120.80},
                                opacity=0.7,
                                hover_name=geo_col,
                                hover_data={'Map_Location': False, 'MR Target': ':,', 'MR Administered': ':,'}
                            )
                            fig_map_mr.update_layout(margin={"r":0,"t":0,"l":0,"b":0}, coloraxis_colorbar=dict(title="MR %"))
                            st.plotly_chart(fig_map_mr, use_container_width=True)
                            
                        with map_c2:
                            st.markdown("**Vitamin A Coverage**")
                            if 'Vit A Coverage %' in df_geo_summary.columns and view_mode == "All Municipalities (Abra)":
                                fig_map_va = px.choropleth_mapbox(
                                    df_geo_summary,
                                    geojson=abra_geo,
                                    locations='Map_Location',
                                    featureidkey="properties.Standard_Name", 
                                    color='Vit A Coverage %',
                                    color_continuous_scale="RdYlGn",
                                    range_color=[0, 100],
                                    mapbox_style="carto-positron",
                                    zoom=8.5,
                                    center={"lat": 17.58, "lon": 120.80},
                                    opacity=0.7,
                                    hover_name=geo_col,
                                    hover_data={'Map_Location': False, 'Vit A Target': ':,', 'Vit A Administered': ':,'}
                                )
                                fig_map_va.update_layout(margin={"r":0,"t":0,"l":0,"b":0}, coloraxis_colorbar=dict(title="Vit A %"))
                                st.plotly_chart(fig_map_va, use_container_width=True)
                    else:
                        st.warning("Map boundary data could not be loaded or dataset is empty.")
                    
                with st.expander("View Full Geographic Coverage Data"):
                    # Format to remove decimals and add comma separators
                    # Dynamically drop Vit A for specific municipality views
                    df_display = df_geo_summary.copy()
                    
                    if view_mode == "Specific Municipality":
                        cols_to_drop = [c for c in df_display.columns if 'Vit A' in c]
                        df_display = df_display.drop(columns=cols_to_drop, errors='ignore')
                        format_dict = {
                            "MR Coverage %": "{:.1f}%",
                            "MR Target": "{:,.0f}",
                            "MR Administered": "{:,.0f}"
                        }
                    else:
                        format_dict = {
                            "MR Coverage %": "{:.1f}%",
                            "MR Target": "{:,.0f}",
                            "MR Administered": "{:,.0f}",
                            "Vit A Coverage %": "{:.1f}%",
                            "Vit A Target": "{:,.0f}",
                            "Vit A Administered": "{:,.0f}"
                        }
                        
                    # Reverse sort again for the raw data table so best is at the top row
                    st.dataframe(df_display.sort_values('MR Coverage %', ascending=False).style.format(format_dict), use_container_width=True, hide_index=True)

            else:
                # ==============================
                # COMPARISON VIEW
                # ==============================
                nat_cov_mr = (total_mr_doses / nat_target_mr * 100) if nat_target_mr > 0 else 0
                act_cov_mr = (total_mr_doses / act_target_mr * 100) if act_target_mr > 0 else 0
                
                nat_cov_va = (total_vita_doses / nat_target_va * 100) if nat_target_va > 0 else 0
                act_cov_va = (total_vita_doses / act_target_va * 100) if act_target_va > 0 else 0
                
                mr_var = act_target_mr - nat_target_mr
                va_var = act_target_va - nat_target_va

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Total MR Doses", f"{total_mr_doses:,.0f}")
                k2.metric("MR Target Variance", f"{mr_var:,.0f}", "Actual vs Projected", delta_color="inverse")
                k3.metric("Total Vit A Doses", f"{total_vita_doses:,.0f}")
                k4.metric("Vit A Target Variance", f"{va_var:,.0f}", "Actual vs Projected", delta_color="inverse")
                
                st.divider()
                st.markdown("#### Coverage Comparison")
                
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    fig_gm1 = go.Figure(go.Indicator(mode="gauge+number", value=nat_cov_mr, title={'text': "MR (vs Projected)"}, gauge={'axis': {'range': [None, 100]}, 'bar': {'color': "#1E88E5"}, 'bgcolor': "rgba(128,128,128,0.2)", 'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 95}}))
                    fig_gm1.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_gm1, use_container_width=True)
                with c2:
                    fig_gm2 = go.Figure(go.Indicator(mode="gauge+number", value=act_cov_mr, title={'text': "MR (vs Actual)"}, gauge={'axis': {'range': [None, 100]}, 'bar': {'color': "#43A047"}, 'bgcolor': "rgba(128,128,128,0.2)", 'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 95}}))
                    fig_gm2.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_gm2, use_container_width=True)
                with c3:
                    fig_gv1 = go.Figure(go.Indicator(mode="gauge+number", value=nat_cov_va, title={'text': "Vit A (vs Projected)"}, gauge={'axis': {'range': [None, 100]}, 'bar': {'color': "#F4511E"}, 'bgcolor': "rgba(128,128,128,0.2)", 'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 95}}))
                    fig_gv1.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_gv1, use_container_width=True)
                with c4:
                    fig_gv2 = go.Figure(go.Indicator(mode="gauge+number", value=act_cov_va, title={'text': "Vit A (vs Actual)"}, gauge={'axis': {'range': [None, 100]}, 'bar': {'color': "#8E24AA"}, 'bgcolor': "rgba(128,128,128,0.2)", 'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 95}}))
                    fig_gv2.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_gv2, use_container_width=True)
                    
                st.divider()
                st.markdown(f"#### Geographic Coverage Comparison ({geo_col})")
                
                if not df_mr_live.empty and geo_col in df_mr_filtered.columns:
                    mr_geo_doses = df_mr_filtered.groupby(geo_col)['Total Doses'].sum().reset_index()
                    mr_geo_doses.rename(columns={'Total Doses': 'Doses'}, inplace=True)
                else:
                    mr_geo_doses = pd.DataFrame(columns=[geo_col, 'Doses'])
                    
                mr_geo_nat = df_view.groupby('Location')[mr_target_col_geo].sum().reset_index().rename(columns={'Location': geo_col, mr_target_col_geo: 'Proj Target'})
                mr_geo_act = df_view.groupby('Location')[act_mr_target_col_geo].sum().reset_index().rename(columns={'Location': geo_col, act_mr_target_col_geo: 'Act Target'})
                
                df_comp_geo = pd.merge(mr_geo_nat, mr_geo_act, on=geo_col, how='left').fillna(0)
                df_comp_geo = pd.merge(df_comp_geo, mr_geo_doses, on=geo_col, how='left').fillna(0)
                
                # Zero-division fix applied here for the Comparison logic!
                df_comp_geo['Proj Coverage %'] = df_comp_geo.apply(
                    lambda row: (row['Doses'] / row['Proj Target'] * 100) if row['Proj Target'] > 0 else 0, axis=1
                )
                df_comp_geo['Act Coverage %'] = df_comp_geo.apply(
                    lambda row: (row['Doses'] / row['Act Target'] * 100) if row['Act Target'] > 0 else 0, axis=1
                )
                
                # Sort ascending for horizontal bar chart
                df_comp_geo = df_comp_geo.sort_values('Proj Coverage %', ascending=True)
                df_melt = df_comp_geo.melt(id_vars=[geo_col], value_vars=['Proj Coverage %', 'Act Coverage %'], var_name='Baseline', value_name='Coverage %')
                df_melt['Baseline'] = df_melt['Baseline'].replace({'Proj Coverage %': 'vs Projected', 'Act Coverage %': 'vs Actual'})
                
                chart_height_comp = max(500, len(df_comp_geo) * 70)
                
                fig_comp_cov = px.bar(
                    df_melt, 
                    x='Coverage %', 
                    y=geo_col, 
                    color='Baseline', 
                    barmode='group', 
                    orientation='h',
                    text_auto='.1f', 
                    color_discrete_sequence=['#1E88E5', '#43A047']
                )
                
                fig_comp_cov.update_traces(
                    textfont=dict(size=16),
                    insidetextfont=dict(size=16),
                    outsidetextfont=dict(size=16),
                    textposition="outside", 
                    cliponaxis=False
                )
                
                fig_comp_cov.add_vline(x=95, line_dash="dash", line_color="red", annotation_text="95% Target")
                fig_comp_cov.update_layout(
                    plot_bgcolor='rgba(0,0,0,0)', 
                    xaxis_title="Coverage (%)", 
                    yaxis_title="", 
                    height=chart_height_comp, 
                    margin=dict(l=0, r=50, t=40, b=0), 
                    legend_title_text="",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                st.plotly_chart(fig_comp_cov, use_container_width=True)

    with tab_target:
        st.markdown("### Provincial Target Baseline Overview")
        df_targets = fetch_targets_from_supabase()
        
        if df_targets.empty:
            st.warning("⚠️ The Targets Database is empty. Please ask a System Admin to sync the database from Google Sheets.")
        else:
            # 1. Geographic Filtering (Locked to Abra)
            if view_mode == "All Municipalities (Abra)":
                df_view = df_targets[(df_targets['Level'] == 'Municipality') & (df_targets['Parent_Province'] == 'Abra')]
                location_label = "Abra Province"
                va_warning = False
                df_view_va = df_view
            else:
                df_view = df_targets[(df_targets['Level'] == 'Barangay') & (df_targets['Parent_Municipality'] == selected_muni)]
                location_label = f"{selected_muni}, Abra"
                va_warning = True
                df_view_va = df_targets[(df_targets['Level'] == 'Municipality') & (df_targets['Location'] == selected_muni)]

            # 2. Create 5 Distinct Sub-Tabs
            tab_nat_mr, tab_nat_va, tab_act_mr, tab_act_va, tab_compare = st.tabs([
                "💉 Nat. MR Targets", "💊 Nat. Vit A Targets", 
                "📊 Act. MR Targets", "📈 Act. Vit A Targets", 
                "⚖️ Target Comparison"
            ])
            
            # ==========================================
            # SUB-TAB 1: NATIONAL MR
            # ==========================================
            with tab_nat_mr:
                st.markdown(f"#### National MR Breakdown: {location_label} ({gender_filter})")
                
                t_col = 'MR_6-59m_Total' if gender_filter == "Total (Both)" else 'MR_6-59m_M' if gender_filter == "Male" else 'MR_6-59m_F'
                c1_col = 'MR_6-12m_Total' if gender_filter == "Total (Both)" else 'MR_6-12m_M' if gender_filter == "Male" else 'MR_6-12m_F'
                c2_col = 'MR_13-23m_Total' if gender_filter == "Total (Both)" else 'MR_13-23m_M' if gender_filter == "Male" else 'MR_13-23m_F'
                c3_col = 'MR_24-59m_Total' if gender_filter == "Total (Both)" else 'MR_24-59m_M' if gender_filter == "Male" else 'MR_24-59m_F'
                
                kpi1, kpi2, kpi3, kpi4 = st.columns(4)
                kpi1.metric("6 - 59m (Grand Total)", f"{df_view[t_col].sum():,.0f}")
                kpi2.metric("6 - 12 months", f"{df_view[c1_col].sum():,.0f}")
                kpi3.metric("13 - 23 months", f"{df_view[c2_col].sum():,.0f}")
                kpi4.metric("24 - 59 months", f"{df_view[c3_col].sum():,.0f}")
                
                if age_filter == "MR: 6 - 12 months":
                    plot_col = c1_col
                    chart_title = f"Eligible Children (6-12m, {gender_filter})"
                elif age_filter == "MR: 13 - 23 months":
                    plot_col = c2_col
                    chart_title = f"Eligible Children (13-23m, {gender_filter})"
                elif age_filter == "MR: 24 - 59 months":
                    plot_col = c3_col
                    chart_title = f"Eligible Children (24-59m, {gender_filter})"
                else:
                    plot_col = t_col
                    chart_title = f"Eligible Children (Total, {gender_filter})"

                if not df_view.empty:
                    # Full width bar chart
                    df_sorted_mr = df_view.sort_values(plot_col, ascending=True) 
                    fig_mr = px.bar(df_sorted_mr, x=plot_col, y='Location', orientation='h', text_auto='.0f', color_discrete_sequence=['#1E88E5'])
                    fig_mr.update_layout(xaxis_title=chart_title, yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=600, margin=dict(l=0, r=0, t=10, b=0))
                    st.plotly_chart(fig_mr, use_container_width=True)
                    
                    st.markdown("<br>", unsafe_allow_html=True)
                    
                    # Centered Pie Chart below
                    pc1, pc2, pc3 = st.columns([1, 2, 1])
                    with pc2:
                        mr_age_data = pd.DataFrame({
                            'Age Group': ['6-12m', '13-23m', '24-59m'], 
                            'Target': [df_view[c1_col].sum(), df_view[c2_col].sum(), df_view[c3_col].sum()]
                        })
                        fig_donut_mr = px.pie(mr_age_data, names='Age Group', values='Target', hole=0.4, title=f"Age Distribution ({gender_filter})", color_discrete_sequence=['#E53935', '#FFB300', '#43A047'])
                        fig_donut_mr.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), height=400, margin=dict(l=0, r=0, t=30, b=0))
                        st.plotly_chart(fig_donut_mr, use_container_width=True)

            # ==========================================
            # SUB-TAB 2: NATIONAL VITAMIN A
            # ==========================================
            with tab_nat_va:
                st.markdown(f"#### National Vitamin A Breakdown: {location_label} ({gender_filter})")
                if va_warning:
                    st.warning("⚠️ The official database does not contain Barangay-level targets for Vitamin A. Displaying the overall Municipal target instead.")
                    
                t_col_va = 'VitA_Total' if gender_filter == "Total (Both)" else 'VitA_Total_M' if gender_filter == "Male" else 'VitA_Total_F'
                c1_col_va = 'VitA_6-11m_Total' if gender_filter == "Total (Both)" else 'VitA_6-11m_M' if gender_filter == "Male" else 'VitA_6-11m_F'
                c2_col_va = 'VitA_12-59m_Total' if gender_filter == "Total (Both)" else 'VitA_12-59m_M' if gender_filter == "Male" else 'VitA_12-59m_F'
                
                kpi1, kpi2, kpi3 = st.columns(3)
                kpi1.metric("Total Vit A Eligible", f"{df_view_va[t_col_va].sum():,.0f}")
                kpi2.metric("6 - 11 months", f"{df_view_va[c1_col_va].sum():,.0f}")
                kpi3.metric("12 - 59 months", f"{df_view_va[c2_col_va].sum():,.0f}")
                
                if age_filter == "Vit A: 6 - 11 months":
                    plot_col_va = c1_col_va
                    chart_title_va = f"Eligible Children (6-11m, {gender_filter})"
                elif age_filter == "Vit A: 12 - 59 months":
                    plot_col_va = c2_col_va
                    chart_title_va = f"Eligible Children (12-59m, {gender_filter})"
                else:
                    plot_col_va = t_col_va
                    chart_title_va = f"Eligible Children (Total, {gender_filter})"

                if not df_view_va.empty:
                    # Full width bar chart
                    df_sorted_va = df_view_va.sort_values(plot_col_va, ascending=True) 
                    fig_va = px.bar(df_sorted_va, x=plot_col_va, y='Location', orientation='h', text_auto='.0f', color_discrete_sequence=['#F4511E'])
                    fig_va.update_layout(xaxis_title=chart_title_va, yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=600, margin=dict(l=0, r=0, t=10, b=0))
                    st.plotly_chart(fig_va, use_container_width=True)
                    
                    st.markdown("<br>", unsafe_allow_html=True)
                    
                    # Centered Pie Chart below
                    pc1, pc2, pc3 = st.columns([1, 2, 1])
                    with pc2:
                        va_age_data = pd.DataFrame({
                            'Age Group': ['6-11m', '12-59m'], 
                            'Target': [df_view_va[c1_col_va].sum(), df_view_va[c2_col_va].sum()]
                        })
                        fig_donut_va = px.pie(va_age_data, names='Age Group', values='Target', hole=0.4, title=f"Age Distribution ({gender_filter})", color_discrete_sequence=['#00ACC1', '#8E24AA'])
                        fig_donut_va.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), height=400, margin=dict(l=0, r=0, t=30, b=0))
                        st.plotly_chart(fig_donut_va, use_container_width=True)

            # ==========================================
            # SUB-TAB 3: ACTUAL MR
            # ==========================================
            with tab_act_mr:
                st.markdown(f"#### Actual MR Breakdown (RHU Census): {location_label} ({gender_filter})")
                
                act_t_col = 'Act_MR_6-59m_Total' if gender_filter == "Total (Both)" else 'Act_MR_6-59m_M' if gender_filter == "Male" else 'Act_MR_6-59m_F'
                act_c1_col = 'Act_MR_6-12m_Total' if gender_filter == "Total (Both)" else 'Act_MR_6-12m_M' if gender_filter == "Male" else 'Act_MR_6-12m_F'
                act_c2_col = 'Act_MR_13-23m_Total' if gender_filter == "Total (Both)" else 'Act_MR_13-23m_M' if gender_filter == "Male" else 'Act_MR_13-23m_F'
                act_c3_col = 'Act_MR_24-59m_Total' if gender_filter == "Total (Both)" else 'Act_MR_24-59m_M' if gender_filter == "Male" else 'Act_MR_24-59m_F'
                
                kpi1, kpi2, kpi3, kpi4 = st.columns(4)
                kpi1.metric("6 - 59m (Actual Grand Total)", f"{df_view[act_t_col].sum():,.0f}")
                kpi2.metric("Actual 6 - 12 months", f"{df_view[act_c1_col].sum():,.0f}")
                kpi3.metric("Actual 13 - 23 months", f"{df_view[act_c2_col].sum():,.0f}")
                kpi4.metric("Actual 24 - 59 months", f"{df_view[act_c3_col].sum():,.0f}")

                if age_filter == "MR: 6 - 12 months":
                    act_plot_col = act_c1_col
                    act_chart_title = f"Actual Eligible Children (6-12m, {gender_filter})"
                elif age_filter == "MR: 13 - 23 months":
                    act_plot_col = act_c2_col
                    act_chart_title = f"Actual Eligible Children (13-23m, {gender_filter})"
                elif age_filter == "MR: 24 - 59 months":
                    act_plot_col = act_c3_col
                    act_chart_title = f"Actual Eligible Children (24-59m, {gender_filter})"
                else:
                    act_plot_col = act_t_col
                    act_chart_title = f"Actual Eligible Children (Total, {gender_filter})"
                
                if not df_view.empty:
                    # Full width bar chart
                    df_sorted_act_mr = df_view.sort_values(act_plot_col, ascending=True) 
                    fig_act_mr = px.bar(df_sorted_act_mr, x=act_plot_col, y='Location', orientation='h', text_auto='.0f', color_discrete_sequence=['#43A047'])
                    fig_act_mr.update_layout(xaxis_title=act_chart_title, yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=600, margin=dict(l=0, r=0, t=10, b=0))
                    st.plotly_chart(fig_act_mr, use_container_width=True)
                    
                    st.markdown("<br>", unsafe_allow_html=True)
                    
                    # Centered Pie Chart below
                    pc1, pc2, pc3 = st.columns([1, 2, 1])
                    with pc2:
                        act_mr_age_data = pd.DataFrame({
                            'Age Group': ['6-12m', '13-23m', '24-59m'], 
                            'Target': [df_view[act_c1_col].sum(), df_view[act_c2_col].sum(), df_view[act_c3_col].sum()]
                        })
                        fig_donut_act_mr = px.pie(act_mr_age_data, names='Age Group', values='Target', hole=0.4, title=f"Actual Age Distribution ({gender_filter})", color_discrete_sequence=['#E53935', '#FFB300', '#43A047'])
                        fig_donut_act_mr.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), height=400, margin=dict(l=0, r=0, t=30, b=0))
                        st.plotly_chart(fig_donut_act_mr, use_container_width=True)

            # ==========================================
            # SUB-TAB 4: ACTUAL VITAMIN A
            # ==========================================
            with tab_act_va:
                st.markdown(f"#### Actual Vitamin A Breakdown (RHU Census): {location_label} ({gender_filter})")
                if va_warning:
                    st.warning("⚠️ The official database does not contain Barangay-level targets for Vitamin A. Displaying the overall Municipal target instead.")
                
                act_t_col_va = 'Act_VitA_Total' if gender_filter == "Total (Both)" else 'Act_VitA_Total_M' if gender_filter == "Male" else 'Act_VitA_Total_F'
                act_c1_col_va = 'Act_VitA_6-11m_Total' if gender_filter == "Total (Both)" else 'Act_VitA_6-11m_M' if gender_filter == "Male" else 'Act_VitA_6-11m_F'
                act_c2_col_va = 'Act_VitA_12-59m_Total' if gender_filter == "Total (Both)" else 'Act_VitA_12-59m_M' if gender_filter == "Male" else 'Act_VitA_12-59m_F'
                
                kpi1, kpi2, kpi3 = st.columns(3)
                kpi1.metric("Total Actual Vit A", f"{df_view_va[act_t_col_va].sum():,.0f}")
                kpi2.metric("Actual 6 - 11 months", f"{df_view_va[act_c1_col_va].sum():,.0f}")
                kpi3.metric("Actual 12 - 59 months", f"{df_view_va[act_c2_col_va].sum():,.0f}")
                
                if age_filter == "Vit A: 6 - 11 months":
                    act_plot_col_va = act_c1_col_va
                    act_chart_title_va = f"Actual Eligible Children (6-11m, {gender_filter})"
                elif age_filter == "Vit A: 12 - 59 months":
                    act_plot_col_va = act_c2_col_va
                    act_chart_title_va = f"Actual Eligible Children (12-59m, {gender_filter})"
                else:
                    act_plot_col_va = act_t_col_va
                    act_chart_title_va = f"Actual Eligible Children (Total, {gender_filter})"

                if not df_view_va.empty:
                    # Full width bar chart
                    df_sorted_act_va = df_view_va.sort_values(act_plot_col_va, ascending=True) 
                    fig_act_va = px.bar(df_sorted_act_va, x=act_plot_col_va, y='Location', orientation='h', text_auto='.0f', color_discrete_sequence=['#00ACC1'])
                    fig_act_va.update_layout(xaxis_title=act_chart_title_va, yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=600, margin=dict(l=0, r=0, t=10, b=0))
                    st.plotly_chart(fig_act_va, use_container_width=True)
                    
                    st.markdown("<br>", unsafe_allow_html=True)
                    
                    # Centered Pie Chart below
                    pc1, pc2, pc3 = st.columns([1, 2, 1])
                    with pc2:
                        act_va_age_data = pd.DataFrame({
                            'Age Group': ['6-11m', '12-59m'], 
                            'Target': [df_view_va[act_c1_col_va].sum(), df_view_va[act_c2_col_va].sum()]
                        })
                        fig_donut_act_va = px.pie(act_va_age_data, names='Age Group', values='Target', hole=0.4, title=f"Actual Age Distribution ({gender_filter})", color_discrete_sequence=['#00ACC1', '#8E24AA'])
                        fig_donut_act_va.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), height=400, margin=dict(l=0, r=0, t=30, b=0))
                        st.plotly_chart(fig_donut_act_va, use_container_width=True)

            # ==========================================
            # SUB-TAB 5: COMPARISON (NATIONAL VS ACTUAL)
            # ==========================================
            with tab_compare:
                st.markdown(f"#### ⚖️ Target Variance Analysis: {location_label}")
                st.write("Compare the baseline National allocations against the Actual RHU reported census to identify geographic shortfalls or over-allocations.")
                
                comp_prog = st.radio("Select Program for Comparison:", ["Measles-Rubella (MR)", "Vitamin A (Vit A)"], horizontal=True)
                
                if comp_prog == "Measles-Rubella (MR)":
                    df_comp = df_view.copy()
                    nat_col = 'MR_6-59m_Total'
                    act_col = 'Act_MR_6-59m_Total'
                else:
                    df_comp = df_view_va.copy()
                    nat_col = 'VitA_Total'
                    act_col = 'Act_VitA_Total'
                    if va_warning:
                        st.warning("⚠️ Vitamin A comparisons are limited to the Municipal level.")
                        
                if not df_comp.empty:
                    df_comp['Variance'] = df_comp[act_col] - df_comp[nat_col]
                    
                    total_nat_comp = df_comp[nat_col].sum()
                    total_act_comp = df_comp[act_col].sum()
                    total_variance = total_act_comp - total_nat_comp
                    
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Sum: National Target", f"{total_nat_comp:,.0f}")
                    c2.metric("Sum: Actual Target", f"{total_act_comp:,.0f}")
                    c3.metric("Net Variance", f"{total_variance:,.0f}", delta_color="inverse")
                    
                    st.write("")
                    
                    df_melt = df_comp.melt(id_vars=['Location'], value_vars=[nat_col, act_col], var_name='Target Type', value_name='Target Count')
                    df_melt['Target Type'] = df_melt['Target Type'].replace({nat_col: 'National Target', act_col: 'Actual RHU Target'})
                    
                    fig_comp = px.bar(df_melt, x='Target Count', y='Location', color='Target Type', barmode='group', orientation='h', color_discrete_sequence=['#1E88E5', '#43A047'])
                    fig_comp.update_layout(xaxis_title="Eligible Children Count", yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=600, legend_title_text="")
                    st.plotly_chart(fig_comp, use_container_width=True)
                    
                    st.markdown("##### Detailed Breakdown")
                    df_table = df_comp[['Location', nat_col, act_col, 'Variance']].rename(columns={
                        nat_col: 'National Target',
                        act_col: 'Actual Target'
                    })
                    st.dataframe(df_table, use_container_width=True, hide_index=True)

    # ==========================================
    # MR ACCOMPLISHMENT TAB
    # ==========================================
    with tab_mr:
        st.markdown(f"### 💉 MR Accomplishment & Coverage: {location_label}")
        
        df_mr_live, _ = fetch_live_accomplishments()
        
        total_mr_doses = 0
        if not df_mr_live.empty and 'Municipality' in df_mr_live.columns:
            df_mr_filtered = df_mr_live.copy()
            
            # Filter by Sidebar Geographic View
            if view_mode == "All Municipalities (Abra)":
                df_mr_filtered = df_mr_filtered[df_mr_filtered['Municipality'].isin(df_view['Location'].tolist())]
            elif view_mode == "Specific Municipality":
                df_mr_filtered = df_mr_filtered[df_mr_filtered['Municipality'] == selected_muni]
            
            mr_dose_cols = ['MR 6-12 Male', 'MR 6-12 Female', 'MR 13-23 Male', 'MR 13-23 Female', 'MR 24-59 Male', 'MR 24-59 Female']
            
            for col in mr_dose_cols:
                if col in df_mr_filtered.columns:
                    df_mr_filtered[col] = pd.to_numeric(df_mr_filtered[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
            
            df_mr_filtered['Total Doses'] = df_mr_filtered[mr_dose_cols].sum(axis=1).astype(int)
            total_mr_doses = df_mr_filtered['Total Doses'].sum()
        else:
            df_mr_filtered = pd.DataFrame()

        nat_target = df_view['MR_6-59m_Total'].sum()
        act_target = df_view['Act_MR_6-59m_Total'].sum() if 'Act_MR_6-59m_Total' in df_view.columns else 0

        nat_cov = (total_mr_doses / nat_target * 100) if nat_target > 0 else 0
        act_cov = (total_mr_doses / act_target * 100) if act_target > 0 else 0

        col_mr1, col_mr2, col_mr3, col_mr4 = st.columns(4)
        col_mr1.metric("Total Doses Administered", f"{total_mr_doses:,.0f}")
        col_mr2.metric("National Coverage %", f"{nat_cov:.1f}%", f"{nat_target:,.0f} Nat. Target", delta_color="off")
        
        if act_target > 0:
            col_mr3.metric("Actual RHU Coverage %", f"{act_cov:.1f}%", f"{act_target:,.0f} Act. Target", delta_color="off")
        else:
            col_mr3.metric("Actual RHU Coverage %", "Awaiting Data", "RHU Sheet Empty", delta_color="off")
            
        variance = act_target - nat_target
        var_label = "More than National" if variance > 0 else "Less than National"
        col_mr4.metric("Variance (Act vs Nat)", f"{variance:,.0f}", var_label, delta_color="inverse")
        
        st.divider()
        
        # --- MR CHARTS & ANALYTICS ---
        st.markdown("#### 📈 Accomplishment Analytics")
        if not df_mr_filtered.empty:
            geo_col = 'Municipality' if view_mode != "Specific Municipality" else 'Barangay'
            
            if 'Vaccination Date' in df_mr_filtered.columns:
                df_mr_filtered['Vaccination Date'] = pd.to_datetime(df_mr_filtered['Vaccination Date'], errors='coerce')
                
            # Full width Timeline Line Chart
            if 'Vaccination Date' in df_mr_filtered.columns and not df_mr_filtered['Vaccination Date'].isna().all():
                df_time = df_mr_filtered.groupby(df_mr_filtered['Vaccination Date'].dt.date)['Total Doses'].sum().reset_index()
                fig_time = px.line(df_time, x='Vaccination Date', y='Total Doses', markers=True, title="Daily Doses Administered Trend", color_discrete_sequence=['#1E88E5'])
                fig_time.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis_title="", yaxis_title="Doses", margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_time, use_container_width=True)
            
            # Full width Geographic Bar Chart
            if geo_col in df_mr_filtered.columns:
                df_geo = df_mr_filtered.groupby(geo_col)['Total Doses'].sum().reset_index().sort_values('Total Doses', ascending=True)
                fig_geo = px.bar(df_geo, x='Total Doses', y=geo_col, orientation='h', text_auto='.0f', title=f"Doses Administered by {geo_col}", color_discrete_sequence=['#1E88E5'])
                fig_geo.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis_title="Total Doses", yaxis_title="", height=600, margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_geo, use_container_width=True)
                
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Pie Charts Side-by-Side below the bar charts
            pc1, pc2 = st.columns(2)
            with pc1:
                mr_6_12 = df_mr_filtered[['MR 6-12 Male', 'MR 6-12 Female']].sum().sum()
                mr_13_23 = df_mr_filtered[['MR 13-23 Male', 'MR 13-23 Female']].sum().sum()
                mr_24_59 = df_mr_filtered[['MR 24-59 Male', 'MR 24-59 Female']].sum().sum()
                
                df_age = pd.DataFrame({'Age Group': ['6-12m', '13-23m', '24-59m'], 'Doses': [mr_6_12, mr_13_23, mr_24_59]})
                fig_age = px.pie(df_age, names='Age Group', values='Doses', hole=0.4, title="By Age Group", color_discrete_sequence=['#E53935', '#FFB300', '#43A047'])
                fig_age.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_age, use_container_width=True)
                
            with pc2:
                mr_male = df_mr_filtered[['MR 6-12 Male', 'MR 13-23 Male', 'MR 24-59 Male']].sum().sum()
                mr_female = df_mr_filtered[['MR 6-12 Female', 'MR 13-23 Female', 'MR 24-59 Female']].sum().sum()
                
                df_gender = pd.DataFrame({'Gender': ['Male', 'Female'], 'Doses': [mr_male, mr_female]})
                fig_gender = px.pie(df_gender, names='Gender', values='Doses', hole=0.4, title="By Gender", color_discrete_sequence=['#1E88E5', '#D81B60'])
                fig_gender.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_gender, use_container_width=True)
                
            st.divider()

            # ==========================================
            # 📅 DAILY TALLY SHEET GRID (MR)
            # ==========================================
            st.divider()
            st.markdown("#### 📅 Daily Tally Sheet Grid")
            st.write("Use this grid to easily copy daily totals to your physical office tally board.")
            
            if not df_mr_filtered.empty and 'Vaccination Date' in df_mr_filtered.columns:
                df_tally_mr = df_mr_filtered.copy()
                
                # Convert to datetime and extract just the day number
                df_tally_mr['Vaccination Date'] = pd.to_datetime(df_tally_mr['Vaccination Date'], errors='coerce')
                df_tally_mr = df_tally_mr.dropna(subset=['Vaccination Date'])
                df_tally_mr['Day'] = df_tally_mr['Vaccination Date'].dt.day.astype(int)
                
                # Create the pivot table
                tally_grid_mr = pd.pivot_table(
                    df_tally_mr, 
                    values='Total Doses', 
                    index='Municipality', 
                    columns='Day', 
                    aggfunc='sum',
                    fill_value=0
                )
                
                # Force all 27 Abra Municipalities to display as rows, even if they have 0 doses
                tally_grid_mr = tally_grid_mr.reindex(abra_munis, fill_value=0)
                
                # Force columns 1 through 31 to display for the days of the month
                # (Change 32 to 29 if you strictly only want 28 days showing)
                days_cols = list(range(1, 32))
                tally_grid_mr = tally_grid_mr.reindex(columns=days_cols, fill_value=0)
                
                # Replace zeros with empty strings to make it look exactly like a blank paper tally sheet
                tally_grid_mr = tally_grid_mr.replace(0, "")
                
                st.dataframe(tally_grid_mr, use_container_width=True)
            else:
                st.info("Awaiting vaccination date records to generate the tally board.")
            
            st.markdown("#### 📥 Raw Data Export")
            with st.expander("View & Download Raw MR Accomplishment Data"):
                st.dataframe(df_mr_filtered, use_container_width=True)
                csv_mr = df_mr_filtered.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download MR Data (CSV)",
                    data=csv_mr,
                    file_name=f"MR_Accomplishment_{location_label.replace(', ', '_').replace(' ', '_')}.csv",
                    mime="text/csv"
                )
        else:
            st.info("Awaiting Gsheet Sync to populate analytics.")

    # ==========================================
    # VITAMIN A ACCOMPLISHMENT TAB
    # ==========================================
    with tab_vita:
        st.markdown(f"### 💊 Vitamin A Accomplishment: {location_label}")
        
        _, df_vita_live = fetch_live_accomplishments()
        
        total_vita_doses = 0
        if not df_vita_live.empty and 'Municipality' in df_vita_live.columns:
            df_vita_filtered = df_vita_live.copy()
            
            # Filter by Sidebar Geographic View
            if view_mode == "All Municipalities (Abra)":
                df_vita_filtered = df_vita_filtered[df_vita_filtered['Municipality'].isin(df_view['Location'].tolist())]
            elif view_mode == "Specific Municipality":
                df_vita_filtered = df_vita_filtered[df_vita_filtered['Municipality'] == selected_muni]
            
            vita_dose_cols = ['VitA 6-11 Male', 'VitA 6-11 Female', 'VitA 12-59 Male', 'VitA 12-59 Female']
            
            for col in vita_dose_cols:
                if col in df_vita_filtered.columns:
                    df_vita_filtered[col] = pd.to_numeric(df_vita_filtered[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
            
            df_vita_filtered['Total Doses'] = df_vita_filtered[vita_dose_cols].sum(axis=1).astype(int)
            total_vita_doses = df_vita_filtered['Total Doses'].sum()
        else:
            df_vita_filtered = pd.DataFrame()
            
        nat_target_va = df_view_va['VitA_Total'].sum() if not df_view_va.empty else 0
        act_target_va = df_view_va['Act_VitA_Total'].sum() if not df_view_va.empty and 'Act_VitA_Total' in df_view_va.columns else 0

        nat_cov_va = (total_vita_doses / nat_target_va * 100) if nat_target_va > 0 else 0
        act_cov_va = (total_vita_doses / act_target_va * 100) if act_target_va > 0 else 0

        col_va1, col_va2, col_va3, col_va4 = st.columns(4)
        col_va1.metric("Total Doses Administered", f"{total_vita_doses:,.0f}")
        col_va2.metric("National Coverage %", f"{nat_cov_va:.1f}%", f"{nat_target_va:,.0f} Nat. Target", delta_color="off")
        
        if act_target_va > 0:
            col_va3.metric("Actual RHU Coverage %", f"{act_cov_va:.1f}%", f"{act_target_va:,.0f} Act. Target", delta_color="off")
        else:
            col_va3.metric("Actual RHU Coverage %", "Awaiting Data", "RHU Sheet Empty", delta_color="off")
            
        variance_va = act_target_va - nat_target_va
        var_label_va = "More than National" if variance_va > 0 else "Less than National"
        col_va4.metric("Variance (Act vs Nat)", f"{variance_va:,.0f}", var_label_va, delta_color="inverse")
        
        st.divider()

        # --- VITAMIN A CHARTS & ANALYTICS ---
        st.markdown("#### 📈 Accomplishment Analytics")
        if not df_vita_filtered.empty:
            geo_col_va = 'Municipality' if view_mode != "Specific Municipality" else 'Barangay'
            
            if 'Vaccination Date' in df_vita_filtered.columns:
                df_vita_filtered['Vaccination Date'] = pd.to_datetime(df_vita_filtered['Vaccination Date'], errors='coerce')
                
            # Full width Timeline Line Chart
            if 'Vaccination Date' in df_vita_filtered.columns and not df_vita_filtered['Vaccination Date'].isna().all():
                df_time_va = df_vita_filtered.groupby(df_vita_filtered['Vaccination Date'].dt.date)['Total Doses'].sum().reset_index()
                fig_time_va = px.line(df_time_va, x='Vaccination Date', y='Total Doses', markers=True, title="Daily Doses Administered Trend", color_discrete_sequence=['#F4511E'])
                fig_time_va.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis_title="", yaxis_title="Doses", margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_time_va, use_container_width=True)
            
            # Full width Geographic Bar Chart
            if geo_col_va in df_vita_filtered.columns:
                df_geo_va = df_vita_filtered.groupby(geo_col_va)['Total Doses'].sum().reset_index().sort_values('Total Doses', ascending=True)
                fig_geo_va = px.bar(df_geo_va, x='Total Doses', y=geo_col_va, orientation='h', text_auto='.0f', title=f"Doses Administered by {geo_col_va}", color_discrete_sequence=['#F4511E'])
                fig_geo_va.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis_title="Total Doses", yaxis_title="", height=600, margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_geo_va, use_container_width=True)
                
            st.markdown("<br>", unsafe_allow_html=True)
                
            # Pie Charts Side-by-Side below the bar charts
            pc1_va, pc2_va = st.columns(2)
            with pc1_va:
                va_6_11 = df_vita_filtered[['VitA 6-11 Male', 'VitA 6-11 Female']].sum().sum()
                va_12_59 = df_vita_filtered[['VitA 12-59 Male', 'VitA 12-59 Female']].sum().sum()
                
                df_age_va = pd.DataFrame({'Age Group': ['6-11m', '12-59m'], 'Doses': [va_6_11, va_12_59]})
                fig_age_va = px.pie(df_age_va, names='Age Group', values='Doses', hole=0.4, title="By Age Group", color_discrete_sequence=['#00ACC1', '#8E24AA'])
                fig_age_va.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_age_va, use_container_width=True)
                
            with pc2_va:
                va_male = df_vita_filtered[['VitA 6-11 Male', 'VitA 12-59 Male']].sum().sum()
                va_female = df_vita_filtered[['VitA 6-11 Female', 'VitA 12-59 Female']].sum().sum()
                
                df_gender_va = pd.DataFrame({'Gender': ['Male', 'Female'], 'Doses': [va_male, va_female]})
                fig_gender_va = px.pie(df_gender_va, names='Gender', values='Doses', hole=0.4, title="By Gender", color_discrete_sequence=['#1E88E5', '#D81B60'])
                fig_gender_va.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_gender_va, use_container_width=True)

            st.divider()

            # ==========================================
            # 📅 DAILY TALLY SHEET GRID (VIT A)
            # ==========================================
            st.divider()
            st.markdown("#### 📅 Daily Tally Sheet Grid")
            st.write("Use this grid to easily copy daily totals to your physical office tally board.")
            
            if not df_vita_filtered.empty and 'Vaccination Date' in df_vita_filtered.columns:
                df_tally_va = df_vita_filtered.copy()
                
                # Convert to datetime and extract just the day number
                df_tally_va['Vaccination Date'] = pd.to_datetime(df_tally_va['Vaccination Date'], errors='coerce')
                df_tally_va = df_tally_va.dropna(subset=['Vaccination Date'])
                df_tally_va['Day'] = df_tally_va['Vaccination Date'].dt.day.astype(int)
                
                # Create the pivot table
                tally_grid_va = pd.pivot_table(
                    df_tally_va, 
                    values='Total Doses', 
                    index='Municipality', 
                    columns='Day', 
                    aggfunc='sum',
                    fill_value=0
                )
                
                # Force all 27 Abra Municipalities to display as rows
                tally_grid_va = tally_grid_va.reindex(abra_munis, fill_value=0)
                
                # Force columns 1 through 31 
                days_cols = list(range(1, 32))
                tally_grid_va = tally_grid_va.reindex(columns=days_cols, fill_value=0)
                
                # Replace zeros with empty strings 
                tally_grid_va = tally_grid_va.replace(0, "")
                
                st.dataframe(tally_grid_va, use_container_width=True)
            else:
                st.info("Awaiting vaccination date records to generate the tally board.")
            
            st.markdown("#### 📥 Raw Data Export")
            with st.expander("View & Download Raw Vitamin A Accomplishment Data"):
                st.dataframe(df_vita_filtered, use_container_width=True)
                csv_va = df_vita_filtered.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Vitamin A Data (CSV)",
                    data=csv_va,
                    file_name=f"VitA_Accomplishment_{location_label.replace(', ', '_').replace(' ', '_')}.csv",
                    mime="text/csv"
                )
        else:
            st.info("Awaiting Gsheet Sync to populate analytics.")
        
    # ==========================================
    # DEFERRAL & REFUSAL ANALYSIS TAB
    # ==========================================
    with tab_def_ref:
        st.markdown(f"### 📉 Deferral and Refusal Analysis: {location_label}")
        st.write("Deep dive into the specific reasons for missed vaccination targets based on RHU reports.")
        
        df_mr_live, df_vita_live = fetch_live_accomplishments()
        
        # Helper function to generate clean, full-width charts
        def plot_reasons(df, cols, title, color):
            if not cols:
                return st.warning(f"⚠️ Could not find data columns for {title}")
            
            # Clean and sum the data
            for c in cols:
                df[c] = pd.to_numeric(df[c].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            df_sum = df[cols].sum().reset_index()
            df_sum.columns = ['Reason', 'Count']
            df_sum = df_sum[df_sum['Count'] > 0].sort_values('Count', ascending=True)
            
            if not df_sum.empty:
                # Truncate extremely long reasons so the chart doesn't shrink
                df_sum['Short Reason'] = df_sum['Reason'].apply(lambda x: (str(x)[:85] + '...') if len(str(x)) > 85 else str(x))
                
                fig = px.bar(df_sum, x='Count', y='Short Reason', orientation='h', text_auto='.0f', title=title, color_discrete_sequence=[color])
                fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis_title="Total Cases", yaxis_title="", height=max(350, len(df_sum)*45), margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info(f"✅ No {title.lower()} have been recorded for this location yet.")

        # Create Sub-Tabs for MR and Vit A
        tab_mr_reasons, tab_va_reasons = st.tabs(["💉 MR Reasons", "💊 Vit A Reasons"])
        
        with tab_mr_reasons:
            if not df_mr_live.empty and 'Municipality' in df_mr_live.columns:
                df_mr_filtered = df_mr_live.copy()
                
                # Apply Geographic Filter
                if view_mode == "All Municipalities (Abra)":
                    df_mr_filtered = df_mr_filtered[df_mr_filtered['Municipality'].isin(df_view['Location'].tolist())]
                elif view_mode == "Specific Municipality":
                    df_mr_filtered = df_mr_filtered[df_mr_filtered['Municipality'] == selected_muni]
                
                # Group columns: C1 to C6 (Deferrals), C7 to C23 (Refusals)
                def_prefixes = tuple([f"C{i} " for i in range(1, 7)])
                ref_prefixes = tuple([f"C{i} " for i in range(7, 24)])
                
                reason_cols_mr_def = [col for col in df_mr_filtered.columns if str(col).startswith(def_prefixes)]
                reason_cols_mr_ref = [col for col in df_mr_filtered.columns if str(col).startswith(ref_prefixes)]
                
                # Plot Full Width Charts
                plot_reasons(df_mr_filtered, reason_cols_mr_def, "MR Deferrals (C1 - C6)", '#FFB300') # Yellow/Orange for Deferral
                st.markdown("<br>", unsafe_allow_html=True)
                plot_reasons(df_mr_filtered, reason_cols_mr_ref, "MR Refusals (C7 - C23)", '#E53935') # Red for Refusal
                    
            else:
                st.info("Awaiting Gsheet Sync to populate analytics.")

        # ==========================================
        # RAW DATA EXPORT: DEFERRALS & REFUSALS
        # ==========================================
        st.divider()
        st.markdown("#### 📥 Raw Data Export")
        
        # We use sub-tabs here to keep the UI clean
        raw_tab_mr, raw_tab_va = st.tabs(["MR Deferrals & Refusals", "Vit A Deferrals & Refusals"])
        
        with raw_tab_mr:
            if not df_mr_live.empty:
                # 1. Apply Location Filter
                if view_mode == "All Municipalities (Abra)":
                    df_mr_raw = df_mr_live[df_mr_live['Municipality'].isin(df_view['Location'].tolist())].copy()
                else:
                    df_mr_raw = df_mr_live[df_mr_live['Municipality'] == selected_muni].copy()
                
                # 2. Filter only rows that have a Deferral or Refusal Reason
                reason_cols = [c for c in df_mr_raw.columns if 'Reason' in c or 'Deferral' in c or 'Refusal' in c]
                if reason_cols:
                    # FIX: Convert floats/NaNs to empty strings before stripping
                    mask = df_mr_raw[reason_cols].fillna('').astype(str).apply(lambda x: x.str.strip() != '')
                    df_mr_def_only = df_mr_raw[mask.any(axis=1)]
                else:
                    df_mr_def_only = df_mr_raw 
                
                if not df_mr_def_only.empty:
                    with st.expander("View & Download Raw MR Deferral/Refusal Data", expanded=False):
                        st.dataframe(df_mr_def_only, use_container_width=True)
                        csv_mr = df_mr_def_only.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="⬇️ Download MR Data (CSV)",
                            data=csv_mr,
                            file_name=f"MR_Deferrals_Refusals_{location_label.replace(', ', '_')}.csv",
                            mime="text/csv",
                            key="dl_mr_def_raw"
                        )
                else:
                    st.info(f"No MR deferrals or refusals recorded yet for {location_label}.")
            else:
                st.warning("MR Accomplishment data is empty.")

        with raw_tab_va:
            if not df_vita_live.empty:
                # 1. Apply Location Filter
                if view_mode == "All Municipalities (Abra)":
                    df_va_raw = df_vita_live[df_vita_live['Municipality'].isin(df_view['Location'].tolist())].copy()
                else:
                    df_va_raw = df_vita_live[df_vita_live['Municipality'] == selected_muni].copy()
                
                # 2. Filter only rows that have a Deferral or Refusal Reason
                reason_cols_va = [c for c in df_va_raw.columns if 'Reason' in c or 'Deferral' in c or 'Refusal' in c]
                if reason_cols_va:
                    # FIX: Convert floats/NaNs to empty strings before stripping
                    mask_va = df_va_raw[reason_cols_va].fillna('').astype(str).apply(lambda x: x.str.strip() != '')
                    df_va_def_only = df_va_raw[mask_va.any(axis=1)]
                else:
                    df_va_def_only = df_va_raw 

                if not df_va_def_only.empty:
                    with st.expander("View & Download Raw Vit A Deferral/Refusal Data", expanded=False):
                        st.dataframe(df_va_def_only, use_container_width=True)
                        csv_va = df_va_def_only.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="⬇️ Download Vit A Data (CSV)",
                            data=csv_va,
                            file_name=f"VitA_Deferrals_Refusals_{location_label.replace(', ', '_')}.csv",
                            mime="text/csv",
                            key="dl_va_def_raw"
                        )
                else:
                    st.info(f"No Vit A deferrals or refusals recorded yet for {location_label}.")
            else:
                st.warning("Vit A Accomplishment data is empty.")
                
        with tab_va_reasons:
            if not df_vita_live.empty and 'Municipality' in df_vita_live.columns:
                df_va_filtered = df_vita_live.copy()
                
                # Apply Geographic Filter
                if view_mode == "All Municipalities (Abra)":
                    df_va_filtered = df_va_filtered[df_va_filtered['Municipality'].isin(df_view['Location'].tolist())]
                elif view_mode == "Specific Municipality":
                    df_va_filtered = df_va_filtered[df_va_filtered['Municipality'] == selected_muni]
                
                # Group columns: VIT1,3,4,5 (Deferrals), VIT2 (Refusals)
                va_def_prefixes = ('VIT1 ', 'VIT3 ', 'VIT4 ', 'VIT5 ')
                va_ref_prefixes = ('VIT2 ')
                
                reason_cols_va_def = [col for col in df_va_filtered.columns if str(col).startswith(va_def_prefixes)]
                reason_cols_va_ref = [col for col in df_va_filtered.columns if str(col).startswith(va_ref_prefixes)]
                
                # Catch any extra VIT columns
                all_vit = [col for col in df_va_filtered.columns if str(col).startswith('VIT')]
                missed = [c for c in all_vit if c not in reason_cols_va_def and c not in reason_cols_va_ref]
                if missed:
                    reason_cols_va_ref.extend(missed) 
                
                # Plot Full Width Charts
                plot_reasons(df_va_filtered, reason_cols_va_def, "Vitamin A Deferrals", '#00ACC1') 
                st.markdown("<br>", unsafe_allow_html=True)
                plot_reasons(df_va_filtered, reason_cols_va_ref, "Vitamin A Refusals", '#8E24AA')  

                # Moved the Export block INSIDE the if-statement so it doesn't crash on empty data!
                st.divider()
                st.markdown("#### Raw Data Export")
                with st.expander("View & Download Raw Vit A Deferral/Refusal Data"):
                    st.dataframe(df_va_filtered, use_container_width=True)
                    csv_va_def = df_va_filtered.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="Download Vit A Data (CSV)",
                        data=csv_va_def,
                        file_name=f"VitA_Deferrals_Refusals_{location_label.replace(', ', '_')}.csv",
                        mime="text/csv",
                        key="dl_va_def"
                    )
            else:
                st.info("Awaiting Gsheet Sync to populate analytics.")

    # ==========================================
    # ADMIN PANEL
    # ==========================================
    if is_admin:
        with tab_admin:
            st.markdown("### 🔄 Phase 1: Target Database Sync")
            st.write("Pull the latest baseline targets from the `Target(CAR)` Google Sheet.")
            
            if st.button("Sync Target Database", type="secondary", use_container_width=True):
                with st.spinner("Downloading National & Actual Targets from 4 Sheets..."):
                    try:
                        conn = st.connection("gsheets", type=GSheetsConnection)
                        
                        mr_cols = ["Code", "Location", "6-59m_M", "6-59m_F", "6-59m_Total", "6-12m_M", "6-12m_F", "6-12m_Total", "13-23m_M", "13-23m_F", "13-23m_Total", "24-59m_M", "24-59m_F", "24-59m_Total"]
                        df_mr_nat = clean_and_process_car_data(conn.read(spreadsheet=sheet_url, worksheet="MR Target(CAR)".strip(), usecols=list(range(14)), skiprows=2, names=mr_cols, ttl=0), mr_cols)
                        
                        mr_act_cols = ["Code", "Location", "Act_MR_6-59m_M", "Act_MR_6-59m_F", "Act_MR_6-59m_Total", "Act_MR_6-12m_M", "Act_MR_6-12m_F", "Act_MR_6-12m_Total", "Act_MR_13-23m_M", "Act_MR_13-23m_F", "Act_MR_13-23m_Total", "Act_MR_24-59m_M", "Act_MR_24-59m_F", "Act_MR_24-59m_Total"]
                        df_mr_act = clean_and_process_car_data(conn.read(spreadsheet=sheet_url, worksheet="MR Actual Target(UPDATE THIS)".strip(), usecols=list(range(14)), skiprows=2, names=mr_act_cols, ttl=0), mr_act_cols)
                        
                        vita_cols = ["Code", "Location", "VitA_6-11m_M", "VitA_6-11m_F", "VitA_6-11m_Total", "VitA_12-59m_M", "VitA_12-59m_F", "VitA_12-59m_Total", "VitA_Total"]
                        df_vita_nat = clean_and_process_car_data(conn.read(spreadsheet=sheet_url, worksheet="Vitamin A Target".strip(), usecols=[0, 2, 3, 4, 5, 6, 7, 8, 9], skiprows=2, names=vita_cols, ttl=0), vita_cols)
                        
                        vita_act_cols = ["Code", "Location", "Act_VitA_6-11m_M", "Act_VitA_6-11m_F", "Act_VitA_6-11m_Total", "Act_VitA_12-59m_M", "Act_VitA_12-59m_F", "Act_VitA_12-59m_Total", "Act_VitA_Total"]
                        df_vita_act = clean_and_process_car_data(conn.read(spreadsheet=sheet_url, worksheet="Vitamin A Actual Target(UPDATE THIS)".strip(), usecols=[0, 2, 3, 4, 5, 6, 7, 8, 9], skiprows=2, names=vita_act_cols, ttl=0), vita_act_cols)
                        
                        for c in ["VitA_6-11m_M", "VitA_12-59m_M", "VitA_6-11m_F", "VitA_12-59m_F"]:
                            df_vita_nat[c] = pd.to_numeric(df_vita_nat[c].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                        df_vita_nat['VitA_Total_M'] = df_vita_nat['VitA_6-11m_M'] + df_vita_nat['VitA_12-59m_M']
                        df_vita_nat['VitA_Total_F'] = df_vita_nat['VitA_6-11m_F'] + df_vita_nat['VitA_12-59m_F']
                        
                        for c in ["Act_VitA_6-11m_M", "Act_VitA_12-59m_M", "Act_VitA_6-11m_F", "Act_VitA_12-59m_F"]:
                            df_vita_act[c] = pd.to_numeric(df_vita_act[c].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                        df_vita_act['Act_VitA_Total_M'] = df_vita_act['Act_VitA_6-11m_M'] + df_vita_act['Act_VitA_12-59m_M']
                        df_vita_act['Act_VitA_Total_F'] = df_vita_act['Act_VitA_6-11m_F'] + df_vita_act['Act_VitA_12-59m_F']
                        
                        df_merged = df_mr_nat.copy()
                        df_merged = pd.merge(df_merged, df_mr_act[['Code', 'Act_MR_6-59m_Total', 'Act_MR_6-59m_M', 'Act_MR_6-59m_F', 'Act_MR_6-12m_Total', 'Act_MR_6-12m_M', 'Act_MR_6-12m_F', 'Act_MR_13-23m_Total', 'Act_MR_13-23m_M', 'Act_MR_13-23m_F', 'Act_MR_24-59m_Total', 'Act_MR_24-59m_M', 'Act_MR_24-59m_F']], on='Code', how='left')
                        df_merged = pd.merge(df_merged, df_vita_nat[['Code', 'VitA_6-11m_Total', 'VitA_12-59m_Total', 'VitA_Total', 'VitA_6-11m_M', 'VitA_6-11m_F', 'VitA_12-59m_M', 'VitA_12-59m_F', 'VitA_Total_M', 'VitA_Total_F']], on='Code', how='left')
                        df_merged = pd.merge(df_merged, df_vita_act[['Code', 'Act_VitA_6-11m_Total', 'Act_VitA_6-11m_M', 'Act_VitA_6-11m_F', 'Act_VitA_12-59m_Total', 'Act_VitA_12-59m_M', 'Act_VitA_12-59m_F', 'Act_VitA_Total', 'Act_VitA_Total_M', 'Act_VitA_Total_F']], on='Code', how='left')
                        
                        df_push = df_merged[['Code', 'Location', 'Level', 'Parent_Province', 'Parent_Municipality', 
                                             '6-59m_Total', '6-12m_Total', '13-23m_Total', '24-59m_Total',
                                             '6-59m_M', '6-59m_F', '6-12m_M', '6-12m_F', '13-23m_M', '13-23m_F', '24-59m_M', '24-59m_F',
                                             'VitA_6-11m_Total', 'VitA_12-59m_Total', 'VitA_Total',
                                             'VitA_6-11m_M', 'VitA_6-11m_F', 'VitA_12-59m_M', 'VitA_12-59m_F', 'VitA_Total_M', 'VitA_Total_F',
                                             'Act_MR_6-59m_Total', 'Act_MR_6-12m_Total', 'Act_MR_13-23m_Total', 'Act_MR_24-59m_Total',
                                             'Act_VitA_6-11m_Total', 'Act_VitA_12-59m_Total', 'Act_VitA_Total',
                                             'Act_MR_6-59m_M', 'Act_MR_6-59m_F', 'Act_MR_6-12m_M', 'Act_MR_6-12m_F', 'Act_MR_13-23m_M', 'Act_MR_13-23m_F', 'Act_MR_24-59m_M', 'Act_MR_24-59m_F',
                                             'Act_VitA_6-11m_M', 'Act_VitA_6-11m_F', 'Act_VitA_12-59m_M', 'Act_VitA_12-59m_F', 'Act_VitA_Total_M', 'Act_VitA_Total_F']].copy()
                        
                        df_push.columns = [
                            'code', 'location', 'level', 'parent_province', 'parent_municipality', 
                            'grand_total_6_59m', 'grand_total_6_12m', 'grand_total_13_23m', 'grand_total_24_59m',
                            'mr_6_59m_m', 'mr_6_59m_f', 'mr_6_12m_m', 'mr_6_12m_f', 'mr_13_23m_m', 'mr_13_23m_f', 'mr_24_59m_m', 'mr_24_59m_f',
                            'vita_6_11m', 'vita_12_59m', 'vita_total',
                            'vita_6_11m_m', 'vita_6_11m_f', 'vita_12_59m_m', 'vita_12_59m_f', 'vita_total_m', 'vita_total_f',
                            'actual_mr_6_59m_total', 'actual_mr_6_12m_total', 'actual_mr_13_23m_total', 'actual_mr_24_59m_total',
                            'actual_vita_6_11m_total', 'actual_vita_12_59m_total', 'actual_vita_total',
                            'actual_mr_6_59m_m', 'actual_mr_6_59m_f', 'actual_mr_6_12m_m', 'actual_mr_6_12m_f', 'actual_mr_13_23m_m', 'actual_mr_13_23m_f', 'actual_mr_24_59m_m', 'actual_mr_24_59m_f',
                            'actual_vita_6_11m_m', 'actual_vita_6_11m_f', 'actual_vita_12_59m_m', 'actual_vita_12_59m_f', 'actual_vita_total_m', 'actual_vita_total_f'
                        ]
                        
                        num_cols = df_push.columns[5:]
                        for c in num_cols:
                            df_push[c] = pd.to_numeric(df_push[c], errors='coerce').fillna(0).astype(int)
                        
                        df_push = df_push.replace({np.nan: None})
                        supabase.table('targets').upsert(df_push.to_dict(orient='records')).execute()
                        
                        st.success("✅ Mega-Sync Complete: Actual Genders Fully Integrated!")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"Target Sync Failed: {e}")
                       
            st.divider()
            
            st.markdown("### 🔐 User Account Management")
            
            # --- NEW: SECURE ACCOUNT CREATION FORM ---
            with st.expander("➕ Create New Account"):
                with st.form("create_account_form"):
                    new_user = st.text_input("Username")
                    new_pass = st.text_input("Password", type="password")
                    new_role = st.selectbox("Role", ["Guest / Viewer", "System Admin"])
                    
                    submit_new_account = st.form_submit_button("Create Account", type="primary")
                    
                    if submit_new_account:
                        if not new_user or not new_pass:
                            st.warning("Please enter both username and password.")
                        else:
                            try:
                                supabase.table('user_accounts').insert({
                                    "username": new_user.strip(),
                                    "password_hash": make_hashes(new_pass),
                                    "name": "RHU Visitor" if new_role == "Guest / Viewer" else "System Admin",
                                    "role": new_role,
                                    "account_status": "Approved",
                                    "failed_attempts": 0
                                }).execute()
                                st.success(f"✅ Account '{new_user}' successfully created!")
                                time.sleep(1)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error creating account. Username may already exist. Details: {e}")

            # --- UPDATED: ACCOUNT EDITOR WITH DELETION LOGIC ---
            res_users = supabase.table('user_accounts').select('*').execute()
            if res_users.data:
                users_admin_df = pd.DataFrame(res_users.data)
                
                cols = ['username', 'role', 'password_hash']
                users_admin_df = users_admin_df[[c for c in cols if c in users_admin_df.columns]]
                
                # Keep track of original usernames to detect if you delete a row
                original_usernames = set(users_admin_df['username'].dropna().tolist())
                
                st.caption("Select a row on the left side and press 'Delete' on your keyboard to remove an account.")
                edited_users = st.data_editor(
                    users_admin_df,
                    column_config={
                        "password_hash": None, 
                        "username": st.column_config.TextColumn("Username", disabled=True),
                        "role": st.column_config.SelectboxColumn("Role", options=["Guest / Viewer", "System Admin"])
                    },
                    use_container_width=True,
                    num_rows="dynamic",
                    key="user_editor"
                )
                
                if st.button("💾 Save User Changes", type="secondary"):
                    try:
                        # 1. Detect and execute Deletions in Supabase
                        current_usernames = set(edited_users['username'].dropna().tolist())
                        deleted_users = list(original_usernames - current_usernames)
                        
                        if deleted_users:
                            supabase.table('user_accounts').delete().in_('username', deleted_users).execute()
                        
                        # 2. Fix the NaN JSON error by cleaning empty data
                        edited_users = edited_users.dropna(subset=['username']) # Ignore accidental blank rows
                        edited_users = edited_users.replace({np.nan: None})     # Replace Pandas NaN with clean nulls
                        
                        updated_records = edited_users.to_dict(orient='records')
                        if updated_records:
                            supabase.table('user_accounts').upsert(updated_records).execute()
                            
                        st.toast("User accounts updated successfully!", icon="✅")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to update users: {e}")
            
            st.divider()

            st.markdown("### 📋 System Access Logs & Session Tracking")
            try:
                # Pull the latest 100 logs from Supabase
                res_logs = supabase.table('access_logs').select('*').order('id', desc=True).limit(100).execute()
                
                if res_logs.data:
                    # Convert to dataframe
                    logs_df = pd.DataFrame(res_logs.data)
                    
                    # Make sure the 'action' column exists in case older logs don't have it
                    if 'action' not in logs_df.columns:
                        logs_df['action'] = "Legacy Login"
                        
                    # Filter to show the columns we care about, INCLUDING the new action column
                    display_cols = ['timestamp', 'name', 'role', 'action']
                    logs_df = logs_df[[c for c in display_cols if c in logs_df.columns]]
                    
                    st.dataframe(logs_df, use_container_width=True, hide_index=True)
                else:
                    st.info("No access logs found yet.")
            except Exception as e:
                st.warning(f"Could not load Access Logs: {e}")

except Exception as e:
    st.error(f"Dashboard Error: {e}")
