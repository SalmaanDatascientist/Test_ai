import streamlit as st
import os
import json
import base64
import datetime
import uuid
import requests
import hashlib
import random
import re
import urllib.parse

from PIL import Image
from groq import Groq
from openai import OpenAI
import PyPDF2
from streamlit_gsheets import GSheetsConnection

# =============================================================================
# 1. PAGE CONFIGURATION
# =============================================================================
try:
    im = Image.open("logo.png")
except Exception:
    im = "TMM"

st.set_page_config(
    page_title="The Molecular Man | Expert Tuition Solutions",
    page_icon=im,
    layout="wide",
    initial_sidebar_state="collapsed",
)

# =============================================================================
# 2. SESSION STATE & FILE SETUP
# =============================================================================
if "page" not in st.session_state:
    st.session_state.page = "Home"
if "username" not in st.session_state:
    st.session_state.username = "Student"
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False
if "aya_messages" not in st.session_state:
    st.session_state.aya_messages = []
if "mt_questions" not in st.session_state:
    st.session_state.mt_questions = None
if "mt_answers" not in st.session_state:
    st.session_state.mt_answers = {}
if "mt_feedback" not in st.session_state:
    st.session_state.mt_feedback = None
if "mt_q_type" not in st.session_state:
    st.session_state.mt_q_type = "MCQ"

NOTIFICATIONS_FILE = "notifications.json"
LIVE_STATUS_FILE   = "live_status.json"

def init_files():
    if not os.path.exists(NOTIFICATIONS_FILE):
        with open(NOTIFICATIONS_FILE, "w") as f:
            json.dump([], f)
    if not os.path.exists(LIVE_STATUS_FILE):
        with open(LIVE_STATUS_FILE, "w") as f:
            json.dump({"is_live": False, "topic": "", "link": ""}, f)

init_files()

# =============================================================================
# 3. HELPER FUNCTIONS
# =============================================================================

# ── Image path helpers ────────────────────────────────────────────────────────
def get_image_path(filename_base):
    extensions = [".png", ".jpg", ".jpeg", ".webp", ".gif"]
    paths = [
        f"images/{filename_base}",
        f"assets/{filename_base}",
        filename_base,
        f"./{filename_base}",
    ]
    for path in paths:
        for ext in extensions:
            full = path + ext
            if os.path.exists(full):
                return full
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
    except Exception:
        return False

# ── Auth helpers ──────────────────────────────────────────────────────────────
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def login_user(username, password):
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df = conn.read(
            spreadsheet="https://docs.google.com/spreadsheets/d/18o58Ot15bBL2VA4uMib_HWJWgd112e2dKuil2YwojDk/edit?usp=sharing"
        )
        df.columns = df.columns.str.strip()
        df["username"] = df["username"].astype(str).str.strip()
        df["password"] = df["password"].astype(str).str.strip()
        user_row = df[df["username"] == username.strip()]
        if not user_row.empty:
            return str(user_row.iloc[0]["password"]) == password.strip()
        return False
    except Exception as e:
        st.error(f"Login Error: {e}")
        return False

