import logging
import time
from datetime import datetime

import pytz
import streamlit as st

from auth_utils import authenticate_user
from core.data import init_supabase
from programs.sbi import render_sbi_dashboard
from programs.sia import render_sia_dashboard

logger = logging.getLogger("abra_nip_dashboard")
logger.setLevel(logging.INFO)

# # ==========================================
# 1. PAGE CONFIGURATION & UI/UX STYLING
# ==========================================
st.set_page_config(page_title="Abra NIP Dashboard", page_icon="https://github.com/RJA24/abra_nip_dashboard/blob/main/PHO%20logo.png?raw=true?raw=true", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    @import url('https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css');
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

try:
    supabase = init_supabase()
except Exception as e:
    st.error("⚠️ Supabase Connection Error: Please ensure SUPABASE_URL and SUPABASE_KEY are set in Streamlit Secrets.")
    st.stop()



# ==========================================
# 3. SECURITY, SESSION STATE & TIMEOUT
# ==========================================
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'active_program' not in st.session_state:         
    st.session_state['active_program'] = None
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
# 4. THE WELCOME PAGE
# ==========================================
def _start_dashboard_session(display_name, role, assigned_muni="Abra Province", username=""):
    """Initialize the Streamlit session and create a best-effort access log."""
    st.session_state['logged_in'] = True
    st.session_state['username'] = username
    st.session_state['user_name'] = display_name
    st.session_state['user_role'] = role
    st.session_state['assigned_muni'] = assigned_muni or "Abra Province"
    st.session_state['last_active'] = time.time()
    st.session_state['login_time'] = time.time()
    st.session_state['last_session_log_write'] = 0

    try:
        manila_tz = pytz.timezone('Asia/Manila')
        current_time_str = datetime.now(manila_tz).strftime("%Y-%m-%d %I:%M:%S %p")
        log_response = supabase.table('access_logs').insert({
            'timestamp': current_time_str,
            'name': display_name,
            'role': role,
            'action': 'Active Session'
        }).execute()
        if log_response.data:
            st.session_state['log_id'] = log_response.data[0]['id']
    except Exception:
        logger.exception("Unable to create access log")


if not st.session_state.get('logged_in', False):
    bg_css = """
    <style>
    .stApp {
        background: linear-gradient(
            rgba(240, 242, 246, 0.8),
            rgba(240, 242, 246, 0.8)
        ),
        url("https://github.com/RJA24/abra_sia_2026/blob/main/Abra%20(2).png?raw=true") !important;
        background-size: cover !important;
        background-position: center !important;
        background-attachment: fixed !important;
    }
    header[data-testid="stHeader"] { background: rgba(0,0,0,0) !important; }
    </style>
    """
    st.markdown(bg_css, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2.5, 1])
    with col2:
        st.markdown("<h1 style='text-align: center; font-family: \"Arial Black\", Impact, sans-serif; letter-spacing: 2px; text-transform: uppercase;'>National Immunization Program</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: #475569; font-size: 1.2rem; margin-bottom: 2rem;'>Secure Provincial Command Center</p>", unsafe_allow_html=True)

        account_tab, guest_tab = st.tabs(["Account Login", "Guest Access"])

        with account_tab:
            with st.form("account_login_form", border=True):
                st.markdown("### 🔐 Registered Account")
                username_input = st.text_input("Username", key="login_username").strip()
                password_input = st.text_input("Password", type="password", key="login_password")
                submit_account = st.form_submit_button("Sign In", type="primary", use_container_width=True)

                if submit_account:
                    result = authenticate_user(supabase, username_input, password_input)
                    if not result.ok:
                        st.error(result.message)
                    else:
                        user = result.user or {}
                        display_name = str(user.get('name') or user.get('username') or username_input)
                        role = str(user.get('role') or 'Guest / Viewer')
                        assigned_muni = str(user.get('assigned_muni') or user.get('municipality') or 'Abra Province')
                        _start_dashboard_session(display_name, role, assigned_muni, username_input)
                        st.toast(f"Welcome, {display_name}!", icon="✅")
                        st.rerun()

        with guest_tab:
            with st.form("guest_login_form", border=True):
                st.markdown("### 👋 Visitor Access")
                st.caption("Guest access can view dashboards but cannot use administration controls.")
                visitor_name = st.text_input("Your Name", placeholder="e.g., Dr. Cruz / DOH Rep", key="guest_name").strip()
                submit_guest = st.form_submit_button("Continue as Guest", use_container_width=True)

                if submit_guest:
                    if not visitor_name:
                        st.error("Please enter your name to continue.")
                    else:
                        db_name = f"Visitor ({visitor_name})"
                        _start_dashboard_session(db_name, "Guest", "Abra Province", "")
                        st.toast(f"Welcome, {visitor_name}!", icon="👋")
                        st.rerun()

    st.stop()

