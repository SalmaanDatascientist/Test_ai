import streamlit as st
import os
import json
import base64
import datetime
import uuid
import requests
import hashlib
from PIL import Image
from groq import Groq
from openai import OpenAI
import PyPDF2
import re
import urllib.parse
from streamlit_gsheets import GSheetsConnection

# -----------------------------------------------------------------------------
# 1. PAGE CONFIGURATION
# -----------------------------------------------------------------------------
try:
    im = Image.open("logo.png")
except:
    im = "TMM"

st.set_page_config(
    page_title="The Molecular Man | Expert Tuition Solutions",
    page_icon=im,
    layout="wide",
    initial_sidebar_state="collapsed"
)

# -----------------------------------------------------------------------------
# 2. SESSION STATE & FILE SETUP
# -----------------------------------------------------------------------------
if 'page' not in st.session_state:
    st.session_state.page = "Home"

if "username" not in st.session_state:
    st.session_state.username = "Student"

# Auth State for Live Class
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

# AI Tutor State
if "aya_messages" not in st.session_state:
    st.session_state.aya_messages = []

# Mock Test State
if 'mt_questions' not in st.session_state: st.session_state.mt_questions = None
if 'mt_answers' not in st.session_state: st.session_state.mt_answers = {}
if 'mt_feedback' not in st.session_state: st.session_state.mt_feedback = None

# Files
NOTIFICATIONS_FILE = "notifications.json"
LIVE_STATUS_FILE = "live_status.json"

def init_files():
    if not os.path.exists(NOTIFICATIONS_FILE):
        with open(NOTIFICATIONS_FILE, "w") as f:
            json.dump([], f)
    if not os.path.exists(LIVE_STATUS_FILE):
        with open(LIVE_STATUS_FILE, "w") as f:
            json.dump({"is_live": False, "topic": "", "link": ""}, f)
init_files()

# -----------------------------------------------------------------------------
# 3. HELPER FUNCTIONS
# -----------------------------------------------------------------------------
def get_image_path(filename_base):
    extensions = [".png", ".jpg", ".jpeg", ".webp", ".gif"]
    paths = [f"images/{filename_base}", f"assets/{filename_base}", filename_base, f"./{filename_base}"]
    for path in paths:
        for ext in extensions:
            full_path = path + ext
            if os.path.exists(full_path):
                return full_path
    return None

def render_image(filename, caption=None, width=None, use_column_width=False):
    img_path = get_image_path(filename)
    try:
        if img_path:
            if use_column_width:
                st.image(img_path, caption=caption, use_container_width=True)
            else:
                st.image(img_path, caption=caption, width=width)
            return True
        return False
    except:
        return False

# Auth Helpers
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def login_user(username, password):
    try:
        # Establish connection
        conn = st.connection("gsheets", type=GSheetsConnection)
        
        # Read the Google Sheet data into a dataframe
        df = conn.read(spreadsheet="https://docs.google.com/spreadsheets/d/18o58Ot15bBL2VA4uMib_HWJWgd112e2dKuil2YwojDk/edit?usp=sharing")
        
        # BULLETPROOF FIX: Strip hidden spaces/newlines from columns and data
        df.columns = df.columns.str.strip()
        df['username'] = df['username'].astype(str).str.strip()
        df['password'] = df['password'].astype(str).str.strip()
        
        # Clean the typed input just in case
        clean_username = username.strip()
        clean_password = password.strip()
        
        # Check if username exists
        user_row = df[df['username'] == clean_username]
        
        if not user_row.empty:
            stored_password = str(user_row.iloc[0]['password'])
            if stored_password == clean_password:
                return True
                
        return False
    except Exception as e:
        st.error(f"Login Error: {e}")
        return False

