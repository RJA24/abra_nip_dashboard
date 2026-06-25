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
st.set_page_config(page_title="CAR SIA 2026 Tracker", page_icon="💉", layout="wide", initial_sidebar_state="expanded")

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
# 4. THE GATEWAY (Login, Registration & Recovery)
# ==========================================
abra_munis = ["Bangued", "Boliney", "Bucay", "Bucloc", "Daguioman", "Danglas", "Dolores", "La Paz", "Lacub", "Lagangilang", "Lagayan", "Langiden", "Licuan-Baay", "Luba", "Malibcong", "Manabo", "Peñarrubia", "Pidigan", "Pilar", "Sallapadan", "San Isidro", "San Juan", "San Quintin", "Tayum", "Tineg", "Tubo", "Villaviciosa"]

if not st.session_state['logged_in']:
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.title("🔒 CAR SIA 2026")
        st.markdown("##### Secure Regional Command Center")
        st.divider()
        
        tab_login, tab_signup, tab_forgot = st.tabs(["🔑 Log In", "📝 Request Account", "❓ Forgot Password"])
        
        with tab_login:
            with st.form("login_form"):
                input_username = st.text_input("Username").strip()
                input_password = st.text_input("Password", type="password")
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
                                account_status = user_data.get('account_status', 'Pending')
                                failed_attempts = user_data.get('failed_attempts', 0)
                                MAX_ATTEMPTS = 3
                                
                                if account_status == "Locked":
                                    st.error("🚨 Your account is locked. Please contact a System Admin to unlock it.")
                                elif account_status == "Approved":
                                    if check_hashes(input_password, stored_hash):
                                        if failed_attempts > 0:
                                            supabase.table('user_accounts').update({'failed_attempts': 0}).eq('username', input_username).execute()
                                        
                                        db_name = user_data['name']
                                        db_role = user_data['role']
                                        db_muni = user_data.get('assigned_municipality', 'None')
                                        
                                        manila_tz = pytz.timezone('Asia/Manila')
                                        current_time_str = datetime.now(manila_tz).strftime("%Y-%m-%d %I:%M:%S %p")
                                        supabase.table('access_logs').insert({'timestamp': current_time_str, 'name': db_name, 'role': db_role}).execute()
                                        
                                        st.session_state['logged_in'] = True
                                        st.session_state['username'] = input_username 
                                        st.session_state['user_name'] = db_name
                                        st.session_state['user_role'] = db_role
                                        st.session_state['assigned_muni'] = db_muni
                                        st.session_state['last_active'] = time.time()
                                        
                                        st.toast(f"Welcome back, {db_name}!", icon="👋")
                                        time.sleep(1)
                                        st.rerun()
                                    else:
                                        failed_attempts += 1
                                        if failed_attempts >= MAX_ATTEMPTS:
                                            supabase.table('user_accounts').update({'failed_attempts': failed_attempts, 'account_status': 'Locked'}).eq('username', input_username).execute()
                                            st.error("🚨 Maximum login attempts reached. Your account is now locked.")
                                        else:
                                            supabase.table('user_accounts').update({'failed_attempts': failed_attempts}).eq('username', input_username).execute()
                                            st.error(f"❌ Incorrect Password. Attempt {failed_attempts} of {MAX_ATTEMPTS}.")
                                elif account_status == "Pending":
                                    st.warning("⏳ Your account request is still pending admin approval.")
                                elif account_status == "Pending Reset":
                                    st.warning("🔄 Your password reset request is pending admin approval.")
                                else:
                                    st.error("🚫 Your account access has been denied or revoked.")
                            else:
                                st.error("❌ Username not found.")
                        except Exception as e:
                            st.error(f"System Error: {e}")

        with tab_signup:
            st.info("Submitted requests are reviewed by a System Admin before access is granted.")
            new_role = st.selectbox("Designation / Role", ["Municipal Health Office", "DOH Regional Office", "Provincial Health Office", "System Admin",  "Data Encoder", "Guest / Viewer"])
            
            with st.form("signup_form"):
                new_name = st.text_input("Full Name")
                
                if new_role in ["Municipal Health Office", "Data Encoder"]:
                    new_muni = st.selectbox("Assigned Municipality", abra_munis)
                else:
                    new_muni = "None"
                    
                new_contact = st.text_input("Official Contact (Email or Viber Number)", placeholder="Used for account verification")
                new_username = st.text_input("Desired Username").strip()
                new_password = st.text_input("Create Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
                
                submit_signup = st.form_submit_button("Submit Request", type="primary", use_container_width=True)
                
                if submit_signup:
                    if not all([new_name, new_role, new_contact, new_username, new_password, confirm_password]):
                        st.warning("Please fill out all fields.")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match!")
                    else:
                        try:
                            res = supabase.table('user_accounts').select('username').eq('username', new_username).execute()
                            if res.data:
                                st.error("⚠️ That username is already taken. Please choose another.")
                            else:
                                hashed_pw = make_hashes(new_password)
                                supabase.table('user_accounts').insert({
                                    "username": new_username, "password_hash": hashed_pw, "name": new_name.strip(),
                                    "role": new_role, "assigned_municipality": new_muni, "account_status": "Pending", 
                                    "contact_info": new_contact.strip(), "failed_attempts": 0 
                                }).execute()
                                st.success("✅ Request submitted! Please wait for admin approval.")
                        except Exception as e:
                            st.error(f"Registration Error: {e}")

        with tab_forgot:
            with st.form("forgot_password_form"):
                st.info("An Admin must approve this reset before you can log in.")
                reset_username = st.text_input("Your Username").strip()
                reset_new_password = st.text_input("New Password", type="password")
                reset_confirm_password = st.text_input("Confirm New Password", type="password")
                
                submit_reset = st.form_submit_button("Request Password Reset", use_container_width=True)
                
                if submit_reset:
                    if not all([reset_username, reset_new_password, reset_confirm_password]):
                        st.warning("Please fill out all fields.")
                    elif reset_new_password != reset_confirm_password:
                        st.error("Passwords do not match!")
                    else:
                        try:
                            res = supabase.table('user_accounts').select('username').eq('username', reset_username).execute()
                            if res.data:
                                supabase.table('user_accounts').update({'password_hash': make_hashes(reset_new_password), 'account_status': 'Pending Reset', 'failed_attempts': 0}).eq('username', reset_username).execute()
                                st.success("✅ Reset request sent! Your account is locked until Admin approval.")
                            else:
                                st.error("⚠️ Username not found.")
                        except Exception as e:
                            st.error(f"Reset Error: {e}")
    st.stop()

# ==========================================
# MAIN DASHBOARD CODE (Only runs if logged in)
# ==========================================
st.title("Cordillera Administrative Region (CAR) SIA 2026")

@st.cache_data(ttl="15s")
def get_last_updated_time():
    tz = pytz.timezone('Asia/Manila')
    return datetime.now(tz).strftime("%B %d, %Y | %I:%M %p")

last_updated = get_last_updated_time()
is_admin = st.session_state['user_role'] == "System Admin"
is_encoder = st.session_state['user_role'] in ["Municipal Health Office", "Data Encoder", "System Admin"]

with st.sidebar:
    # 1. NEW PRO-LOOKING PROFILE CARD (With fixed readability)
    user_territory = st.session_state.get('assigned_muni', 'None')
    muni_display = f"{user_territory}" if user_territory != "None" else "Regional Access"
    
    st.markdown(f"""
    <div style="text-align: center; padding: 10px 0px 15px 0px;">
        <img src="https://upload.wikimedia.org/wikipedia/commons/0/0c/Seal_of_the_Cordillera_Administrative_Region.png" width="90" style="margin-bottom: 15px; filter: drop-shadow(0px 4px 6px rgba(0,0,0,0.1));">
        <h3 style="margin: 0; padding: 0; font-size: 1.15rem; font-weight: 700;">{st.session_state['user_name']}</h3>
        <p style="margin: 2px 0 12px 0; font-size: 0.85rem; opacity: 0.8; font-style: italic;">{st.session_state['user_role']}</p>
        <span style="background-color: rgba(128,128,128,0.15); border: 1px solid rgba(128,128,128,0.3); color: inherit; padding: 6px 16px; border-radius: 20px; font-size: 0.85rem; font-weight: 600; letter-spacing: 0.5px;">
            📍 {muni_display.upper()}
        </span>
    </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    # 2. Workspace Toggle (Inside an Expander)
    app_mode = "📊 Dashboard View"
    if is_encoder:
        with st.expander("⚙️ WORKSPACE MODE", expanded=False):
            app_mode = st.radio("Select Interface:", ["📊 Dashboard View", "📝 Data Entry Mode"], label_visibility="collapsed")
    
    # 3. Dynamic Filters (Inside an Expander)
    if app_mode == "📊 Dashboard View":
        with st.expander("🎛️ DASHBOARD FILTERS", expanded=False):
            view_mode = st.radio("Geographic Level:", ["Region-wide (Compare Provinces)", "Province-wide (Compare Municipalities)", "Specific Municipality (Compare Barangays)"])
            st.write("")
            # The Universal Program & Age Filter
            age_filter = st.selectbox("Program & Age Group:", [
                "MR: 6 - 59 months (Total)", "MR: 6 - 12 months", "MR: 13 - 23 months", "MR: 24 - 59 months",
                "Vit A: 6 - 59 months (Total)", "Vit A: 6 - 11 months", "Vit A: 12 - 59 months"
            ])

            # NEW: Universal Gender Filter
            gender_filter = st.selectbox("Target Gender:", ["Total (Both)", "Male", "Female"])
            st.write("")
            
            # The placeholder container for the dynamic location dropdowns
            geo_filters_container = st.container()
            
    
    # 4. Account Settings (Merged into a single Expander)
    with st.expander("⚙️ ACCOUNT SETTINGS", expanded=False):
        with st.form("change_password_form"):
            st.caption("Change Your Password")
            current_pw = st.text_input("Current Password", type="password")
            new_pw = st.text_input("New Password", type="password")
            confirm_new_pw = st.text_input("Confirm New Password", type="password")
            submit_pw_change = st.form_submit_button("Update Password", use_container_width=True)

            if submit_pw_change:
                if not current_pw or not new_pw or not confirm_new_pw:
                    st.error("Please fill all fields.")
                elif new_pw != confirm_new_pw:
                    st.error("New passwords do not match.")
                else:
                    try:
                        res = supabase.table('user_accounts').select('password_hash').eq('username', st.session_state['username']).execute()
                        if res.data:
                            stored_hash = res.data[0]['password_hash']
                            if check_hashes(current_pw, stored_hash):
                                new_hash = make_hashes(new_pw)
                                supabase.table('user_accounts').update({'password_hash': new_hash}).eq('username', st.session_state['username']).execute()
                                st.success("✅ Password successfully updated!")
                            else:
                                st.error("❌ Current password is incorrect.")
                        else:
                            st.error("Account verification failed.")
                    except Exception as e:
                        st.error(f"Error updating password: {e}")

    # 5. System Actions (Inside an Expander)
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
        'vita_12_59m': 'VitA_12-59m_Total', 'vita_12_59m_m': 'VitA_12-59m_M', 'vita_12_59m_f': 'VitA_12-59m_F'
    }
    
    # Crash Prevention: Fills with 0 if columns aren't synced yet
    for db_col in col_mapping.keys():
        if db_col not in df.columns:
            df[db_col] = 0
            
    return df.rename(columns=col_mapping)

# ==========================================
# MODE 1: DATA ENTRY (No tabs, no filters)
# ==========================================
if app_mode == "📝 Data Entry Mode":
    st.markdown("### 📝 Daily Gender Disaggregation Entry")
    st.info("Please enter the exact Male/Female breakdown for your daily vaccinations. This data supplements the official VaccTrack totals.")
    
    df_targets_for_form = fetch_targets_from_supabase()
    
    if not df_targets_for_form.empty:
        df_abra = df_targets_for_form[df_targets_for_form['Parent_Province'] == 'Abra']
        
        with st.form("rhu_daily_encoding"):
            col_info1, col_info2, col_info3 = st.columns(3)
            with col_info1:
                encode_date = st.date_input("Vaccination Date", max_value=datetime.today())
            
            with col_info2:
                user_muni = st.session_state.get('assigned_muni', 'None')
                
                if is_admin or user_muni == "None" or user_muni == "" or user_muni == "Abra":
                    muni_list = sorted(df_abra[df_abra['Level'] == 'Municipality']['Location'].dropna().unique().tolist())
                    encode_muni = st.selectbox("Municipality", muni_list)
                else:
                    st.text_input("Municipality (Locked to your assignment)", value=user_muni, disabled=True)
                    encode_muni = user_muni
                    
            with col_info3:
                brgy_list = sorted(df_abra[(df_abra['Parent_Municipality'] == encode_muni) & (df_abra['Level'] == 'Barangay')]['Location'].dropna().unique().tolist())
                if not brgy_list:
                     st.warning("No barangays found. Please check assigned municipality.")
                     encode_brgy = None
                else:
                     encode_brgy = st.selectbox("Barangay", brgy_list)
            
            st.divider()
            st.markdown("#### 💉 Measles-Rubella (MR)")
            col_mr1, col_mr2, col_mr3 = st.columns(3)
            with col_mr1:
                st.caption("6 - 12 months")
                mr_6_12_m = st.number_input("Male", min_value=0, key="mr1m")
                mr_6_12_f = st.number_input("Female", min_value=0, key="mr1f")
            with col_mr2:
                st.caption("13 - 23 months")
                mr_13_23_m = st.number_input("Male", min_value=0, key="mr2m")
                mr_13_23_f = st.number_input("Female", min_value=0, key="mr2f")
            with col_mr3:
                st.caption("24 - 59 months")
                mr_24_59_m = st.number_input("Male", min_value=0, key="mr3m")
                mr_24_59_f = st.number_input("Female", min_value=0, key="mr3f")

            st.divider()
            st.markdown("#### 💊 Vitamin A")
            col_va1, col_va2 = st.columns(2)
            with col_va1:
                st.caption("6 - 11 months")
                va_6_11_m = st.number_input("Male", min_value=0, key="va1m")
                va_6_11_f = st.number_input("Female", min_value=0, key="va1f")
            with col_va2:
                st.caption("12 - 59 months")
                va_12_59_m = st.number_input("Male", min_value=0, key="va2m")
                va_12_59_f = st.number_input("Female", min_value=0, key="va2f")
            
            submit_daily = st.form_submit_button("💾 Save Daily Data", type="primary", use_container_width=True)
            
            if submit_daily:
                if encode_brgy is None:
                     st.error("Cannot save data without a selected Barangay.")
                else:
                    try:
                        supabase.table('rhu_disaggregated').insert({
                            "date": str(encode_date),
                            "municipality": encode_muni,
                            "barangay": encode_brgy,
                            "encoder_name": st.session_state['user_name'],
                            "mr_6_12m_m": mr_6_12_m, "mr_6_12m_f": mr_6_12_f,
                            "mr_13_23m_m": mr_13_23_m, "mr_13_23m_f": mr_13_23_f,
                            "mr_24_59m_m": mr_24_59_m, "mr_24_59m_f": mr_24_59_f,
                            "vita_6_11m_m": va_6_11_m, "vita_11_6m_f": va_6_11_f,
                            "vita_12_59m_m": va_12_59_m, "vita_12_59m_f": va_12_59_f
                        }).execute()
                        st.success(f"✅ Data for {encode_brgy} successfully saved!")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to save data: {e}")

        # --- THE ROW DELETION EDITOR ---
        st.divider()
        st.markdown("### 📋 Review, Edit & Delete Data")
        
        try:
            user_muni = st.session_state.get('assigned_muni', 'None')
            
            if is_admin or user_muni == "None" or user_muni == "" or user_muni == "Abra":
                res_rhu = supabase.table('rhu_disaggregated').select('*').execute()
            else:
                res_rhu = supabase.table('rhu_disaggregated').select('*').eq('municipality', user_muni).execute()
                
            if res_rhu.data:
                df_rhu = pd.DataFrame(res_rhu.data)
                df_rhu['date'] = pd.to_datetime(df_rhu['date'], errors='coerce')
                df_rhu = df_rhu.sort_values('date', ascending=False)
                
                col_order = ['id', 'date', 'municipality', 'barangay', 'mr_6_12m_m', 'mr_6_12m_f', 'mr_13_23m_m', 'mr_13_23m_f', 'mr_24_59m_m', 'mr_24_59m_f', 'vita_6_11m_m', 'vita_11_6m_f', 'vita_12_59m_m', 'vita_12_59m_f', 'encoder_name']
                df_rhu = df_rhu[[c for c in col_order if c in df_rhu.columns]]
                
                today = pd.Timestamp.today().normalize()
                
                if is_admin:
                    st.info("🛡️ System Admin View: You have overriding access to edit or delete all records.")
                    df_editable = df_rhu.copy()
                    df_locked = pd.DataFrame()
                else:
                    st.info("You can edit or delete data within 7 days of the vaccination date. To delete a row, click the row number on the left and press the 'Delete' key.")
                    df_rhu['is_editable'] = (today - df_rhu['date']).dt.days <= 7
                    df_editable = df_rhu[df_rhu['is_editable']].drop(columns=['is_editable'])
                    df_locked = df_rhu[~df_rhu['is_editable']].drop(columns=['is_editable'])

                if not df_editable.empty:
                    df_editable['date'] = df_editable['date'].dt.date
                if not df_locked.empty:
                    df_locked['date'] = df_locked['date'].dt.date

                # Table 1: Editable & Deletable
                if not df_editable.empty:
                    st.markdown("#### 🟢 Recent Records (Editable)")
                    edited_rhu = st.data_editor(
                        df_editable,
                        column_config={
                            "id": None, 
                            "date": st.column_config.DateColumn("Date", disabled=True),
                            "municipality": st.column_config.TextColumn("Municipality", disabled=True),
                            "barangay": st.column_config.TextColumn("Barangay", disabled=True),
                            "encoder_name": st.column_config.TextColumn("Encoder", disabled=True)
                        },
                        use_container_width=True,
                        num_rows="dynamic", 
                        key="rhu_data_editor"
                    )
                    
                    if st.button("💾 Save Edits & Deletions", type="secondary"):
                        try:
                            # 1. Handle Deletions
                            original_ids = set(df_editable['id'].tolist())
                            current_ids = set(edited_rhu['id'].dropna().tolist())
                            deleted_ids = list(original_ids - current_ids)
                            
                            if deleted_ids:
                                supabase.table('rhu_disaggregated').delete().in_('id', deleted_ids).execute()
                                
                            # 2. Handle Updates
                            records_to_update = edited_rhu.dropna(subset=['id']).copy() 
                            if not records_to_update.empty:
                                records_to_update['date'] = records_to_update['date'].astype(str)
                                updates = records_to_update.to_dict(orient='records')
                                supabase.table('rhu_disaggregated').upsert(updates).execute()
                                
                            st.success("✅ Database successfully updated!")
                            time.sleep(1)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to update database: {e}")
                
                # Table 2: Locked
                if not df_locked.empty:
                    st.markdown("#### 🔒 Locked Records (Historical)")
                    st.dataframe(
                        df_locked,
                        column_config={
                            "id": None,
                            "date": st.column_config.DateColumn("Date"), 
                            "municipality": "Municipality", 
                            "barangay": "Barangay", 
                            "encoder_name": "Encoder"
                        },
                        use_container_width=True
                    )
            else:
                st.info("No encoded data found yet.")
        except Exception as e:
            st.error(f"Could not load encoded data: {e}")
            
    else:
         st.warning("Please wait for the System Admin to sync the Target Database before encoding daily data.")

# ==========================================
# MODE 2: THE DASHBOARD (Tabs and Filters)
# ==========================================
elif app_mode == "📊 Dashboard View":

    tab_names = ["🎯 Target Overview", "💉 MR Accomplishment", "💊 Vit A Accomplishment", "📉 Wastage & Refusals", "📊 Executive Summary"]
    if is_admin:
        tab_names.append("🛡️ Admin Panel")
        
    tabs = st.tabs(tab_names)
    
    if is_admin:
        tab_target, tab_mr, tab_vita, tab_wastage, tab_total, tab_admin = tabs
    else:
        tab_target, tab_mr, tab_vita, tab_wastage, tab_total = tabs

    try:
        with tab_target:
            st.markdown("### Regional Target Baseline Overview")
            df_targets = fetch_targets_from_supabase()
            
            if df_targets.empty:
                st.warning("⚠️ The Targets Database is empty. Please ask a System Admin to sync the database from Google Sheets.")
            else:
                # 1. Geographic Filtering
                selected_prov, selected_muni = "CAR_Region", ""
                
                if view_mode == "Region-wide (Compare Provinces)":
                    df_view = df_targets[df_targets['Level'] == 'Province']
                    location_label = "CAR Region"
                elif view_mode == "Province-wide (Compare Municipalities)":
                    province_list = df_targets[df_targets['Level'] == 'Province']['Location'].unique().tolist()
                    default_prov_idx = province_list.index("Abra") if "Abra" in province_list else 0
                    with geo_filters_container:
                        selected_prov = st.selectbox("Select Province:", province_list, index=default_prov_idx)
                    df_view = df_targets[(df_targets['Level'] == 'Municipality') & (df_targets['Parent_Province'] == selected_prov)]
                    location_label = f"{selected_prov} Province"
                else: 
                    province_list = df_targets[df_targets['Level'] == 'Province']['Location'].unique().tolist()
                    default_prov_idx = province_list.index("Abra") if "Abra" in province_list else 0
                    with geo_filters_container:
                        selected_prov = st.selectbox("Select Province:", province_list, index=default_prov_idx)
                        muni_list = df_targets[(df_targets['Level'] == 'Municipality') & (df_targets['Parent_Province'] == selected_prov)]['Location'].unique().tolist()
                        default_muni_idx = muni_list.index("Manabo") if "Manabo" in muni_list else 0
                        selected_muni = st.selectbox("Select Municipality:", muni_list, index=default_muni_idx)
                    df_view = df_targets[(df_targets['Level'] == 'Barangay') & (df_targets['Parent_Municipality'] == selected_muni)]
                    location_label = f"{selected_muni}, {selected_prov}"

                # 2. Sub-Tabs
                sub_mr, sub_vita = st.tabs(["💉 Measles-Rubella (MR) Targets", "💊 Vitamin A Targets"])
                
                # --- MR TAB CONTENT ---
                with sub_mr:
                    st.markdown(f"#### MR Breakdown: {location_label}")
                    
                    # Localized Age Filter (Defaults to MR Total automatically)
                    mr_age = st.selectbox("Select MR Age Group:", ["6 - 59 months (Total)", "6 - 12 months", "13 - 23 months", "24 - 59 months"], key="mr_age_sel")
                    
                    mr_map = {
                        "6 - 59 months (Total)": ('MR_6-59m_Total', 'MR_6-59m_M', 'MR_6-59m_F'),
                        "6 - 12 months": ('MR_6-12m_Total', 'MR_6-12m_M', 'MR_6-12m_F'),
                        "13 - 23 months": ('MR_13-23m_Total', 'MR_13-23m_M', 'MR_13-23m_F'),
                        "24 - 59 months": ('MR_24-59m_Total', 'MR_24-59m_M', 'MR_24-59m_F')
                    }
                    t_col, m_col, f_col = mr_map[mr_age]
                    
                    # Determine Bar Chart Column based on Global Gender Filter
                    plot_col = t_col if gender_filter == "Total (Both)" else m_col if gender_filter == "Male" else f_col
                    
                    # Metric Cards
                    kpi1, kpi2, kpi3 = st.columns(3)
                    kpi1.metric(f"Target ({gender_filter})", f"{df_view[plot_col].sum():,.0f}")
                    kpi2.metric("Total Male Target", f"{df_view[m_col].sum():,.0f}")
                    kpi3.metric("Total Female Target", f"{df_view[f_col].sum():,.0f}")
                    
                    if not df_view.empty:
                        c1, c2 = st.columns([7, 3])
                        with c1:
                            df_sorted_mr = df_view.sort_values(plot_col, ascending=True) 
                            fig_mr = px.bar(df_sorted_mr, x=plot_col, y='Location', orientation='h', text_auto='.0f', color_discrete_sequence=['#1E88E5'])
                            fig_mr.update_layout(xaxis_title=f"Eligible Children ({gender_filter})", yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=400, margin=dict(l=0, r=0, t=10, b=0))
                            st.plotly_chart(fig_mr, use_container_width=True)
                        with c2:
                            # Pie chart for Age Groups based on selected Gender
                            p_col1 = 'MR_6-12m_Total' if gender_filter == "Total (Both)" else 'MR_6-12m_M' if gender_filter == "Male" else 'MR_6-12m_F'
                            p_col2 = 'MR_13-23m_Total' if gender_filter == "Total (Both)" else 'MR_13-23m_M' if gender_filter == "Male" else 'MR_13-23m_F'
                            p_col3 = 'MR_24-59m_Total' if gender_filter == "Total (Both)" else 'MR_24-59m_M' if gender_filter == "Male" else 'MR_24-59m_F'
                            
                            mr_age_data = pd.DataFrame({
                                'Age Group': ['6-12m', '13-23m', '24-59m'], 
                                'Target': [df_view[p_col1].sum(), df_view[p_col2].sum(), df_view[p_col3].sum()]
                            })
                            fig_donut_mr = px.pie(mr_age_data, names='Age Group', values='Target', hole=0.4, title=f"Age Distribution ({gender_filter})", color_discrete_sequence=['#43A047', '#FFB300', '#E53935'])
                            fig_donut_mr.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), height=400, margin=dict(l=0, r=0, t=30, b=0))
                            st.plotly_chart(fig_donut_mr, use_container_width=True)
                            
                    with st.expander("📂 View & Download MR Targets"):
                        mr_df = df_view[['Code', 'Location', 'Level', 'Parent_Province', 'Parent_Municipality', 'MR_6-59m_Total', 'MR_6-12m_Total', 'MR_13-23m_Total', 'MR_24-59m_Total']]
                        st.dataframe(mr_df, use_container_width=True, hide_index=True)
                        csv_mr = mr_df.to_csv(index=False).encode('utf-8')
                        st.download_button("📥 Download MR Data", data=csv_mr, file_name=f"MR_Targets_{location_label}_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", type="primary", key="dl_mr")

                # --- VITAMIN A TAB CONTENT ---
                with sub_vita:
                    st.markdown(f"#### Vitamin A Breakdown: {location_label}")
                    
                    if view_mode == "Specific Municipality (Compare Barangays)":
                        st.warning("⚠️ The official database does not contain Barangay-level targets for Vitamin A. Displaying the overall Municipal target instead.")
                        df_view_va = df_targets[(df_targets['Level'] == 'Municipality') & (df_targets['Location'] == selected_muni)]
                    else:
                        df_view_va = df_view
                        
                    # Localized Age Filter (Defaults to Vit A Total automatically)
                    va_age = st.selectbox("Select Vit A Age Group:", ["6 - 59 months (Total)", "6 - 11 months", "12 - 59 months"], key="va_age_sel")
                    
                    va_map = {
                        "6 - 59 months (Total)": ('VitA_Total', 'VitA_Total_M', 'VitA_Total_F'),
                        "6 - 11 months": ('VitA_6-11m_Total', 'VitA_6-11m_M', 'VitA_6-11m_F'),
                        "12 - 59 months": ('VitA_12-59m_Total', 'VitA_12-59m_M', 'VitA_12-59m_F')
                    }
                    t_col_va, m_col_va, f_col_va = va_map[va_age]
                    
                    # Determine Bar Chart Column based on Global Gender Filter
                    plot_col_va = t_col_va if gender_filter == "Total (Both)" else m_col_va if gender_filter == "Male" else f_col_va
                    
                    kpi1, kpi2, kpi3 = st.columns(3)
                    kpi1.metric(f"Target ({gender_filter})", f"{df_view_va[plot_col_va].sum():,.0f}")
                    kpi2.metric("Total Male Target", f"{df_view_va[m_col_va].sum():,.0f}")
                    kpi3.metric("Total Female Target", f"{df_view_va[f_col_va].sum():,.0f}")
                    
                    if not df_view_va.empty:
                        c1, c2 = st.columns([7, 3])
                        with c1:
                            df_sorted_va = df_view_va.sort_values(plot_col_va, ascending=True) 
                            fig_va = px.bar(df_sorted_va, x=plot_col_va, y='Location', orientation='h', text_auto='.0f', color_discrete_sequence=['#F4511E'])
                            fig_va.update_layout(xaxis_title=f"Eligible Children ({gender_filter})", yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=400, margin=dict(l=0, r=0, t=10, b=0))
                            st.plotly_chart(fig_va, use_container_width=True)
                        with c2:
                            # Pie chart for Age Groups based on selected Gender
                            p_col1_va = 'VitA_6-11m_Total' if gender_filter == "Total (Both)" else 'VitA_6-11m_M' if gender_filter == "Male" else 'VitA_6-11m_F'
                            p_col2_va = 'VitA_12-59m_Total' if gender_filter == "Total (Both)" else 'VitA_12-59m_M' if gender_filter == "Male" else 'VitA_12-59m_F'
                            
                            va_age_data = pd.DataFrame({
                                'Age Group': ['6-11m', '12-59m'], 
                                'Target': [df_view_va[p_col1_va].sum(), df_view_va[p_col2_va].sum()]
                            })
                            fig_donut_va = px.pie(va_age_data, names='Age Group', values='Target', hole=0.4, title=f"Age Distribution ({gender_filter})", color_discrete_sequence=['#8E24AA', '#00ACC1'])
                            fig_donut_va.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), height=400, margin=dict(l=0, r=0, t=30, b=0))
                            st.plotly_chart(fig_donut_va, use_container_width=True)
                            
                    with st.expander("📂 View & Download Vit A Targets"):
                        va_df = df_view_va[['Code', 'Location', 'Level', 'Parent_Province', 'Parent_Municipality', 'VitA_Total', 'VitA_6-11m_Total', 'VitA_12-59m_Total']]
                        st.dataframe(va_df, use_container_width=True, hide_index=True)
                        csv_va = va_df.to_csv(index=False).encode('utf-8')
                        st.download_button("📥 Download Vit A Data", data=csv_va, file_name=f"VitA_Targets_{location_label}_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", type="primary", key="dl_va")

        with tab_mr:
            st.markdown(f"### 💉 MR Accomplishment & Coverage: {location_label}")
            
            # Fetch daily doses safely
            try:
                res_doses = supabase.table('rhu_disaggregated').select('*').execute()
                df_doses = pd.DataFrame(res_doses.data) if res_doses.data else pd.DataFrame()
            except:
                df_doses = pd.DataFrame()

            # Filter doses to match the current geographic view
            if not df_doses.empty:
                if view_mode == "Province-wide (Compare Municipalities)":
                    df_doses = df_doses[df_doses['municipality'].isin(df_view['Location'].tolist())]
                elif view_mode == "Specific Municipality (Compare Barangays)":
                    df_doses = df_doses[(df_doses['municipality'] == selected_muni) & (df_doses['barangay'].isin(df_view['Location'].tolist()))]
                
                # Calculate total administered MR doses (Male + Female for all age groups)
                total_mr_doses = df_doses[['mr_6_12m_m', 'mr_6_12m_f', 'mr_13_23m_m', 'mr_13_23m_f', 'mr_24_59m_m', 'mr_24_59m_f']].sum().sum()
            else:
                total_mr_doses = 0

            # Get Targets
            nat_target = df_view['MR_6-59m_Total'].sum()
            # Fetch Actual Target dynamically (fallback to 0 if not fully synced yet)
            act_target = df_view.get('actual_mr_6_59m_total', pd.Series([0])).sum()

            # Calculate Coverage Percentages
            nat_cov = (total_mr_doses / nat_target * 100) if nat_target > 0 else 0
            act_cov = (total_mr_doses / act_target * 100) if act_target > 0 else 0

            # UI Display
            col_mr1, col_mr2, col_mr3, col_mr4 = st.columns(4)
            col_mr1.metric("💉 Total Doses Administered", f"{total_mr_doses:,.0f}")
            col_mr2.metric("🎯 National Coverage %", f"{nat_cov:.1f}%", f"{nat_target:,.0f} Target", delta_color="off")
            
            if act_target > 0:
                col_mr3.metric("📊 Actual RHU Coverage %", f"{act_cov:.1f}%", f"{act_target:,.0f} Actual Target", delta_color="off")
            else:
                col_mr3.metric("📊 Actual RHU Coverage %", "Awaiting Data", "RHU Sheet Empty", delta_color="off")
                
            col_mr4.metric("🚨 Variance (Actual vs Nat)", f"{act_target - nat_target:,.0f}", "Children")
            
            st.divider()
            st.info("🚧 Sub-charts for Deferrals, Refusals, and Age-Group breakdowns will populate here as VaccTrack data flows in.")

        with tab_vita:
            st.markdown("### 💊 Vitamin A Accomplishment")
            col_va1, col_va2, col_va3, col_va4 = st.columns(4)
            col_va1.metric("Coverage Target", "0%")
            col_va2.metric("Total Doses Administered", "0")
            col_va3.metric("⚠️ Total Deferrals", "0")
            col_va4.metric("🚨 Total Refusals", "0")
            st.info("🚧 Vitamin A analytical engine ready. Awaiting VaccTrack Sync.")
            
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

        with tab_total:
            st.info("🚧 Executive Summary will populate once data streams are connected.")

        # ==========================================
        # SECRET ADMIN PANEL
        # ==========================================
        if is_admin:
            with tab_admin:
                st.markdown("### 🔄 Phase 1: Target Database Sync")
                st.write("Pull the latest baseline targets from the `Target(CAR)` Google Sheet.")
                
                if st.button("Sync Target Database", type="secondary", use_container_width=True):
                    with st.spinner("Downloading National & Actual Data from 4 Sheets..."):
                        try:
                            conn = st.connection("gsheets", type=GSheetsConnection)
                            
                            # 1. Fetch MR (National & Actual)
                            mr_cols = ["Code", "Location", "6-59m_M", "6-59m_F", "6-59m_Total", "6-12m_M", "6-12m_F", "6-12m_Total", "13-23m_M", "13-23m_F", "13-23m_Total", "24-59m_M", "24-59m_F", "24-59m_Total"]
                            df_mr_nat = clean_and_process_car_data(conn.read(spreadsheet=sheet_url, worksheet="MR Target(CAR)", usecols=list(range(14)), skiprows=2, names=mr_cols, ttl=0), mr_cols)
                            
                            mr_act_cols = ["Code", "Location", "a1", "a2", "Act_MR_6-59m_Total", "a3", "a4", "Act_MR_6-12m_Total", "a5", "a6", "Act_MR_13-23m_Total", "a7", "a8", "Act_MR_24-59m_Total"]
                            df_mr_act = clean_and_process_car_data(conn.read(spreadsheet=sheet_url, worksheet="MR Actual Target(UPDATE THIS)", usecols=list(range(14)), skiprows=2, names=mr_act_cols, ttl=0), mr_act_cols)
                            
                            # 2. Fetch Vit A (National & Actual)
                            vita_cols = ["Code", "Location", "VitA_6-11m_M", "VitA_6-11m_F", "VitA_6-11m_Total", "VitA_12-59m_M", "VitA_12-59m_F", "VitA_12-59m_Total", "VitA_Total"]
                            df_vita_nat = clean_and_process_car_data(conn.read(spreadsheet=sheet_url, worksheet="Vitamin A Pop", usecols=[0, 2, 3, 4, 5, 6, 7, 8, 9], skiprows=2, names=vita_cols, ttl=0), vita_cols)
                            
                            vita_act_cols = ["Code", "Location", "b1", "b2", "Act_VitA_6-11m_Total", "b3", "b4", "Act_VitA_12-59m_Total", "Act_VitA_Total"]
                            df_vita_act = clean_and_process_car_data(conn.read(spreadsheet=sheet_url, worksheet="Vitamin A Pop(UPDATE THIS)", usecols=[0, 2, 3, 4, 5, 6, 7, 8, 9], skiprows=2, names=vita_act_cols, ttl=0), vita_act_cols)
                            
                            # Calc missing genders for VitA National
                            for c in ["VitA_6-11m_M", "VitA_12-59m_M", "VitA_6-11m_F", "VitA_12-59m_F"]:
                                df_vita_nat[c] = pd.to_numeric(df_vita_nat[c].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                            df_vita_nat['VitA_Total_M'] = df_vita_nat['VitA_6-11m_M'] + df_vita_nat['VitA_12-59m_M']
                            df_vita_nat['VitA_Total_F'] = df_vita_nat['VitA_6-11m_F'] + df_vita_nat['VitA_12-59m_F']
                            
                            # 3. Mega-Merge all 4 sheets
                            df_merged = df_mr_nat.copy()
                            df_merged = pd.merge(df_merged, df_mr_act[['Code', 'Act_MR_6-59m_Total', 'Act_MR_6-12m_Total', 'Act_MR_13-23m_Total', 'Act_MR_24-59m_Total']], on='Code', how='left')
                            df_merged = pd.merge(df_merged, df_vita_nat[['Code', 'VitA_6-11m_Total', 'VitA_12-59m_Total', 'VitA_Total', 'VitA_6-11m_M', 'VitA_6-11m_F', 'VitA_12-59m_M', 'VitA_12-59m_F', 'VitA_Total_M', 'VitA_Total_F']], on='Code', how='left')
                            df_merged = pd.merge(df_merged, df_vita_act[['Code', 'Act_VitA_6-11m_Total', 'Act_VitA_12-59m_Total', 'Act_VitA_Total']], on='Code', how='left')
                            
                            # 4. Map & Push to Supabase
                            df_push = df_merged[['Code', 'Location', 'Level', 'Parent_Province', 'Parent_Municipality', 
                                                 '6-59m_Total', '6-12m_Total', '13-23m_Total', '24-59m_Total',
                                                 '6-59m_M', '6-59m_F', '6-12m_M', '6-12m_F', '13-23m_M', '13-23m_F', '24-59m_M', '24-59m_F',
                                                 'VitA_6-11m_Total', 'VitA_12-59m_Total', 'VitA_Total',
                                                 'VitA_6-11m_M', 'VitA_6-11m_F', 'VitA_12-59m_M', 'VitA_12-59m_F', 'VitA_Total_M', 'VitA_Total_F',
                                                 'Act_MR_6-59m_Total', 'Act_MR_6-12m_Total', 'Act_MR_13-23m_Total', 'Act_MR_24-59m_Total',
                                                 'Act_VitA_6-11m_Total', 'Act_VitA_12-59m_Total', 'Act_VitA_Total']].copy()
                            
                            df_push.columns = [
                                'code', 'location', 'level', 'parent_province', 'parent_municipality', 
                                'grand_total_6_59m', 'grand_total_6_12m', 'grand_total_13_23m', 'grand_total_24_59m',
                                'mr_6_59m_m', 'mr_6_59m_f', 'mr_6_12m_m', 'mr_6_12m_f', 'mr_13_23m_m', 'mr_13_23m_f', 'mr_24_59m_m', 'mr_24_59m_f',
                                'vita_6_11m', 'vita_12_59m', 'vita_total',
                                'vita_6_11m_m', 'vita_6_11m_f', 'vita_12_59m_m', 'vita_12_59m_f', 'vita_total_m', 'vita_total_f',
                                'actual_mr_6_59m_total', 'actual_mr_6_12m_total', 'actual_mr_13_23m_total', 'actual_mr_24_59m_total',
                                'actual_vita_6_11m_total', 'actual_vita_12_59m_total', 'actual_vita_total'
                            ]
                            
                            num_cols = df_push.columns[5:]
                            for c in num_cols:
                                df_push[c] = pd.to_numeric(df_push[c], errors='coerce').fillna(0).astype(int)
                            
                            df_push = df_push.replace({np.nan: None})
                            supabase.table('targets').upsert(df_push.to_dict(orient='records')).execute()
                            
                            st.success("✅ Mega-Sync Complete: Targets, Genders & Actuals Synced!")
                            st.cache_data.clear()
                        except Exception as e:
                            st.error(f"Target Sync Failed: {e}")
                
                st.divider()

                st.markdown("### 📥 Phase 2: VaccTrack Raw Data Sync")
                st.write("Upload the daily export from the `VaccTrack_Raw` Google Sheet into the Accomplishment Database. This powers the MR, Vit A, and Wastage tabs.")
                
                if st.button("Sync Daily VaccTrack Accomplishments", type="primary", use_container_width=True):
                     st.info("Pipeline ready! We will connect this mapping engine once the Vit A target format is confirmed and you have pasted your first batch of export data.")
                
                st.divider()
                
                st.markdown("### 🔐 User Account Management")
                st.write("Edit user roles, approval status, and assign them to specific municipalities to restrict their encoding access.")
                res_users = supabase.table('user_accounts').select('*').execute()
                if res_users.data:
                    users_admin_df = pd.DataFrame(res_users.data)
                    users_admin_df['contact_info'] = users_admin_df['contact_info'].fillna("").astype(str)
                    if 'assigned_municipality' not in users_admin_df.columns:
                         users_admin_df['assigned_municipality'] = "None"
                         
                    cols = ['username', 'name', 'role', 'assigned_municipality', 'account_status', 'contact_info', 'failed_attempts', 'password_hash']
                    users_admin_df = users_admin_df[cols]
                    
                    edited_users = st.data_editor(
                        users_admin_df,
                        column_config={
                            "account_status": st.column_config.SelectboxColumn("Account Status", width="medium", options=["Approved", "Pending", "Pending Reset", "Locked", "Denied", "Revoked"], required=True),
                            "assigned_municipality": st.column_config.SelectboxColumn("Assigned Territory", width="medium", options=["None", "Abra"] + abra_munis),
                            "password_hash": None, 
                            "username": st.column_config.TextColumn("Username", disabled=True),
                            "contact_info": st.column_config.TextColumn("Contact Info", width="medium"),
                            "failed_attempts": st.column_config.NumberColumn("Strikes", width="small", disabled=True) 
                        },
                        use_container_width=True,
                        num_rows="dynamic",
                        key="user_editor"
                    )
                    
                    if st.button("💾 Save User Changes", type="secondary"):
                        try:
                            edited_users.loc[edited_users['account_status'] == 'Approved', 'failed_attempts'] = 0
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
