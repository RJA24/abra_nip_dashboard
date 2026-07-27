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

# ==========================================
# 1. PAGE CONFIGURATION & UI/UX STYLING
# ==========================================
st.set_page_config(page_title="Abra SIA 2026 Tracker", page_icon="💉", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    footer {visibility: hidden;}
    [data-testid="stMetric"] {
        background-color: var(--secondary-background-color);
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: 8px;
        padding: 15px 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        transition: transform 0.2s ease-in-out;
    }
    [data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 12px rgba(0,0,0,0.2);
    }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        background-color: var(--secondary-background-color);
        border-radius: 4px 4px 0px 0px;
        padding: 10px 20px;
    }
    .stTabs [aria-selected="true"] {
        background-color: var(--background-color);
        border-bottom: 2px solid var(--primary-color);
        font-weight: bold;
    }
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
        st.title("🔒 Abra SIA 2026")
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
                                    supabase.table('access_logs').insert({'timestamp': current_time_str, 'name': db_name, 'role': db_role}).execute()
                                    
                                    st.session_state['logged_in'] = True
                                    st.session_state['username'] = input_username 
                                    st.session_state['user_name'] = db_name
                                    st.session_state['user_role'] = db_role
                                    st.session_state['assigned_muni'] = db_muni
                                    st.session_state['last_active'] = time.time()
                                    
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