# Data Helpers
def get_notifications():
    try:
        with open(NOTIFICATIONS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def add_notification(message):
    notifs = get_notifications()
    new_notif = {"date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "message": message}
    notifs.insert(0, new_notif)
    with open(NOTIFICATIONS_FILE, "w") as f:
        json.dump(notifs, f)

def get_live_status():
    try:
        with open(LIVE_STATUS_FILE, "r") as f:
            return json.load(f)
    except:
        return {"is_live": False, "topic": "", "link": ""}

def set_live_status(is_live, topic="", link=""):
    status = {"is_live": is_live, "topic": topic, "link": link}
    with open(LIVE_STATUS_FILE, "w") as f:
        json.dump(status, f)

# -----------------------------------------------------------------------------
# 4. CSS STYLING
# -----------------------------------------------------------------------------
st.markdown("""
<style>
    /* GLOBAL STYLES */
    .stApp {
        background: linear-gradient(135deg, #004e92 0%, #000428 100%) !important;
        background-attachment: fixed;
    }
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 5rem !important;
    }
    h1, h2, h3, h4, h5, h6, p, div, span, li, label, .stMarkdown {
        color: #ffffff !important;
    }
    
    /* BUTTONS */
    div.stButton > button {
        background: linear-gradient(90deg, #1e3a5f, #3b6b9e, #1e3a5f);
        color: white !important; border-radius: 25px !important; border: 1px solid rgba(255,255,255,0.2) !important;
    }
    div.stButton > button:hover { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(0,0,0,0.3); }
    div[data-testid="stFormSubmitButton"] > button {
        background: #1e3a5f !important; color: #ffffff !important; border: 2px solid white !important;
    }
    div[data-testid="stFormSubmitButton"] > button p { color: #ffffff !important; }
    
    /* INPUTS */
    .stTextInput>div>div>input, .stTextArea>div>div>textarea, .stSelectbox>div>div {
        background-color: rgba(255, 255, 255, 0.1) !important; color: #ffffff !important; border-radius: 8px; border: 1px solid rgba(255,255,255,0.3) !important;
    }
    
    /* UTILS */
    #MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
    .stDeployButton {display: none;}
    
    /* --- HERO AD BANNER --- */
    @keyframes neon-pulse {
        0% { box-shadow: 0 0 5px #ffd700, 0 0 15px #ffd700 inset; border-color: #ffd700; }
        50% { box-shadow: 0 0 20px #00ffff, 0 0 10px #00ffff inset; border-color: #00ffff; }
        100% { box-shadow: 0 0 5px #ffd700, 0 0 15px #ffd700 inset; border-color: #ffd700; }
    }
    .hero-ad-box {
        background: rgba(0, 0, 0, 0.7);
        backdrop-filter: blur(12px);
        border: 2px solid #ffd700;
        border-radius: 20px;
        padding: 40px 20px;
        margin: 30px 0;
        text-align: center;
        animation: neon-pulse 4s infinite alternate;
    }
    .hero-headline {
        font-size: 32px; font-weight: 900; text-transform: uppercase; letter-spacing: 1px;
        background: linear-gradient(to right, #ffffff, #ffd700); -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 15px;
    }
    .hero-subhead { font-size: 18px; color: #e0e0e0; margin-bottom: 25px; font-weight: 300; }
    .hero-suite-title {
        font-size: 22px; color: #00ffff; font-weight: 800; text-transform: uppercase; margin-bottom: 20px;
        text-shadow: 0 0 10px rgba(0, 255, 255, 0.5);
    }
    .hero-feature-grid { display: flex; justify-content: center; gap: 30px; margin-bottom: 30px; flex-wrap: wrap; }
    .hero-feature-item {
        background: rgba(255, 255, 255, 0.05); padding: 15px 25px; border-radius: 12px;
        border: 1px solid rgba(255, 255, 255, 0.1); text-align: left; max-width: 400px;
    }
    .hero-footer {
        font-size: 14px; font-weight: 800; color: #ff4d4d; letter-spacing: 1.5px;
        border-top: 1px solid rgba(255, 255, 255, 0.1); padding-top: 15px; margin-top: 10px;
    }

    /* --- ANIMATED FOUNDER HEADER --- */
    @keyframes fadeInUp {
        from { opacity: 0; transform: translate3d(0, 30px, 0); }
        to { opacity: 1; transform: translate3d(0, 0, 0); }
    }
    @keyframes border-flow {
        0% { border-color: rgba(255, 215, 0, 0.3); box-shadow: 0 0 15px rgba(255, 215, 0, 0.1); }
        50% { border-color: rgba(0, 255, 255, 0.5); box-shadow: 0 0 25px rgba(0, 255, 255, 0.2); }
        100% { border-color: rgba(255, 215, 0, 0.3); box-shadow: 0 0 15px rgba(255, 215, 0, 0.1); }
    }
    
    .founder-header-container {
        text-align: center;
        padding: 35px 20px;
        background: linear-gradient(135deg, rgba(0,0,0,0.4) 0%, rgba(0,0,0,0.2) 100%);
        backdrop-filter: blur(10px);
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.1);
        margin-bottom: 30px;
        animation: border-flow 4s infinite alternate;
        overflow: hidden;
        position: relative;
    }
    
    .founder-headline {
        font-size: 2.2rem;
        font-weight: 900;
        letter-spacing: -0.5px;
        background: linear-gradient(to right, #ffffff 0%, #a1c4fd 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 15px;
        opacity: 0;
        animation: fadeInUp 0.8s ease-out forwards;
    }
    
    .founder-subhead {
        font-size: 1.2rem;
        color: #e2e8f0;
        font-weight: 400;
        margin-bottom: 15px;
        opacity: 0;
        animation: fadeInUp 0.8s ease-out 0.3s forwards;
    }
    
    .founder-tagline {
        font-size: 1rem;
        color: #ffd700;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 2px;
        opacity: 0;
        animation: fadeInUp 0.8s ease-out 0.6s forwards;
    }

    /* Live Class Specifics */
    .notif-card {
        background: rgba(255, 215, 0, 0.1);
        border-left: 4px solid #ffd700;
        padding: 15px;
        margin-bottom: 10px;
        border-radius: 5px;
    }
    @keyframes pulse-btn {
        0% { transform: scale(1); }
        50% { transform: scale(1.05); }
        100% { transform: scale(1); }
    }
    .live-button-container {
        text-align: center;
        margin-top: 20px;
        animation: pulse-btn 2s infinite;
    }

    /* --- CRITICAL FIX FOR TESTIMONIALS --- */
    .white-card-fix {
        background-color: white !important;
        color: black !important;
        padding: 20px !important;
        border-radius: 10px !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1) !important;
        margin-bottom: 20px !important;
    }
    .white-card-fix *, .white-card-fix p, .white-card-fix span, .white-card-fix div, .white-card-fix h1, .white-card-fix h2, .white-card-fix h3 {
        color: #000000 !important;
    }
</style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 5. NAVIGATION (With Animated Header)
# -----------------------------------------------------------------------------
st.markdown("""
<div class="founder-header-container">
<div class="founder-headline">Other Apps Were Coded by Engineers. This One Was Coded by Your Master Tutor - Mohammed Salmaan.</div>
<div class="founder-subhead">The only online tuition service in the world running on a proprietary engine built by the Founder.</div>
<div class="founder-tagline">Pure Teaching Intelligence. Zero Corporate Noise.</div>
</div>
""", unsafe_allow_html=True)

st.markdown("### 🧭 Main Menu")
col1, col2, col3, col4, col5, col6 = st.columns(6)
with col1:
    if st.button("🏠 Home", use_container_width=True): st.session_state.page = "Home"; st.rerun()
with col2:
    if st.button("📚 Services", use_container_width=True): st.session_state.page = "Services"; st.rerun()
with col3:
    if st.button("🔴 Live Class", use_container_width=True): st.session_state.page = "Live Class"; st.rerun()
with col4:
    if st.button("💬 Stories", use_container_width=True): st.session_state.page = "Testimonials"; st.rerun()
with col5:
    if st.button("🐍 Bootcamp", use_container_width=True): st.session_state.page = "Bootcamp"; st.rerun()
with col6:
    if st.button("📞 Contact", use_container_width=True): st.session_state.page = "Contact"; st.rerun()

st.write("")
st.markdown("### 🤖 AI Power Tools (Free)")
ai_col1, ai_col2 = st.columns(2)
with ai_col1:
    if st.button("🧠 Chat with AyA (AI Tutor)", use_container_width=True, type="primary"): 
        st.session_state.page = "AyA_AI"
        st.rerun()
with ai_col2:
    if st.button("📝 Generate Mock Test", use_container_width=True, type="primary"): 
        st.session_state.page = "Mock_Test"
        st.rerun()

st.divider()

# -----------------------------------------------------------------------------
# 6. PAGE LOGIC
# -----------------------------------------------------------------------------

# ==========================================
# PAGE: HOME
# ==========================================
if st.session_state.page == "Home":
    
    # 1. Logo and Intro
    logo_col1, logo_col2 = st.columns([1, 2])
    with logo_col1:
        with st.container(border=True):
            if not render_image("logo", use_column_width=True):
                st.markdown("# 🧪")
                st.markdown("### The Molecular Man")
    with logo_col2:
        with st.container(border=True):
            st.markdown("# Expert Tuition for Excellence 🎓")
            st.markdown("### Personalized coaching in Mathematics, Physics, Chemistry & Biology")
            st.write("For Classes 6-12 & Competitive Exams (NEET/JEE/Boards)")
            st.write("")
            st.link_button("📱 Book Free Trial", "https://wa.me/917339315376", use_container_width=True)

    # 2. DYNAMIC ADVERTISEMENT
    st.markdown("""
<div class="hero-ad-box">
<div class="hero-headline">🚨 The Education System Just Got a Reality Check</div>
<div class="hero-subhead">
Stop paying for "premium" test series. The corporate coaching giants are scared.
</div>
<div class="hero-suite-title">INTRODUCING: THE MOLECULAR MAN AI SUITE</div>
<div class="hero-feature-grid">
<div class="hero-feature-item">
<span style="font-size: 20px; color: #ffd700;">1. 🧠 AyA (AI Tutor)</span><br>
<span style="font-size: 16px; color: #e0e0e0;">She doesn't sleep. She solves PDFs & problems instantly.</span>
</div>
<div class="hero-feature-item">
<span style="font-size: 20px; color: #ffd700;">2. 📝 Infinite Mock Tests</span><br>
<span style="font-size: 16px; color: #e0e0e0;">Generate unlimited tests for ANY Board/Subject for ₹0.</span>
</div>
</div>
<div class="hero-footer">
🚫 NO SUBSCRIPTIONS. NO HIDDEN FEES. PURE TEACHING INTELLIGENCE.
</div>
</div>
""", unsafe_allow_html=True)

    # 3. Stats
    st.markdown("## 📊 Our Impact")
    m1, m2, m3, m4 = st.columns(4)
    with m1: st.metric("Students Taught", "500+")
    with m2: st.metric("Success Rate", "100%")
    with m3: st.metric("Support", "24/7")
    with m4: st.metric("Experience", "5+ Years")

    st.markdown("## 🎯 What We Offer")
    s1, s2, s3 = st.columns(3)
    with s1:
        with st.container(border=True):
            st.markdown("#### 👨‍🏫 Expert Tutoring")
            st.write("One-on-one and small group classes for Classes 6-12.")
    with s2:
        with st.container(border=True):
            st.markdown("#### 📚 Comprehensive Material")
            st.write("Access to curated notes, practice problems, and revision guides.")
    with s3:
        with st.container(border=True):
            st.markdown("#### 🐍 Python Bootcamp")
            st.write("Weekend intensive courses in Data Science & AI.")

# ==========================================
# PAGE: AyA AI TUTOR
# ==========================================
elif st.session_state.page == "AyA_AI":
    # CSS FIX: This makes the input text visible
    st.markdown("""
    <style>
        .stTextArea textarea {
            color: #ffffff !important;
            background-color: #262730 !important;
            border: 1px solid #4B4B4B;
        }
        .stChatInput textarea {
            color: #ffffff !important;
            background-color: #262730 !important;
        }
        ::placeholder {
            color: #d3d3d3 !important;
            opacity: 1;
        }
    </style>
    """, unsafe_allow_html=True)

    # Header
    st.markdown("## 🧠 AyA - The Molecular Man AI")
    st.caption("Your personal AI Tutor for Math, Science, Coding, and Visual Diagrams.")

    # API Key Setup
    try:
        groq_api_key = st.secrets["GROQ_API_KEY"]
        groq_client = Groq(api_key=groq_api_key)
    except Exception:
        st.error("⚠️ GROQ_API_KEY not found in Secrets! Please check your .streamlit/secrets.toml file.")
        st.stop()

    SYSTEM_PROMPT = """You are **Aya**, the Lead AI Tutor at **The Molecular Man Expert Tuition Solutions**. 
    Your Mission: Guide students from "Zero" to "Hero".
    Tone: Encouraging, clear, patient, and intellectually rigorous.
    Structure: 🧠 CONCEPT -> 🌍 CONTEXT -> ✍️ SOLUTION -> ✅ ANSWER -> 🚀 HERO TIP.

    CRITICAL INSTRUCTION FOR IMAGES: 
    If a student asks for a visual, diagram, or illustration, output a simple text description wrapped in image tags.
    Format: <IMAGE>Detailed description of the educational diagram</IMAGE>
    
    Example:
    <IMAGE>Highly detailed educational diagram showing lateral displacement of a light ray passing through a glass slab</IMAGE>
    """

    # Input Section
    with st.expander("📝 New Problem Input", expanded=(len(st.session_state.aya_messages) == 0)):
        input_type = st.radio("Input Method:", ["📄 Text Problem", "📕 Upload PDF"], horizontal=True)
        
        if input_type == "📄 Text Problem":
            user_text = st.text_area("Paste question:", height=100)
            if st.button("Ask AyA 🚀", use_container_width=True):
                if user_text:
                    st.session_state.aya_messages = [] 
                    st.session_state.aya_messages.append({"role": "user", "content": f"PROBLEM:\n{user_text}"})
                    st.rerun()

        elif input_type == "📕 Upload PDF":
            uploaded_file = st.file_uploader("Upload PDF", type=["pdf"])
            if st.button("Analyze PDF 🚀", use_container_width=True):
                if uploaded_file:
                    try:
                        pdf_reader = PyPDF2.PdfReader(uploaded_file)
                        pdf_text = ""
                        for page_num in range(min(2, len(pdf_reader.pages))):
                            pdf_text += pdf_reader.pages[page_num].extract_text()[:3000]
                        
                        st.session_state.aya_messages = [] 
                        st.session_state.aya_messages.append({"role": "user", "content": f"PROBLEM from PDF:\n{pdf_text}"})
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error reading PDF: {e}")

    # --- BULLETPROOF CHAT RENDERER ---
    # This function catches BOTH normal markdown images AND custom <IMAGE> tags, 
    # forcing Streamlit to render them natively so they never break.
    def render_chat_content(text):
        # 1. Convert any stubborn markdown images ![alt](url) into <IMAGE> tags
        text = re.sub(r'!\[([^\]]*)\]\([^\)]*\)', r'<IMAGE>\1</IMAGE>', text)
        
        # 2. Split and render
        parts = re.split(r'<IMAGE>(.*?)</IMAGE>', text, flags=re.IGNORECASE | re.DOTALL)
        for i, part in enumerate(parts):
            if i % 2 == 0:
                if part.strip():
                    st.markdown(part)
            else:
                pr



















