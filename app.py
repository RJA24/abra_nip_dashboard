import html
import logging
import time
from datetime import datetime

import pytz
import streamlit as st

from auth_utils import authenticate_user
from core.data import init_supabase
from programs.sbi import render_sbi_dashboard
from programs.sia import render_sia_dashboard
from admin import render_admin_dashboard

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
    st.error("Supabase connection failed. Please check SUPABASE_URL and SUPABASE_KEY in Streamlit Secrets.")
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
        st.toast("Session expired after 30 minutes of inactivity.")
        time.sleep(1)
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


def _logout_session():
    """Clear the current application session and return to the login screen."""
    st.session_state.clear()
    st.rerun()


if not st.session_state.get('logged_in', False):
    bg_css = """
    <style>
    .stApp {
        background: linear-gradient(
            rgba(240, 242, 246, 0.80),
            rgba(240, 242, 246, 0.80)
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
        st.markdown("<h1 style='text-align:center;font-family:Impact,sans-serif;letter-spacing:2px;text-transform:uppercase;'>National Immunization Program</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align:center;color:#475569;font-size:1.05rem;margin-bottom:1.5rem;'>Abra Provincial Dashboard</p>", unsafe_allow_html=True)

        account_tab, guest_tab = st.tabs(["Account Login", "Guest Access"])

        with account_tab:
            with st.form("account_login_form", border=True):
                st.markdown("### Registered Account")
                username_input = st.text_input("Username", key="login_username").strip()
                password_input = st.text_input("Password", type="password", key="login_password")
                submit_account = st.form_submit_button("Sign In", type="primary", width="stretch")

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
                        st.session_state['welcome_notice'] = f"Welcome, {display_name}."
                        st.rerun()

        with guest_tab:
            with st.form("guest_login_form", border=True):
                st.markdown("### Visitor Access")
                visitor_name = st.text_input("Your Name", placeholder="e.g., Dr. Cruz / DOH Rep", key="guest_name").strip()
                submit_guest = st.form_submit_button("Continue as Guest", width="stretch")

                if submit_guest:
                    if not visitor_name:
                        st.error("Please enter your name to continue.")
                    else:
                        db_name = f"Visitor ({visitor_name})"
                        _start_dashboard_session(db_name, "Guest", "Abra Province", "")
                        st.session_state['welcome_notice'] = f"Welcome, {visitor_name}."
                        st.rerun()

    st.stop()

# ==========================================
# 4.5. PROGRAM ROUTING MENU
# ==========================================
if st.session_state.get('logged_in', False) and st.session_state.get('active_program') is None:
    menu_css = """
    <style>
    .stApp {
        background:
            linear-gradient(rgba(244,247,250,0.28), rgba(244,247,250,0.42)),
            url("https://github.com/RJA24/abra_sia_2026/blob/main/Abra%20(2).png?raw=true") !important;
        background-size: cover !important;
        background-position: center center !important;
        background-attachment: fixed !important;
    }
    header[data-testid="stHeader"] { background: rgba(0,0,0,0) !important; }

    .block-container {
        max-width: 1320px !important;
        padding-top: 0.15rem !important;
        padding-bottom: 1.5rem !important;
    }

    .nip-menu-hero {
        max-width: 1180px;
        margin: 0.15rem auto 0.8rem auto;
        text-align: center;
    }
    .nip-menu-logos {
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 14px;
        margin-bottom: 0.65rem;
    }
    .nip-menu-logos img {
        width: 76px;
        height: 76px;
        object-fit: contain;
        filter: drop-shadow(0 5px 8px rgba(0,0,0,0.18));
    }
    .nip-menu-title {
        margin: 0;
        font-family: "Arial Black", Impact, sans-serif;
        font-size: clamp(2.35rem, 4.1vw, 4rem);
        line-height: 1;
        letter-spacing: 0.045em;
        color: #172033;
        text-transform: uppercase;
        text-shadow: 0 2px 8px rgba(255,255,255,0.28);
        white-space: nowrap;
    }

    .program-button-spacer {
        height: clamp(2.5rem, 7vh, 5.25rem);
    }

    div.element-container:has(.program-btn-marker) + div.element-container button {
        height: 148px !important;
        border-radius: 999px !important;
        background: rgba(255,255,255,0.58) !important;
        backdrop-filter: blur(14px) !important;
        -webkit-backdrop-filter: blur(14px) !important;
        border: 1px solid rgba(255,255,255,0.88) !important;
        box-shadow: 0 12px 32px rgba(15,23,42,0.14) !important;
        transition: transform 0.18s ease, background 0.18s ease, box-shadow 0.18s ease !important;
    }
    div.element-container:has(.program-btn-marker) + div.element-container button p {
        font-family: "Arial Black", Impact, sans-serif !important;
        font-size: clamp(1.6rem, 2.5vw, 2.2rem) !important;
        font-weight: 900 !important;
        letter-spacing: 0.08em !important;
        color: #111827 !important;
    }
    div.element-container:has(.program-btn-marker) + div.element-container button:hover {
        transform: translateY(-3px) !important;
        background: rgba(255,255,255,0.76) !important;
        box-shadow: 0 16px 38px rgba(15,23,42,0.20) !important;
    }

    .admin-button-spacer {
        height: 0.9rem;
    }
    div.element-container:has(.admin-btn-marker) + div.element-container button {
        height: 48px !important;
        border-radius: 12px !important;
        background: rgba(255,255,255,0.62) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border: 1px solid rgba(255,255,255,0.88) !important;
        box-shadow: 0 8px 22px rgba(15,23,42,0.12) !important;
    }
    div.element-container:has(.admin-btn-marker) + div.element-container button p {
        font-size: 0.98rem !important;
        font-weight: 700 !important;
        letter-spacing: 0.02em !important;
        color: #1f2937 !important;
    }
    div.element-container:has(.admin-btn-marker) + div.element-container button p::before {
        content: "\f3ed";
        font-family: "Font Awesome 6 Free" !important;
        font-weight: 900 !important;
        margin-right: 0.5rem;
        color: #0033A0;
    }
    div.element-container:has(.admin-btn-marker) + div.element-container button:hover {
        background: rgba(255,255,255,0.80) !important;
        border-color: rgba(0,51,160,0.28) !important;
    }

    .nip-welcome-toast {
        position: fixed;
        top: 4.6rem;
        right: 1.6rem;
        z-index: 999999;
        display: flex;
        align-items: center;
        gap: 0.65rem;
        padding: 0.8rem 1rem;
        border-radius: 12px;
        background: rgba(255,255,255,0.94);
        border: 1px solid rgba(15,23,42,0.10);
        box-shadow: 0 12px 30px rgba(15,23,42,0.18);
        color: #1f2937;
        font-size: 0.95rem;
        font-weight: 600;
        animation: nipToastFade 4.6s ease forwards;
    }
    .nip-welcome-toast i {
        color: #15803d;
        font-size: 1.05rem;
    }
    @keyframes nipToastFade {
        0%, 78% { opacity: 1; transform: translateY(0); }
        100% { opacity: 0; transform: translateY(-6px); visibility: hidden; }
    }

    @media (max-width: 1000px) {
        .nip-menu-title {
            font-size: clamp(2rem, 5vw, 3.1rem);
            white-space: normal;
        }
    }

    @media (max-width: 800px) {
        .nip-menu-hero { margin-top: 0; }
        .nip-menu-logos img { width: 64px; height: 64px; }
        .program-button-spacer { height: 2rem; }
        .program-button-spacer {
        height: clamp(2.5rem, 7vh, 5.25rem);
    }

    div.element-container:has(.program-btn-marker) + div.element-container button {
            height: 108px !important;
            border-radius: 999px !important;
        }
    }
    </style>
    """
    st.markdown(menu_css, unsafe_allow_html=True)

    st.markdown(
        """
        <div class="nip-menu-hero">
            <div class="nip-menu-logos">
                <img src="https://upload.wikimedia.org/wikipedia/commons/1/1a/Abra_provincial_seal.png" alt="Province of Abra seal">
                <img src="https://github.com/RJA24/abra_sia_2026/blob/main/PHO%20logo.png?raw=true" alt="Provincial Health Office logo">
            </div>
            <h1 class="nip-menu-title">National Immunization Program</h1>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="program-button-spacer"></div>', unsafe_allow_html=True)

    welcome_notice = st.session_state.pop("welcome_notice", None)
    if welcome_notice:
        notice_html = (
            '<div class="nip-welcome-toast">'
            '<i class="fa-solid fa-circle-check"></i>'
            f'<span>{html.escape(str(welcome_notice))}</span>'
            '</div>'
        )
        st.markdown(notice_html, unsafe_allow_html=True)

    left_pad, mr_col, gap_one, sbi_col, right_pad = st.columns([1.0, 3.25, 0.45, 3.25, 1.0])

    with mr_col:
        st.markdown('<span class="program-btn-marker"></span>', unsafe_allow_html=True)
        if st.button("MR SIA", key="open_mr_sia", width="stretch"):
            st.session_state['active_program'] = 'SIA'
            st.rerun()

    with sbi_col:
        st.markdown('<span class="program-btn-marker"></span>', unsafe_allow_html=True)
        if st.button("SBI", key="open_sbi", width="stretch"):
            st.session_state['active_program'] = 'SBI'
            st.rerun()

    if st.session_state.get("user_role") == "System Admin":
        st.markdown('<div class="admin-button-spacer"></div>', unsafe_allow_html=True)
        admin_left, admin_col, admin_right = st.columns([2.35, 1.3, 2.35])
        with admin_col:
            st.markdown('<span class="admin-btn-marker"></span>', unsafe_allow_html=True)
            if st.button("Administration", key="open_admin", width="stretch"):
                st.session_state['active_program'] = 'ADMIN'
                st.rerun()

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

if active_program == "ADMIN":
    if st.session_state.get("user_role") != "System Admin":
        st.session_state["active_program"] = None
        st.rerun()
    render_admin_dashboard(supabase)
    st.stop()

# Safety fallback for an unknown program value.
st.session_state["active_program"] = None
st.rerun()