with st.sidebar:
    # 1. PROFILE CARD
    user_territory = st.session_state.get('assigned_muni', 'None')
    
    st.markdown(f"""
    <div style="text-align: center; padding: 10px 0px 15px 0px;">
        <img src="https://upload.wikimedia.org/wikipedia/commons/0/0c/Seal_of_the_Cordillera_Administrative_Region.png" width="90" style="margin-bottom: 15px; filter: drop-shadow(0px 4px 6px rgba(0,0,0,0.1));">
        <h3 style="margin: 0; padding: 0; font-size: 1.15rem; font-weight: 700;">{st.session_state['user_name']}</h3>
        <p style="margin: 2px 0 12px 0; font-size: 0.85rem; opacity: 0.8; font-style: italic;">{st.session_state['user_role']}</p>
        <span style="background-color: rgba(128,128,128,0.15); border: 1px solid rgba(128,128,128,0.3); color: inherit; padding: 6px 16px; border-radius: 20px; font-size: 0.85rem; font-weight: 600; letter-spacing: 0.5px;">
            📍 {user_territory.upper()}
        </span>
    </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    # 2. Dynamic Filters (Abra Only)
    with st.expander("🎛️ DASHBOARD FILTERS", expanded=False):
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
            st.session_state['logged_in'] = False
            st.session_state['username'] = ""
            st.session_state['user_name'] = ""
            st.session_state['user_role'] = ""
            st.session_state['assigned_muni'] = ""
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

@st.cache_data(ttl="15s")
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
            
    return df.rename(columns=col_mapping)

@st.cache_data(ttl="10m")
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
tab_names = ["📊 Executive Summary", "🎯 Target Overview", "💉 MR Accomplishment", "💊 Vit A Accomplishment", "📉 Wastage & Refusals"]
if is_admin:
    tab_names.append("🛡️ Admin Panel")
    
tabs = st.tabs(tab_names)

if is_admin:
    tab_total, tab_target, tab_mr, tab_vita, tab_wastage, tab_admin = tabs
else:
    tab_total, tab_target, tab_mr, tab_vita, tab_wastage = tabs

try:
    with tab_total:
        st.info("🚧 Executive Summary will populate once data streams are connected.")

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
                    c1, c2 = st.columns([7, 3])
                    with c1:
                        df_sorted_mr = df_view.sort_values(plot_col, ascending=True) 
                        fig_mr = px.bar(df_sorted_mr, x=plot_col, y='Location', orientation='h', text_auto='.0f', color_discrete_sequence=['#1E88E5'])
                        fig_mr.update_layout(xaxis_title=chart_title, yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=400, margin=dict(l=0, r=0, t=10, b=0))
                        st.plotly_chart(fig_mr, use_container_width=True)
                    with c2:
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
                    c1, c2 = st.columns([7, 3])
                    with c1:
                        df_sorted_va = df_view_va.sort_values(plot_col_va, ascending=True) 
                        fig_va = px.bar(df_sorted_va, x=plot_col_va, y='Location', orientation='h', text_auto='.0f', color_discrete_sequence=['#F4511E'])
                        fig_va.update_layout(xaxis_title=chart_title_va, yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=400, margin=dict(l=0, r=0, t=10, b=0))
                        st.plotly_chart(fig_va, use_container_width=True)
                    with c2:
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
                    c1, c2 = st.columns([7, 3])
                    with c1:
                        df_sorted_act_mr = df_view.sort_values(act_plot_col, ascending=True) 
                        fig_act_mr = px.bar(df_sorted_act_mr, x=act_plot_col, y='Location', orientation='h', text_auto='.0f', color_discrete_sequence=['#43A047'])
                        fig_act_mr.update_layout(xaxis_title=act_chart_title, yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=400, margin=dict(l=0, r=0, t=10, b=0))
                        st.plotly_chart(fig_act_mr, use_container_width=True)
                    with c2:
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
                    c1, c2 = st.columns([7, 3])
                    with c1:
                        df_sorted_act_va = df_view_va.sort_values(act_plot_col_va, ascending=True) 
                        fig_act_va = px.bar(df_sorted_act_va, x=act_plot_col_va, y='Location', orientation='h', text_auto='.0f', color_discrete_sequence=['#00ACC1'])
                        fig_act_va.update_layout(xaxis_title=act_chart_title_va, yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=400, margin=dict(l=0, r=0, t=10, b=0))
                        st.plotly_chart(fig_act_va, use_container_width=True)
                    with c2:
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
                    fig_comp.update_layout(xaxis_title="Eligible Children Count", yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=500, legend_title_text="")
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
                df_mr_filtered = df_mr_filtered[(df_mr_filtered['Municipality'] == selected_muni) & (df_mr_filtered['Barangay'].isin(df_view['Location'].tolist()))]
            
            mr_dose_cols = ['MR 6-12 Male', 'MR 6-12 Female', 'MR 13-23 Male', 'MR 13-23 Female', 'MR 24-59 Male', 'MR 24-59 Female']
            
            for col in mr_dose_cols:
                if col in df_mr_filtered.columns:
                    df_mr_filtered[col] = pd.to_numeric(df_mr_filtered[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            
            df_mr_filtered['Total Doses'] = df_mr_filtered[mr_dose_cols].sum(axis=1)
            total_mr_doses = df_mr_filtered['Total Doses'].sum()
        else:
            df_mr_filtered = pd.DataFrame()

        nat_target = df_view['MR_6-59m_Total'].sum()
        act_target = df_view['Act_MR_6-59m_Total'].sum() if 'Act_MR_6-59m_Total' in df_view.columns else 0

        nat_cov = (total_mr_doses / nat_target * 100) if nat_target > 0 else 0
        act_cov = (total_mr_doses / act_target * 100) if act_target > 0 else 0

        col_mr1, col_mr2, col_mr3, col_mr4 = st.columns(4)
        col_mr1.metric("💉 Total Doses Administered", f"{total_mr_doses:,.0f}")
        col_mr2.metric("🎯 National Coverage %", f"{nat_cov:.1f}%", f"{nat_target:,.0f} Nat. Target", delta_color="off")
        
        if act_target > 0:
            col_mr3.metric("📊 Actual RHU Coverage %", f"{act_cov:.1f}%", f"{act_target:,.0f} Act. Target", delta_color="off")
        else:
            col_mr3.metric("📊 Actual RHU Coverage %", "Awaiting Data", "RHU Sheet Empty", delta_color="off")
            
        variance = act_target - nat_target
        var_label = "More than National" if variance > 0 else "Less than National"
        col_mr4.metric("🚨 Variance (Act vs Nat)", f"{variance:,.0f}", var_label, delta_color="inverse")
        
        st.divider()
        
        # --- MR CHARTS & ANALYTICS ---
        st.markdown("#### 📈 Accomplishment Analytics")
        if not df_mr_filtered.empty:
            geo_col = 'Municipality' if view_mode != "Specific Municipality" else 'Barangay'
            
            if 'Vaccination Date' in df_mr_filtered.columns:
                df_mr_filtered['Vaccination Date'] = pd.to_datetime(df_mr_filtered['Vaccination Date'], errors='coerce')
                
            c1, c2 = st.columns([7, 3])
            
            with c1:
                if 'Vaccination Date' in df_mr_filtered.columns and not df_mr_filtered['Vaccination Date'].isna().all():
                    df_time = df_mr_filtered.groupby(df_mr_filtered['Vaccination Date'].dt.date)['Total Doses'].sum().reset_index()
                    fig_time = px.line(df_time, x='Vaccination Date', y='Total Doses', markers=True, title="Daily Doses Administered Trend", color_discrete_sequence=['#1E88E5'])
                    fig_time.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis_title="", yaxis_title="Doses", margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_time, use_container_width=True)
                
                if geo_col in df_mr_filtered.columns:
                    df_geo = df_mr_filtered.groupby(geo_col)['Total Doses'].sum().reset_index().sort_values('Total Doses', ascending=True)
                    fig_geo = px.bar(df_geo, x='Total Doses', y=geo_col, orientation='h', text_auto='.0f', title=f"Doses Administered by {geo_col}", color_discrete_sequence=['#1E88E5'])
                    fig_geo.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis_title="Total Doses", yaxis_title="", margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_geo, use_container_width=True)
                    
            with c2:
                mr_6_12 = df_mr_filtered[['MR 6-12 Male', 'MR 6-12 Female']].sum().sum()
                mr_13_23 = df_mr_filtered[['MR 13-23 Male', 'MR 13-23 Female']].sum().sum()
                mr_24_59 = df_mr_filtered[['MR 24-59 Male', 'MR 24-59 Female']].sum().sum()
                
                df_age = pd.DataFrame({'Age Group': ['6-12m', '13-23m', '24-59m'], 'Doses': [mr_6_12, mr_13_23, mr_24_59]})
                fig_age = px.pie(df_age, names='Age Group', values='Doses', hole=0.4, title="By Age Group", color_discrete_sequence=['#E53935', '#FFB300', '#43A047'])
                fig_age.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_age, use_container_width=True)
                
                mr_male = df_mr_filtered[['MR 6-12 Male', 'MR 13-23 Male', 'MR 24-59 Male']].sum().sum()
                mr_female = df_mr_filtered[['MR 6-12 Female', 'MR 13-23 Female', 'MR 24-59 Female']].sum().sum()
                
                df_gender = pd.DataFrame({'Gender': ['Male', 'Female'], 'Doses': [mr_male, mr_female]})
                fig_gender = px.pie(df_gender, names='Gender', values='Doses', hole=0.4, title="By Gender", color_discrete_sequence=['#1E88E5', '#D81B60'])
                fig_gender.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_gender, use_container_width=True)
                
            st.divider()
            
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
            st.info("Awaiting VaccTrack Sync to populate analytics.")

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
                df_vita_filtered = df_vita_filtered[(df_vita_filtered['Municipality'] == selected_muni) & (df_vita_filtered['Barangay'].isin(df_view['Location'].tolist()))]
            
            vita_dose_cols = ['VitA 6-11 Male', 'VitA 6-11 Female', 'VitA 12-59 Male', 'VitA 12-59 Female']
            
            for col in vita_dose_cols:
                if col in df_vita_filtered.columns:
                    df_vita_filtered[col] = pd.to_numeric(df_vita_filtered[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            
            df_vita_filtered['Total Doses'] = df_vita_filtered[vita_dose_cols].sum(axis=1)
            total_vita_doses = df_vita_filtered['Total Doses'].sum()
        else:
            df_vita_filtered = pd.DataFrame()
            
        nat_target_va = df_view_va['VitA_Total'].sum() if not df_view_va.empty else 0
        act_target_va = df_view_va['Act_VitA_Total'].sum() if not df_view_va.empty and 'Act_VitA_Total' in df_view_va.columns else 0

        nat_cov_va = (total_vita_doses / nat_target_va * 100) if nat_target_va > 0 else 0
        act_cov_va = (total_vita_doses / act_target_va * 100) if act_target_va > 0 else 0

        col_va1, col_va2, col_va3, col_va4 = st.columns(4)
        col_va1.metric("💊 Total Doses Administered", f"{total_vita_doses:,.0f}")
        col_va2.metric("🎯 National Coverage %", f"{nat_cov_va:.1f}%", f"{nat_target_va:,.0f} Nat. Target", delta_color="off")
        
        if act_target_va > 0:
            col_va3.metric("📊 Actual RHU Coverage %", f"{act_cov_va:.1f}%", f"{act_target_va:,.0f} Act. Target", delta_color="off")
        else:
            col_va3.metric("📊 Actual RHU Coverage %", "Awaiting Data", "RHU Sheet Empty", delta_color="off")
            
        variance_va = act_target_va - nat_target_va
        var_label_va = "More than National" if variance_va > 0 else "Less than National"
        col_va4.metric("🚨 Variance (Act vs Nat)", f"{variance_va:,.0f}", var_label_va, delta_color="inverse")
        
        st.divider()

        # --- VITAMIN A CHARTS & ANALYTICS ---
        st.markdown("#### 📈 Accomplishment Analytics")
        if not df_vita_filtered.empty:
            geo_col_va = 'Municipality' if view_mode != "Specific Municipality" else 'Barangay'
            
            if 'Vaccination Date' in df_vita_filtered.columns:
                df_vita_filtered['Vaccination Date'] = pd.to_datetime(df_vita_filtered['Vaccination Date'], errors='coerce')
                
            c1_va, c2_va = st.columns([7, 3])
            
            with c1_va:
                if 'Vaccination Date' in df_vita_filtered.columns and not df_vita_filtered['Vaccination Date'].isna().all():
                    df_time_va = df_vita_filtered.groupby(df_vita_filtered['Vaccination Date'].dt.date)['Total Doses'].sum().reset_index()
                    fig_time_va = px.line(df_time_va, x='Vaccination Date', y='Total Doses', markers=True, title="Daily Doses Administered Trend", color_discrete_sequence=['#F4511E'])
                    fig_time_va.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis_title="", yaxis_title="Doses", margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_time_va, use_container_width=True)
                
                if geo_col_va in df_vita_filtered.columns:
                    df_geo_va = df_vita_filtered.groupby(geo_col_va)['Total Doses'].sum().reset_index().sort_values('Total Doses', ascending=True)
                    fig_geo_va = px.bar(df_geo_va, x='Total Doses', y=geo_col_va, orientation='h', text_auto='.0f', title=f"Doses Administered by {geo_col_va}", color_discrete_sequence=['#F4511E'])
                    fig_geo_va.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis_title="Total Doses", yaxis_title="", margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_geo_va, use_container_width=True)
                    
            with c2_va:
                va_6_11 = df_vita_filtered[['VitA 6-11 Male', 'VitA 6-11 Female']].sum().sum()
                va_12_59 = df_vita_filtered[['VitA 12-59 Male', 'VitA 12-59 Female']].sum().sum()
                
                df_age_va = pd.DataFrame({'Age Group': ['6-11m', '12-59m'], 'Doses': [va_6_11, va_12_59]})
                fig_age_va = px.pie(df_age_va, names='Age Group', values='Doses', hole=0.4, title="By Age Group", color_discrete_sequence=['#00ACC1', '#8E24AA'])
                fig_age_va.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_age_va, use_container_width=True)
                
                va_male = df_vita_filtered[['VitA 6-11 Male', 'VitA 12-59 Male']].sum().sum()
                va_female = df_vita_filtered[['VitA 6-11 Female', 'VitA 12-59 Female']].sum().sum()
                
                df_gender_va = pd.DataFrame({'Gender': ['Male', 'Female'], 'Doses': [va_male, va_female]})
                fig_gender_va = px.pie(df_gender_va, names='Gender', values='Doses', hole=0.4, title="By Gender", color_discrete_sequence=['#1E88E5', '#D81B60'])
                fig_gender_va.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_gender_va, use_container_width=True)

            st.divider()
            
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
            st.info("Awaiting VaccTrack Sync to populate analytics.")
        
    with tab_wastage:
        st.markdown("### 📉 Logistics, Wastage & Deferral Analysis")
        st.write("Deep dive into vaccine utilization and specific reasons for missed targets.")
        
        col_waste1, col_waste2 = st.columns(2)
        with col_waste1:
            st.markdown("#### 🧪 Vaccine Utilization (MR & Vit A)")
            st.info("🚧 Wastage Rate Chart Placeholder (Opened vs. Administered)")
            
        with col_waste2:
            st.markdown("#### 📋 Top 5 Reasons for Non-Vaccination")
            st.info("🚧 C1-C23 Pareto Chart Placeholder")

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
            res_users = supabase.table('user_accounts').select('*').execute()
            if res_users.data:
                users_admin_df = pd.DataFrame(res_users.data)
                
                cols = ['username', 'role', 'password_hash']
                users_admin_df = users_admin_df[[c for c in cols if c in users_admin_df.columns]]
                
                edited_users = st.data_editor(
                    users_admin_df,
                    column_config={
                        "password_hash": None, 
                        "username": st.column_config.TextColumn("Username", disabled=True),
                    },
                    use_container_width=True,
                    num_rows="dynamic",
                    key="user_editor"
                )
                
                if st.button("💾 Save User Changes", type="secondary"):
                    try:
                        updated_records = edited_users.to_dict(orient='records')
                        supabase.table('user_accounts').upsert(updated_records).execute()
                        st.toast("User accounts updated successfully!", icon="✅")
                    except Exception as e:
                        st.error(f"Failed to update users: {e}")
            
            st.divider()

            st.markdown("### 📋 System Access Logs")
            try:
                res_logs = supabase.table('access_logs').select('*').order('id', desc=True).limit(100).execute()
                if res_logs.data:
                    logs_df = pd.DataFrame(res_logs.data)[['timestamp', 'name', 'role']]
                    st.dataframe(logs_df, use_container_width=True)
                else:
                    st.info("No access logs found yet.")
            except Exception as e:
                st.warning(f"Could not load Access Logs: {e}")

except Exception as e:
    st.error(f"Dashboard Error: {e}")