# ── Notification / live-status helpers ───────────────────────────────────────
def get_notifications():
    try:
        with open(NOTIFICATIONS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def add_notification(message):
    notifs = get_notifications()
    notifs.insert(0, {
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "message": message,
    })
    with open(NOTIFICATIONS_FILE, "w") as f:
        json.dump(notifs, f)

def get_live_status():
    try:
        with open(LIVE_STATUS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"is_live": False, "topic": "", "link": ""}

def set_live_status(is_live, topic="", link=""):
    with open(LIVE_STATUS_FILE, "w") as f:
        json.dump({"is_live": is_live, "topic": topic, "link": link}, f)

# ── AyA image fetch (SERVER-SIDE — fixes broken image bug) ───────────────────
@st.cache_data(show_spinner=False)
def fetch_pollinations_image(prompt: str, seed: int) -> bytes | None:
    """
    Downloads image bytes from Pollinations AI on the SERVER.
    Bypasses Streamlit's Content Security Policy that blocks
    external <img> tags rendered via st.markdown(), which is
    the root cause of the broken-image icon shown in the screenshot.
    Cached by (prompt, seed) so reruns don't re-download.
    """
    safe_prompt = urllib.parse.quote(prompt[:200])
    url = (
        f"https://image.pollinations.ai/prompt/{safe_prompt}"
        f"?width=800&height=400&nologo=true&seed={seed}"
    )
    try:
        resp = requests.get(url, timeout=15)
        ct = resp.headers.get("Content-Type", "")
        if resp.status_code == 200 and ct.startswith("image"):
            return resp.content
        return None
    except Exception:
        return None

def render_chat_content(text: str):
    """
    Parses AyA's response text.
    Converts ALL broken image-tag variants the LLM might produce into
    [IMAGE: desc], fetches bytes server-side, then renders with st.image().

    Variants handled:
        [IMAGE: desc]       ← our canonical format
        {IMAGE: desc}       ← curly brace variant
        [[IMAGE: desc]]     ← double bracket variant
        ![alt](url)         ← markdown image link
        ![alt]              ← broken markdown image (no url)
    """
    if not text:
        return

    # 1. Normalise every broken variant → [IMAGE: desc]
    text = re.sub(r'!\[([^\]]+)\](?:\([^\)]*\))?', r'[IMAGE: \1]', text)
    text = re.sub(r'\{IMAGE:\s*(.*?)\}',            r'[IMAGE: \1]', text, flags=re.IGNORECASE)
    text = re.sub(r'\[\[IMAGE:\s*(.*?)\]\]',        r'[IMAGE: \1]', text, flags=re.IGNORECASE)

    # 2. Split on [IMAGE: ...] tags
    parts = re.split(r'\[IMAGE:\s*(.*?)\]', text, flags=re.IGNORECASE | re.DOTALL)

    for i, part in enumerate(parts):
        if i % 2 == 0:
            if part.strip():
                st.markdown(part)
        else:
            prompt = part.strip().replace("\n", " ")
            if not prompt:
                continue

            st.markdown(f"🎨 **AyA Visual:** *{prompt}*")

            # Stable seed = same prompt always yields the same diagram
            seed = int(hashlib.md5(prompt.encode()).hexdigest(), 16) % 100_000

            with st.spinner("🖼️ Generating diagram..."):
                img_bytes = fetch_pollinations_image(prompt, seed)

            if img_bytes:
                st.image(img_bytes, caption=prompt, use_container_width=True)
            else:
                st.markdown(
                    f"""
                    <div style="border:2px dashed #555;border-radius:8px;padding:24px;
                                text-align:center;color:#aaa;background:#1a1a2e;margin:8px 0;">
                        🖼️ <b>Visual:</b> {prompt}<br>
                        <small>Image could not be loaded — try asking again.</small>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

# =============================================================================
# 4. CSS STYLING
# =============================================================================
st.markdown("""
<style>
/* ── Global ── */
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@700&family=Inter:wght@300;400;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* ── Hero banner ── */
.hero-banner {
    background: linear-gradient(135deg, #0a0a1a 0%, #0d1b2a 50%, #1a0a2e 100%);
    border: 1px solid #1e3a5f;
    border-radius: 16px;
    padding: 28px 36px;
    margin-bottom: 20px;
    position: relative;
    overflow: hidden;
}
.hero-banner::before {
    content: '';
    position: absolute;
    top: -50%;
    left: -50%;
    width: 200%;
    height: 200%;
    background: radial-gradient(ellipse at center, rgba(0,150,255,0.05) 0%, transparent 60%);
    pointer-events: none;
}
.hero-title {
    font-family: 'Orbitron', monospace;
    font-size: 1.1rem;
    color: #00bfff;
    letter-spacing: 2px;
    margin-bottom: 6px;
}
.hero-sub {
    color: #e0e0e0;
    font-size: 0.85rem;
    line-height: 1.6;
}

/* ── Metric cards ── */
.metric-card {
    background: linear-gradient(135deg, #0d1b2a, #1a0a2e);
    border: 1px solid #2a3a5f;
    border-radius: 12px;
    padding: 20px;
    text-align: center;
    margin-bottom: 10px;
}
.metric-value {
    font-family: 'Orbitron', monospace;
    font-size: 2rem;
    color: #00bfff;
    font-weight: 700;
}
.metric-label {
    color: #a0b0c0;
    font-size: 0.85rem;
    margin-top: 4px;
}

/* ── Announcement cards ── */
.notif-card {
    background: #0d1b2a;
    border-left: 3px solid #00bfff;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 10px;
}
.notif-date {
    font-size: 0.75rem;
    color: #607080;
    margin-bottom: 4px;
}
.notif-msg {
    color: #e0e0e0;
    font-size: 0.9rem;
}

/* ── Live banner ── */
.live-banner {
    background: linear-gradient(135deg, #1a0505, #2a0a0a);
    border: 2px solid #ff3333;
    border-radius: 12px;
    padding: 24px;
    text-align: center;
    margin: 10px 0;
}
.live-dot {
    display: inline-block;
    width: 12px;
    height: 12px;
    background: #ff3333;
    border-radius: 50%;
    animation: pulse 1.2s infinite;
    margin-right: 8px;
}
@keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(255,51,51,0.7); }
    70%  { box-shadow: 0 0 0 10px rgba(255,51,51,0); }
    100% { box-shadow: 0 0 0 0 rgba(255,51,51,0); }
}
.live-title {
    font-family: 'Orbitron', monospace;
    color: #ff6666;
    font-size: 1.3rem;
    margin-bottom: 6px;
}
.live-topic {
    color: #e0e0e0;
    font-size: 1rem;
    margin-bottom: 16px;
}
.join-btn {
    display: inline-block;
    background: linear-gradient(135deg, #ff3333, #cc0000);
    color: white !important;
    padding: 12px 32px;
    border-radius: 8px;
    text-decoration: none;
    font-weight: 600;
    font-size: 1rem;
    letter-spacing: 1px;
    transition: transform 0.2s;
}
.join-btn:hover { transform: scale(1.04); }

/* ── Testimonial card ── */
.testimonial-card {
    background: linear-gradient(135deg, #0d1b2a, #0a0a1a);
    border: 1px solid #1e3a5f;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 14px;
    position: relative;
}
.testimonial-card::before {
    content: '"';
    font-size: 4rem;
    color: #00bfff22;
    position: absolute;
    top: -10px;
    left: 12px;
    font-family: Georgia, serif;
    line-height: 1;
}
.testimonial-text {
    color: #d0e0f0;
    font-style: italic;
    margin-bottom: 10px;
    padding-left: 10px;
}
.testimonial-author {
    color: #00bfff;
    font-weight: 600;
    font-size: 0.85rem;
}

/* ── Result stat card ── */
.result-card {
    background: linear-gradient(135deg, #0a1628, #0d1b2a);
    border: 1px solid #2a3a5f;
    border-radius: 12px;
    padding: 24px;
    text-align: center;
}
.result-number {
    font-family: 'Orbitron', monospace;
    font-size: 2.5rem;
    font-weight: 700;
    color: #00bfff;
}
.result-label {
    color: #607080;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-top: 4px;
}
.result-title {
    color: #a0c0e0;
    font-weight: 600;
    margin-bottom: 6px;
}

/* ── Why-trust card ── */
.trust-card {
    background: #0d1b2a;
    border: 1px solid #1e3a5f;
    border-radius: 12px;
    padding: 20px;
    height: 100%;
}
.trust-card h4 {
    color: #00bfff;
    margin-bottom: 8px;
}
.trust-card p {
    color: #a0b0c0;
    font-size: 0.9rem;
}

/* ── Announcement box ── */
.announcement-box {
    background: linear-gradient(135deg, #0a1628 0%, #1a0a2e 100%);
    border: 1px solid #1e3a5f;
    border-radius: 16px;
    padding: 28px;
    margin-bottom: 20px;
}
.announcement-title {
    font-family: 'Orbitron', monospace;
    color: #ff6b35;
    font-size: 1.1rem;
    margin-bottom: 12px;
    letter-spacing: 2px;
}
.announcement-text {
    color: #d0e0f0;
    line-height: 1.7;
    font-size: 0.95rem;
}

/* ── AI feature card ── */
.ai-feature {
    background: linear-gradient(135deg, #0d1b2a, #0a1628);
    border: 1px solid #00bfff33;
    border-radius: 12px;
    padding: 18px;
    margin-bottom: 10px;
}
.ai-feature-title {
    color: #00bfff;
    font-weight: 700;
    font-size: 1rem;
    margin-bottom: 6px;
}
.ai-feature-text {
    color: #90a0b0;
    font-size: 0.85rem;
}

/* ── Footer ── */
.footer-box {
    background: linear-gradient(135deg, #050510, #0a0a1a);
    border: 1px solid #1e3a5f;
    border-radius: 16px;
    padding: 24px;
    text-align: center;
    margin-top: 40px;
}
.footer-tagline {
    font-family: 'Orbitron', monospace;
    font-size: 1rem;
    letter-spacing: 3px;
    margin-bottom: 8px;
}
.footer-tagline span:nth-child(1) { color: #00bfff; }
.footer-tagline span:nth-child(2) { color: #a040ff; }
.footer-tagline span:nth-child(3) { color: #ff6b35; }
.footer-copy {
    color: #405060;
    font-size: 0.8rem;
}

/* ── Contact card ── */
.contact-info-card {
    background: #0d1b2a;
    border: 1px solid #1e3a5f;
    border-radius: 12px;
    padding: 24px;
}
.contact-info-card h3 {
    color: #00bfff;
    margin-bottom: 16px;
}
.contact-item {
    color: #d0e0f0;
    margin-bottom: 10px;
    font-size: 0.95rem;
}

/* ── mailto button ── */
.mailto-btn {
    display: inline-block;
    background: linear-gradient(135deg, #00bfff, #0080ff);
    color: white !important;
    padding: 12px 28px;
    border-radius: 8px;
    text-decoration: none;
    font-weight: 600;
    font-size: 0.95rem;
    margin-top: 12px;
    transition: transform 0.2s;
}
.mailto-btn:hover { transform: scale(1.03); color: white !important; }

/* ── Restricted badge ── */
.restricted-badge {
    background: linear-gradient(135deg, #1a0505, #2a0505);
    border: 1px solid #ff333344;
    border-radius: 8px;
    padding: 10px 20px;
    text-align: center;
    color: #ff6666;
    font-weight: 600;
    margin-bottom: 16px;
    letter-spacing: 1px;
}

/* ── Offline banner ── */
.offline-banner {
    background: #0d1b2a;
    border: 1px solid #1e3a5f;
    border-radius: 12px;
    padding: 28px;
    text-align: center;
    color: #607080;
}
</style>
""", unsafe_allow_html=True)

# =============================================================================
# 5. NAVIGATION
# =============================================================================
st.markdown("""
<div class="hero-banner">
    <div class="hero-title">⚗️ THE MOLECULAR MAN EXPERT TUITION SOLUTIONS</div>
    <div class="hero-sub">
        Other Apps Were Coded by Engineers. This One Was Coded by Your Master Tutor — Mohammed Salmaan.<br>
        The only online tuition service in the world running on a proprietary engine built by the Founder.<br>
        <strong style="color:#00bfff;">Pure Teaching Intelligence. Zero Corporate Noise.</strong>
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown("### 🧭 Main Menu")
col1, col2, col3, col4, col5, col6 = st.columns(6)
with col1:
    if st.button("🏠 Home",      use_container_width=True): st.session_state.page = "Home";         st.rerun()
with col2:
    if st.button("📚 Services",  use_container_width=True): st.session_state.page = "Services";     st.rerun()
with col3:
    if st.button("🔴 Live Class",use_container_width=True): st.session_state.page = "Live Class";   st.rerun()
with col4:
    if st.button("💬 Stories",   use_container_width=True): st.session_state.page = "Testimonials"; st.rerun()
with col5:
    if st.button("🐍 Bootcamp",  use_container_width=True): st.session_state.page = "Bootcamp";     st.rerun()
with col6:
    if st.button("📞 Contact",   use_container_width=True): st.session_state.page = "Contact";      st.rerun()

st.write("")
st.markdown("### 🤖 AI Power Tools (Free)")
ai_col1, ai_col2 = st.columns(2)
with ai_col1:
    if st.button("🧠 Chat with AyA (AI Tutor)", use_container_width=True, type="primary"):
        st.session_state.page = "AyA_AI"; st.rerun()
with ai_col2:
    if st.button("📝 Generate Mock Test", use_container_width=True, type="primary"):
        st.session_state.page = "Mock_Test"; st.rerun()

st.divider()

# =============================================================================
# 6. PAGE LOGIC
# =============================================================================

# ──────────────────────────────────────────────────────────────────────────────
# HOME
# ──────────────────────────────────────────────────────────────────────────────
if st.session_state.page == "Home":

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
            st.write("For Classes 6–12 & Competitive Exams (NEET / JEE / Boards)")
            st.write("")
            st.link_button("📱 Book Free Trial", "https://wa.me/917339315376", use_container_width=True)

    st.markdown("""
    <div class="announcement-box">
        <div class="announcement-title">🚨 THE EDUCATION SYSTEM JUST GOT A REALITY CHECK</div>
        <div class="announcement-text">
            Stop paying for "premium" test series. The corporate coaching giants are scared.<br><br>
            <strong style="color:#00bfff;">INTRODUCING: THE MOLECULAR MAN AI SUITE</strong><br><br>
            1. 🧠 <strong>AyA (AI Tutor)</strong> — She doesn't sleep. She solves PDFs &amp; problems instantly.<br>
            2. 📝 <strong>Infinite Mock Tests</strong> — Generate unlimited tests for ANY Board/Subject for ₹0.<br><br>
            <span style="color:#ff6b35;">🚫 NO SUBSCRIPTIONS. NO HIDDEN FEES. PURE TEACHING INTELLIGENCE.</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("## 📊 Our Impact")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown('<div class="metric-card"><div class="metric-value">500+</div><div class="metric-label">Students Taught</div></div>', unsafe_allow_html=True)
    with m2:
        st.markdown('<div class="metric-card"><div class="metric-value">100%</div><div class="metric-label">Success Rate</div></div>', unsafe_allow_html=True)
    with m3:
        st.markdown('<div class="metric-card"><div class="metric-value">24/7</div><div class="metric-label">Support</div></div>', unsafe_allow_html=True)
    with m4:
        st.markdown('<div class="metric-card"><div class="metric-value">5+ Yrs</div><div class="metric-label">Experience</div></div>', unsafe_allow_html=True)

    st.markdown("## 🎯 What We Offer")
    s1, s2, s3 = st.columns(3)
    with s1:
        with st.container(border=True):
            st.markdown("#### 👨‍🏫 Expert Tutoring")
            st.write("One-on-one and small group classes for Classes 6–12.")
    with s2:
        with st.container(border=True):
            st.markdown("#### 📚 Comprehensive Material")
            st.write("Access to curated notes, practice problems, and revision guides.")
    with s3:
        with st.container(border=True):
            st.markdown("#### 🐍 Python Bootcamp")
            st.write("Weekend intensive courses in Data Science & AI.")

    st.markdown("## 🤖 AI Power Tools")
    af1, af2 = st.columns(2)
    with af1:
        st.markdown("""
        <div class="ai-feature">
            <div class="ai-feature-title">🧠 AyA — AI Tutor</div>
            <div class="ai-feature-text">
                Powered by Llama 3.3 70B via Groq. Solves any subject question,
                explains step-by-step, and generates visual diagrams on demand.
                Available 24/7 — completely free.
            </div>
        </div>
        """, unsafe_allow_html=True)
    with af2:
        st.markdown("""
        <div class="ai-feature">
            <div class="ai-feature-title">📝 Mock Test Generator</div>
            <div class="ai-feature-text">
                Generate unlimited MCQ or Descriptive tests for any Board, Class,
                Subject, and Chapter. Auto-graded with detailed feedback. Zero cost.
            </div>
        </div>
        """, unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# AyA AI TUTOR
# ──────────────────────────────────────────────────────────────────────────────
elif st.session_state.page == "AyA_AI":

    st.markdown("## 🧠 AyA — The Molecular Man AI Tutor")
    st.caption("Powered by Llama 3.3 70B · Solves Math, Science, Coding · Generates Visual Diagrams")

    try:
        groq_api_key = st.secrets["GROQ_API_KEY"]
        groq_client  = Groq(api_key=groq_api_key)
    except Exception:
        st.error("⚠️ GROQ_API_KEY not found in Secrets! Please check your .streamlit/secrets.toml file.")
        st.stop()

    SYSTEM_PROMPT = """You are **AyA**, the Lead AI Tutor at **The Molecular Man Expert Tuition Solutions**.

Your Mission: Guide students from "Zero" to "Hero".
Tone: Encouraging, clear, patient, and intellectually rigorous.

CRITICAL INSTRUCTION FOR IMAGES:
If a student asks for a visual, diagram, or image, output a short description inside square brackets
starting with "IMAGE:". Format EXACTLY like this:
[IMAGE: A simple 2D diagram of a glass slab showing lateral displacement of light]

RULES for image tags:
- Keep the description under 100 characters.
- Do NOT use markdown image syntax like ![alt](url).
- Do NOT output any URLs.
- Only use the [IMAGE: description] format.
"""

    # Input panel
    with st.expander("📝 New Problem Input", expanded=(len(st.session_state.aya_messages) == 0)):
        input_type = st.radio("Input Method:", ["📄 Text Problem", "📕 Upload PDF"], horizontal=True)

        if input_type == "📄 Text Problem":
            user_text = st.text_area("Paste your question:", height=100, placeholder="e.g., Explain Newton's Second Law with an example.")
            if st.button("Ask AyA 🚀", use_container_width=True):
                if user_text.strip():
                    st.session_state.aya_messages = [{"role": "user", "content": f"PROBLEM:\n{user_text}"}]
                    st.rerun()
                else:
                    st.warning("Please enter a question first.")

        else:  # PDF
            uploaded_file = st.file_uploader("Upload PDF", type=["pdf"])
            if st.button("Analyze PDF 🚀", use_container_width=True):
                if uploaded_file:
                    try:
                        reader   = PyPDF2.PdfReader(uploaded_file)
                        pdf_text = ""
                        for page_num in range(min(2, len(reader.pages))):
                            pdf_text += reader.pages[page_num].extract_text()[:3000]
                        st.session_state.aya_messages = [{"role": "user", "content": f"PROBLEM from PDF:\n{pdf_text}"}]
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error reading PDF: {e}")
                else:
                    st.warning("Please upload a PDF first.")

    # Chat history
    for msg in st.session_state.aya_messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                render_chat_content(msg["content"])
            else:
                st.markdown(msg["content"])

    # Generate response if last message is from user
    if st.session_state.aya_messages and st.session_state.aya_messages[-1]["role"] == "user":
        with st.chat_message("assistant"):
            with st.spinner("🤖 AyA is thinking..."):
                try:
                    msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + st.session_state.aya_messages
                    completion   = groq_client.chat.completions.create(
                        messages=msgs,
                        model="llama-3.3-70b-versatile",
                        temperature=0.5,
                    )
                    response_text = completion.choices[0].message.content or ""
                    render_chat_content(response_text)
                    st.session_state.aya_messages.append({"role": "assistant", "content": response_text})
                except Exception as e:
                    st.error(f"⚠️ Groq API Error: {e}")

    # Follow-up input
    if st.session_state.aya_messages:
        if user_input := st.chat_input("Ask a follow-up..."):
            st.session_state.aya_messages.append({"role": "user", "content": user_input})
            st.rerun()

    # Clear chat
    if st.session_state.aya_messages:
        if st.button("🗑️ Clear Chat", use_container_width=True):
            st.session_state.aya_messages = []
            st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
# MOCK TEST
# ──────────────────────────────────────────────────────────────────────────────
elif st.session_state.page == "Mock_Test":

    st.markdown("## 📝 AI Mock Test Generator")
    st.caption("Generate unlimited tests for any Board, Subject, or Difficulty — completely free.")

    api_key = st.secrets.get("GROQ_API_KEY")
    if not api_key:
        st.error("Missing GROQ_API_KEY in secrets."); st.stop()

    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

    def get_questions_json(board, cls, sub, chap, num, diff, q_type):
        safe_sub = sub.encode("ascii", "ignore").decode("ascii").strip()
        if q_type == "MCQ":
            prompt = (
                f"You are a strict Examiner for {board} Board. "
                f"Subject: {safe_sub}, Class: {cls}, Chapter: {chap}. "
                f"Create a valid JSON list of {num} {diff} MCQs. "
                'Format: [{"id":1,"question":"...","options":["A","B","C","D"],"correct_answer":"A"}]'
            )
        else:
            prompt = (
                f"You are a strict Examiner for {board} Board. "
                f"Subject: {safe_sub}, Class: {cls}, Chapter: {chap}. "
                f"Create a valid JSON list of {num} {diff} Descriptive Questions with marks. "
                'Format: [{"id":1,"question":"...","marks":5}]'
            )
        try:
            res     = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt + " Return ONLY JSON, nothing else."}],
                temperature=0.1,
            )
            content = res.choices[0].message.content
            content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except Exception as e:
            st.error(f"Question generation error: {e}")
            return None

    # ── Setup form ────────────────────────────────────────────────────────────
    if not st.session_state.mt_questions:
        with st.container(border=True):
            c1, c2 = st.columns(2)
            with c1:
                board = st.selectbox("Board",      ["CBSE", "ICSE", "State", "Other"])
                cls   = st.selectbox("Class",      ["9", "10", "11", "12"])
                diff  = st.selectbox("Difficulty", ["Easy", "Medium", "Hard"])
            with c2:
                sub   = st.text_input("Subject", "Physics")
                chap  = st.text_input("Chapter", "Thermodynamics")
                q_type = st.radio("Format", ["MCQ", "Descriptive"], horizontal=True)
            num = st.slider("Number of Questions", 3, 20, 5)

            if st.button("🚀 Generate Test", type="primary", use_container_width=True):
                if sub.strip() and chap.strip():
                    with st.spinner("📄 Generating question paper..."):
                        st.session_state.mt_q_type   = q_type
                        st.session_state.mt_questions = get_questions_json(board, cls, sub, chap, num, diff, q_type)
                        st.session_state.mt_answers   = {}
                        st.session_state.mt_feedback  = None
                    if st.session_state.mt_questions:
                        st.rerun()
                else:
                    st.warning("Please enter Subject and Chapter.")

    # ── Results view ──────────────────────────────────────────────────────────
    elif st.session_state.mt_feedback:
        st.success("✅ Test Analysis Complete")
        st.markdown(st.session_state.mt_feedback)
        if st.button("🔄 Start New Test", use_container_width=True):
            st.session_state.mt_questions = None
            st.session_state.mt_feedback  = None
            st.rerun()

    # ── Test form ─────────────────────────────────────────────────────────────
    else:
        st.markdown(f"**Format:** {st.session_state.mt_q_type} &nbsp;|&nbsp; **Questions:** {len(st.session_state.mt_questions)}")
        st.divider()

        with st.form("mock_test_form"):
            for q in st.session_state.mt_questions:
                st.markdown(f"**Q{q['id']}. {q['question']}**")
                if st.session_state.mt_q_type == "MCQ":
                    st.radio(
                        "Choose:",
                        q["options"],
                        key=f"q_{q['id']}",
                        label_visibility="collapsed",
                        index=None,
                    )
                else:
                    marks = q.get("marks", "")
                    st.caption(f"Marks: {marks}")
                    st.text_area("Your Answer:", key=f"q_{q['id']}", height=80)
                st.markdown("---")

            submitted = st.form_submit_button("✅ Submit Exam", use_container_width=True)

        if submitted:
            answers      = {}
            all_answered = True
            for q in st.session_state.mt_questions:
                val = st.session_state.get(f"q_{q['id']}")
                if not val:
                    all_answered = False
                answers[str(q["id"])] = val

            if not all_answered and st.session_state.mt_q_type == "MCQ":
                st.error("⚠️ Please answer all questions before submitting.")
            else:
                grade_prompt = (
                    f"Grade this student's test paper. "
                    f"Questions: {json.dumps(st.session_state.mt_questions)}. "
                    f"Student Answers: {json.dumps(answers)}. "
                    "Provide a detailed analysis with score, question-by-question feedback, "
                    "and study recommendations."
                )
                with st.spinner("📊 Grading your paper..."):
                    res = client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[{"role": "user", "content": grade_prompt}],
                        temperature=0.3,
                    )
                    st.session_state.mt_feedback = res.choices[0].message.content
                st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
# LIVE CLASS
# ──────────────────────────────────────────────────────────────────────────────
elif st.session_state.page == "Live Class":

    st.markdown("# 🔴 Molecular Man Live Classroom")

    # ── Login gate ────────────────────────────────────────────────────────────
    if not st.session_state.logged_in:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown('<div class="restricted-badge">🔒 RESTRICTED ACCESS — ENROLLED STUDENTS ONLY</div>', unsafe_allow_html=True)
            with st.container(border=True):
                username = st.text_input("👤 Username")
                password = st.text_input("🔐 Password", type="password")
                if st.button("Login to Classroom 🚀", use_container_width=True, type="primary"):
                    if login_user(username, password):
                        st.session_state.logged_in = True
                        st.session_state.username  = username
                        st.session_state.is_admin  = (username.strip() == "Mohammed")
                        st.rerun()
                    else:
                        st.error("❌ Invalid Credentials. Please try again.")
    else:
        # ── Logged-in header ──────────────────────────────────────────────────
        hc1, hc2 = st.columns([3, 1])
        with hc1:
            st.write(f"👋 Logged in as: **{st.session_state.username}**")
        with hc2:
            if st.button("Logout", use_container_width=True):
                st.session_state.logged_in = False
                st.session_state.username  = "Student"
                st.rerun()

        st.divider()

        # ── ADMIN view ────────────────────────────────────────────────────────
        if st.session_state.is_admin:
            st.markdown("## 👨‍🏫 Teacher Command Center")
            a1, a2 = st.columns([2, 1])

            with a1:
                st.markdown("### 🔴 Live Class Controls")
                status = get_live_status()
                with st.container(border=True):
                    if status["is_live"]:
                        raw_link    = status["link"].strip()
                        final_link  = raw_link if raw_link.startswith("http") else "https://" + raw_link
                        st.success(f"✅ Class is LIVE: **{status['topic']}**")
                        st.markdown(f"**Link:** {final_link}")
                        st.markdown(f'<a href="{final_link}" target="_blank" class="join-btn">🎥 Enter Meeting</a>', unsafe_allow_html=True)
                        st.write("")
                        if st.button("⏹️ End Class", type="primary", use_container_width=True):
                            set_live_status(False)
                            st.rerun()
                    else:
                        st.info("No active class. Start a new session below.")
                        with st.form("start_live"):
                            topic     = st.text_input("Class Topic",    placeholder="e.g., Thermodynamics Part 2")
                            meet_link = st.text_input("Meeting Link",   placeholder="Paste Google Meet / Teams / Zoom link")
                            if st.form_submit_button("📡 Go Live", use_container_width=True):
                                if topic and meet_link:
                                    if not meet_link.startswith("http"):
                                        meet_link = "https://" + meet_link
                                    set_live_status(True, topic, meet_link)
                                    add_notification(f"🔴 Live Class Started: {topic}. Join now!")
                                    st.rerun()
                                else:
                                    st.warning("Please enter both Topic and Meeting Link.")

            with a2:
                st.markdown("### 📢 Send Notification")
                with st.form("notif_form"):
                    msg = st.text_area("Announcement Message", height=100)
                    if st.form_submit_button("Send Blast 🚀", use_container_width=True):
                        if msg.strip():
                            add_notification(msg)
                            st.success("✅ Notification sent to all students!")
                        else:
                            st.warning("Please enter a message.")

                st.markdown("### 📜 Recent Announcements")
                with st.container(border=True):
                    for n in get_notifications()[:5]:
                        st.markdown(
                            f'<div class="notif-card">'
                            f'<div class="notif-date">📅 {n["date"]}</div>'
                            f'<div class="notif-msg">{n["message"]}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

        # ── STUDENT view ──────────────────────────────────────────────────────
        else:
            status = get_live_status()
            if status["is_live"]:
                raw_link   = status["link"].strip()
                final_link = raw_link if raw_link.startswith("http") else "https://" + raw_link
                st.markdown(f"""
                <div class="live-banner">
                    <div class="live-title">
                        <span class="live-dot"></span>LIVE NOW
                    </div>
                    <div class="live-topic">Topic: <strong>{status['topic']}</strong></div>
                    <a href="{final_link}" target="_blank" class="join-btn">👉 CLICK TO JOIN CLASS</a>
                    <div style="margin-top:12px;color:#607080;font-size:0.8rem;">Opens Google Meet / Teams in a new tab</div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown("""
                <div class="offline-banner">
                    <h3>💤 No Live Class Right Now</h3>
                    <p>Check the Notice Board below for the schedule.</p>
                </div>
                """, unsafe_allow_html=True)

            st.write("")
            st.markdown("### 🔔 Notice Board")
            notifs = get_notifications()
            if notifs:
                for n in notifs:
                    st.markdown(
                        f'<div class="notif-card">'
                        f'<div class="notif-date">📅 {n["date"]}</div>'
                        f'<div class="notif-msg">{n["message"]}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.info("No announcements yet.")


# ──────────────────────────────────────────────────────────────────────────────
# SERVICES
# ──────────────────────────────────────────────────────────────────────────────
elif st.session_state.page == "Services":

    st.markdown("# 📚 Our Services")
    st.markdown("## 🎓 Subjects We Teach")

    sub1, sub2 = st.columns(2)
    with sub1:
        with st.container(border=True):
            st.markdown("### 📐 Mathematics")
            st.write("Classes 6–12 (CBSE / State / Commerce / Science)")
        with st.container(border=True):
            st.markdown("### ⚗️ Chemistry")
            st.write("NEET / JEE Chemistry — Organic, Inorganic & Physical")
    with sub2:
        with st.container(border=True):
            st.markdown("### ⚡ Physics")
            st.write("Conceptual clarity & Numerical problem solving")
        with st.container(border=True):
            st.markdown("### 🧬 Biology")
            st.write("Botany, Zoology & NEET Preparation")

    st.markdown("## 🏆 Exam Coverage")
    e1, e2, e3 = st.columns(3)
    with e1:
        with st.container(border=True):
            st.markdown("### 📋 Board Exams")
            st.write("CBSE, ICSE, State Board for Classes 9–12")
    with e2:
        with st.container(border=True):
            st.markdown("### 🔬 NEET")
            st.write("Physics, Chemistry, Biology — Full syllabus coverage")
    with e3:
        with st.container(border=True):
            st.markdown("### ⚙️ JEE")
            st.write("Maths, Physics, Chemistry — Mains & Advanced")

    st.markdown("## 💡 Our Teaching Philosophy")
    with st.container(border=True):
        st.write("""
        We believe every student is capable of excellence when given the right guidance.
        Our approach focuses on deep conceptual understanding rather than rote memorization,
        building problem-solving skills that last a lifetime.
        """)
        st.write("**👨‍🏫 Instructor:** Mohammed Salmaan M — 5+ years of expert tutoring experience")

    st.write("")
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.link_button("📱 Book Free Trial", "https://wa.me/917339315376", use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────────
# TESTIMONIALS
# ──────────────────────────────────────────────────────────────────────────────
elif st.session_state.page == "Testimonials":

    st.markdown("# 💬 Student Success Stories")

    t1, t2 = st.columns(2)

    def testimonial_card(text, author):
        st.markdown(f"""
        <div class="testimonial-card">
            <div class="testimonial-text">{text}</div>
            <div class="testimonial-author">— {author}</div>
        </div>
        """, unsafe_allow_html=True)

    with t1:
        testimonial_card("Sir's organic chemistry teaching helped me score 95+ in Boards. The way he breaks down reactions is incredible.", "Pranav S., Class 12")
        testimonial_card("My son's Math grades improved from 60% to 95% in just two months. Highly recommend.", "Mrs. Lakshmi, Parent")
        testimonial_card("AyA AI helped me solve problems at 2 AM before my exam. It felt like having a tutor available 24/7.", "Aisha K., Class 11")
    with t2:
        testimonial_card("Physics numericals used to scare me. Now I solve them confidently. The conceptual approach made all the difference.", "Rahul M., JEE Aspirant")
        testimonial_card("The Python Bootcamp was amazing! I built my first ML model in just 2 weekends.", "Divya S., College Student")
        testimonial_card("The mock test generator is a game-changer. Unlimited practice, zero cost.", "Kiran T., NEET Aspirant")

    st.write("")
    st.markdown("## 🏆 Our Results")
    r1, r2, r3 = st.columns(3)
    with r1:
        st.markdown("""
        <div class="result-card">
            <div class="result-title">Board Exams</div>
            <div class="result-number">80%</div>
            <div class="result-label">Average Score</div>
        </div>
        """, unsafe_allow_html=True)
    with r2:
        st.markdown("""
        <div class="result-card">
            <div class="result-title">Score Improvement</div>
            <div class="result-number">60%</div>
            <div class="result-label">vs. Baseline</div>
        </div>
        """, unsafe_allow_html=True)
    with r3:
        st.markdown("""
        <div class="result-card">
            <div class="result-title">Doubt Support</div>
            <div class="result-number">&lt; 2 Hrs</div>
            <div class="result-label">Resolution Time</div>
        </div>
        """, unsafe_allow_html=True)

    st.write("")
    st.markdown("## 💡 Why Parents Trust Us")
    w1, w2, w3 = st.columns(3)
    with w1:
        st.markdown("""
        <div class="trust-card">
            <h4>🎓 Expert Educator</h4>
            <p>One-on-one mentoring that identifies and closes specific learning gaps.</p>
        </div>
        """, unsafe_allow_html=True)
    with w2:
        st.markdown("""
        <div class="trust-card">
            <h4>🧠 Conceptual Focus</h4>
            <p>No rote memorization. We focus on "Why" and "How" so concepts stick forever.</p>
        </div>
        """, unsafe_allow_html=True)
    with w3:
        st.markdown("""
        <div class="trust-card">
            <h4>💰 Fair Pricing</h4>
            <p>No hidden fees. Quality education that's accessible to every family.</p>
        </div>
        """, unsafe_allow_html=True)

    st.write("")
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.link_button("📱 Book Free Trial", "https://wa.me/917339315376", use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────────
# BOOTCAMP
# ──────────────────────────────────────────────────────────────────────────────
elif st.session_state.page == "Bootcamp":

    st.markdown("# 🐍 Python for Data Science & AI")
    st.markdown("### Weekend Intensive Bootcamp")

    boot1, boot2 = st.columns([1, 1.5])
    with boot1:
        if not render_image("poster", use_column_width=True):
            if not render_image("python_bootcamp", use_column_width=True):
                with st.container(border=True):
                    st.markdown("""
                    <div style="text-align:center;padding:40px;">
                        <div style="font-size:5rem;">🐍</div>
                        <div style="font-family:'Orbitron',monospace;color:#00bfff;font-size:1.2rem;">PYTHON BOOTCAMP</div>
                        <div style="color:#a0b0c0;margin-top:8px;">Weekend Intensive Program</div>
                    </div>
                    """, unsafe_allow_html=True)

    with boot2:
        with st.container(border=True):
            st.markdown("### Weekend Intensive Program")
            st.write("Master the most in-demand programming language in the world.")
            st.write("")
            st.markdown("👨‍🏫 **Instructor:** Mohammed Salmaan M")
            st.caption("Data Science & AI Expert | Founder, The Molecular Man Expert Tuition Solutions")
            st.write("")
            st.markdown("📅 **Schedule:** Saturdays & Sundays")
            st.caption("1 hour per session | Morning & Evening batches available")
            st.write("")
            st.markdown("💻 **Requirements:** Laptop with internet connection")
            st.caption("We'll help you set up Jupyter Notebook & VS Code from scratch")
            st.write("")
            with st.expander("📚 Curriculum Highlights"):
                st.write("• Python Basics & Data Structures")
                st.write("• NumPy & Pandas for Data Analysis")
                st.write("• Data Visualization with Matplotlib & Seaborn")
                st.write("• Introduction to Machine Learning with Scikit-learn")
                st.write("• Real-world Project: Build your first AI model")
            st.write("")
            st.link_button("📱 Enroll Now via WhatsApp", "https://wa.me/917339315376", use_container_width=True)

    st.write("")
    st.markdown("## 🎯 What You'll Gain")
    g1, g2, g3 = st.columns(3)
    with g1:
        with st.container(border=True):
            st.markdown("#### 💼 Career Ready")
            st.write("Python is the #1 skill for Data Science, AI, and software roles.")
    with g2:
        with st.container(border=True):
            st.markdown("#### 🧪 Hands-On Projects")
            st.write("Build real projects you can add to your portfolio from day one.")
    with g3:
        with st.container(border=True):
            st.markdown("#### 🎓 Certificate")
            st.write("Receive a completion certificate from The Molecular Man.")


# ──────────────────────────────────────────────────────────────────────────────
# CONTACT
# ──────────────────────────────────────────────────────────────────────────────
elif st.session_state.page == "Contact":

    st.markdown("# 📞 Get In Touch")

    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("""
        <div class="contact-info-card">
            <h3>📋 Contact Information</h3>
            <div class="contact-item">📱 <strong>Phone:</strong> +91 73393 15376</div>
            <div class="contact-item">✉️ <strong>Email:</strong> the.molecularmanexpert@gmail.com</div>
            <div class="contact-item">📍 <strong>Location:</strong> Madurai, Tamil Nadu</div>
            <div class="contact-item">🕐 <strong>Hours:</strong> Mon–Sat, 9 AM – 9 PM IST</div>
        </div>
        """, unsafe_allow_html=True)
        st.write("")
        st.link_button("💬 Chat on WhatsApp", "https://wa.me/917339315376", use_container_width=True)
        st.write("")
        st.markdown("### 📱 Follow Us")
        st.write("Stay updated with latest classes, tips, and announcements.")

    with c2:
        with st.container(border=True):
            st.markdown("### ✉️ Send us a Message")
            name  = st.text_input("Full Name")
            phone = st.text_input("Phone Number")
            grade = st.selectbox("Grade / Level", ["Class 6–8", "Class 9–10", "Class 11–12", "Repeater / Other", "Python Bootcamp"])
            msg   = st.text_area("Your Message", height=120)

            if name.strip() and phone.strip() and msg.strip():
                subject    = f"Tuition Inquiry from {name}"
                body       = f"Name: {name}\nPhone: {phone}\nGrade: {grade}\n\nMessage:\n{msg}"
                safe_sub   = urllib.parse.quote(subject)
                safe_body  = urllib.parse.quote(body)
                mailto     = f"mailto:the.molecularmanexpert@gmail.com?subject={safe_sub}&body={safe_body}"
                st.markdown(f'<a href="{mailto}" class="mailto-btn">🚀 Send Email</a>', unsafe_allow_html=True)
                st.caption("Opens your default email app (Gmail, Outlook, etc.)")
            else:
                st.markdown("""
                <div style="background:#0d1b2a;border:1px dashed #2a3a5f;border-radius:8px;
                            padding:16px;text-align:center;color:#405060;margin-top:8px;">
                    Fill in all fields to enable the Send button
                </div>
                """, unsafe_allow_html=True)


# =============================================================================
# 7. FOOTER
# =============================================================================
st.write("")
st.write("")
st.markdown("""
<div class="footer-box">
    <div class="footer-tagline">
        <span>PRECISE</span>
        <span style="color:#607080;"> • </span>
        <span>PASSIONATE</span>
        <span style="color:#607080;"> • </span>
        <span>PROFESSIONAL</span>
    </div>
    <div class="footer-copy">
        © 2026 The Molecular Man Expert Tuition Solutions | Mohammed Salmaan M. All Rights Reserved.
    </div>
</div>
""", unsafe_allow_html=True)