# ==========================================
# 4.5. THE PROGRAM ROUTING MENU
# ==========================================
# If the user is logged in, but hasn't picked a program yet, show the big buttons!
if st.session_state.get('logged_in', False) and st.session_state.get('active_program') is None:
    
    # Keep the beautiful mountain background active
    bg_css = """
    <style>
    .stApp {
        background: linear-gradient(rgba(240, 242, 246, 0.4), rgba(240, 242, 246, 0.4)), 
        url("https://github.com/RJA24/abra_sia_2026/blob/main/Abra%20(2).png?raw=true") !important;
        background-size: cover !important;
        background-position: center !important;
        background-attachment: fixed !important;
    }
    header[data-testid="stHeader"] { background: rgba(0,0,0,0) !important; }
    
    /* 🎨 CSS Magic for the Giant Translucent Pill Buttons */
    div.element-container:has(.big-btn-marker) + div.element-container button {
        height: 180px !important;
        border-radius: 90px !important; 
        background-color: rgba(255, 255, 255, 0.45) !important; 
        backdrop-filter: blur(10px) !important;
        border: 2px solid rgba(255, 255, 255, 0.6) !important;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1) !important;
        transition: all 0.3s ease-in-out !important;
    }
    div.element-container:has(.big-btn-marker) + div.element-container button p {
        font-size: 36px !important;
        font-family: "Arial Black", Impact, sans-serif !important;
        font-weight: 900 !important;
        color: #000000 !important;
        letter-spacing: 2px !important;
    }
    div.element-container:has(.big-btn-marker) + div.element-container button:hover {
        background-color: rgba(255, 255, 255, 0.7) !important;
        transform: translateY(-5px) !important;
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.2) !important;
    }
    </style>
    """
    st.markdown(bg_css, unsafe_allow_html=True)
    st.markdown("<br><br>", unsafe_allow_html=True)
    
    # 🏛️ Logos and Title
    c_logo1, c_logo2, c_logo3 = st.columns([1, 2, 1])
    with c_logo2:
        st.markdown(
            '''
            <div style="text-align: center; margin-bottom: 20px;">
                <img src="https://upload.wikimedia.org/wikipedia/commons/1/1a/Abra_provincial_seal.png" width="90" style="margin-right: 15px; filter: drop-shadow(0px 4px 6px rgba(0,0,0,0.2));">
                <img src="https://github.com/RJA24/abra_sia_2026/blob/main/PHO%20logo.png?raw=true" width="90" style="filter: drop-shadow(0px 4px 6px rgba(0,0,0,0.2));">
            </div>
            ''', 
            unsafe_allow_html=True
        )
        st.markdown("<h1 style='text-align: center; font-family: \"Arial Black\", Impact, sans-serif; letter-spacing: 2px;'>NATIONAL IMMUNIZATION PROGRAM</h1>", unsafe_allow_html=True)
    
    st.markdown("<br><br><br>", unsafe_allow_html=True)

    # 🔘 The Big Buttons
    col_gap1, col_btn1, col_gap2, col_btn2, col_gap3 = st.columns([1, 3, 0.5, 3, 1])
    
    with col_btn1:
        st.markdown('<span class="big-btn-marker"></span>', unsafe_allow_html=True)
        if st.button("MR SIA", use_container_width=True):
            st.session_state['active_program'] = 'SIA'
            st.rerun()
            
    with col_btn2:
        st.markdown('<span class="big-btn-marker"></span>', unsafe_allow_html=True)
        if st.button("SBI", use_container_width=True):
            st.session_state['active_program'] = 'SBI'
            st.rerun()

    # Stop execution here so the dashboard underneath doesn't load yet
    st.stop()



# ==========================================
# 5. PROGRAM ROUTER
# ==========================================
active_program = st.session_state.get("active_program")

if active_program == "SBI":
    render_sbi_dashboard(supabase)
    st.stop()

if active_program == "SIA":
    render_sia_dashboard(supabase)
    st.stop()

# Safety fallback for an unknown program value.
st.session_state["active_program"] = None
st.rerun()
