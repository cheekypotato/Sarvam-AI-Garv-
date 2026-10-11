"""
Bahi-Khata Bolo: Voice-First Credit Ledger for Kirana Owners
Powered by Sarvam AI:
  - Document AI (Vision) -> Handwriting to structured table
  - Saaras v3 STT -> Spoken questions to text (multilingual + code-mixed)
  - Sarvam-105B Chat -> Intent parsing & customer extraction
  - Bulbul v3 TTS -> Spoken voice answers
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from src.answers import generate_answer
from src.config import Settings, load_settings
from src.db import (
    balance_of,
    customer_display_names,
    entries_of,
    get_connection,
    insert_rows,
    list_customers,
    outstanding_total,
    top_debtors,
)
from src.intents import parse_intent
from src.ledger_extract import LedgerRow, extract_ledger
from src.reminders import generate_reminder
from src.sarvam_client import SarvamError
from src.voice import speech_to_text, text_to_speech

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bahi_khata_app")

# ---------------------------------------------------------------------------
# Streamlit Page Config & Custom Styling
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="बही-खाता बोलो | Bahi-Khata Bolo",
    page_icon="📖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for clean typography, cards, badges, and layout
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Noto+Sans+Devanagari:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Outfit', 'Noto Sans Devanagari', sans-serif;
    }

    .main-header {
        background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 50%, #f97316 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.5rem;
        font-weight: 800;
        margin-bottom: 0.2rem;
    }

    .sub-header {
        color: #4b5563;
        font-size: 1.05rem;
        font-weight: 500;
        margin-bottom: 1.5rem;
    }

    .metric-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 16px;
        text-align: center;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #1e3a8a;
    }
    .metric-label {
        font-size: 0.85rem;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-top: 4px;
    }

    .audit-badge {
        display: inline-block;
        background-color: #ecfdf5;
        color: #047857;
        border: 1px solid #a7f3d0;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.82rem;
        font-weight: 600;
        margin: 8px 0;
    }

    .info-pill {
        display: inline-block;
        background-color: #eff6ff;
        color: #1d4ed8;
        border: 1px solid #bfdbfe;
        padding: 3px 8px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 500;
        margin-right: 6px;
    }

    .answer-box {
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        border-radius: 10px;
        padding: 16px;
        margin: 12px 0;
        font-size: 1.15rem;
        color: #166534;
        font-weight: 600;
    }

    .reminder-box {
        background: #fffbeb;
        border: 1px solid #fde68a;
        border-radius: 10px;
        padding: 14px;
        margin: 12px 0;
    }

    .wa-button {
        display: inline-block;
        background-color: #25D366;
        color: white !important;
        font-weight: 600;
        padding: 8px 16px;
        border-radius: 8px;
        text-decoration: none;
        box-shadow: 0 2px 4px rgba(37,211,102,0.3);
        margin-top: 8px;
    }
    .wa-button:hover {
        background-color: #1ebd5b;
        color: white !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# App State & Settings initialization
# ---------------------------------------------------------------------------
@st.cache_resource
def get_app_settings() -> Settings:
    return load_settings()


settings = get_app_settings()
conn = get_connection(settings.db_path)

if "extracted_rows" not in st.session_state:
    st.session_state.extracted_rows = []
if "last_image_path" not in st.session_state:
    st.session_state.last_image_path = None
if "last_transcript" not in st.session_state:
    st.session_state.last_transcript = ""
if "last_language" not in st.session_state:
    st.session_state.last_language = "hi-IN"
if "last_intent" not in st.session_state:
    st.session_state.last_intent = None
if "last_answer" not in st.session_state:
    st.session_state.last_answer = None
if "last_tts_audio" not in st.session_state:
    st.session_state.last_tts_audio = None
if "debug_logs" not in st.session_state:
    st.session_state.debug_logs = []


def add_debug_log(title: str, data: any):
    st.session_state.debug_logs.insert(0, {
        "time": time.strftime("%H:%M:%S"),
        "title": title,
        "data": data,
    })


# ---------------------------------------------------------------------------
# Sidebar: Branding, Settings, & Fast Sample Actions
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 🏪 बही-खाता बोलो")
    st.caption("Sarvam AI Hackathon • Kirana Credit Intelligence")

    shop_name = st.text_input("दुकान का नाम (Shop Name)", value="गुप्ता किराना स्टोर")

    st.markdown("---")
    demo_mode_toggle = st.toggle(
        "⚡ DEMO_MODE (Offline Fixtures)",
        value=settings.demo_mode,
        help="When enabled, uses fast local cached fixtures for Sarvam Vision instead of consuming API credits.",
    )
    if demo_mode_toggle != settings.demo_mode:
        settings = Settings(
            **{k: getattr(settings, k) for k in settings.__dataclass_fields__ if k != "demo_mode"},
            demo_mode=demo_mode_toggle,
        )

    st.markdown("---")
    st.markdown("#### 📊 खाता संक्षेप (Summary)")
    total_out = outstanding_total(conn)
    customers = list_customers(conn)
    col_sb1, col_sb2 = st.columns(2)
    col_sb1.metric("कुल बकाया", f"₹{total_out:,.0f}")
    col_sb2.metric("कुल ग्राहक", len(customers))

    st.markdown("---")
    st.markdown("#### ⚡ त्वरित क्रियाएं (Quick Actions)")

    if st.button("📥 लोड करें सैंपल डेटा (Sample Data)", use_container_width=True):
        sample_fixture = Path(settings.fixtures_dir) / "sample_ledger_extract.json"
        if sample_fixture.exists():
            data = json.loads(sample_fixture.read_text())
            from src.ledger_extract import _parse_rows
            parsed = _parse_rows(data)
            dict_rows = [
                {
                    "customer_name": r.customer_name,
                    "date": r.date,
                    "description": r.description,
                    "amount": r.amount,
                    "entry_type": r.entry_type,
                    "needs_review": r.needs_review,
                    "review_reason": r.review_reason,
                }
                for r in parsed
            ]
            count = insert_rows(conn, dict_rows, source_image="fixtures/sample_ledger.png")
            st.success(f"✅ {count} सैंपल लेन-देन डेटाबेस में जोड़े गए!")
            st.rerun()
        else:
            st.error("सैंपल फ़ाइल नहीं मिली।")

    if st.button("🗑️ साफ़ करें डेटाबेस (Reset DB)", use_container_width=True):
        conn.execute("DELETE FROM ledger_entries")
        conn.commit()
        st.session_state.extracted_rows = []
        st.session_state.last_intent = None
        st.session_state.last_answer = None
        st.warning("डेटाबेस खाली कर दिया गया।")
        st.rerun()

    with st.expander("🛠️ डिबग और सिस्टम स्थिति (Debug Panel)", expanded=False):
        st.write(f"**Chat Model:** `{settings.chat_model}`")
        st.write(f"**STT Model:** `{settings.stt_model}`")
        st.write(f"**TTS Model:** `{settings.tts_model}` (`{settings.tts_speaker}`)")
        st.write(f"**Demo Mode:** `{settings.demo_mode}`")
        if st.session_state.debug_logs:
            for log in st.session_state.debug_logs[:5]:
                st.markdown(f"**[{log['time']}] {log['title']}**")
                st.json(log["data"])


# ---------------------------------------------------------------------------
# Main Header
# ---------------------------------------------------------------------------
st.markdown('<div class="main-header">📖 बही-खाता बोलो (Bahi-Khata Bolo)</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">'
    'हाथ से लिखे बही-खाते की फोटो खींचे ➔ आवाज़ में पूछें ➔ आवाज़ में सही हिसाब पाएं '
    '• <b>गणित सिर्फ कोड से, LLM से नहीं</b>'
    '</div>',
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Tabs: 1. Photo -> Table | 2. Voice Query | 3. Customer Ledger
# ---------------------------------------------------------------------------
tab_scan, tab_voice, tab_ledger = st.tabs([
    "📸 1. फोटो से बही-खाता (Scan & Edit)",
    "🎙️ 2. बोलकर पूछें (Voice Assistant)",
    "📒 3. खाता बही (Customer Balances)",
])


# ===========================================================================
# TAB 1: Photo -> Table (Sarvam Document AI / Vision)
# ===========================================================================
with tab_scan:
    st.markdown("### 📸 बही-खाते की तस्वीर अपलोड करें")
    st.caption("Sarvam Vision handwriting OCR converts paper notebook pages into a clean, editable table.")

    col_up1, col_up2 = st.columns([1, 1])

    with col_up1:
        upload_choice = st.radio(
            "फोटो का माध्यम चुनें:",
            ["सैंपल बही-खाता (Use Realistic Sample)", "कंप्यूटर से अपलोड करें (File Upload)", "कैमरे से फोटो लें (Camera)"],
            horizontal=True,
        )

        image_path_to_process = None

        if upload_choice == "सैंपल बही-खाता (Use Realistic Sample)":
            sample_img = Path(settings.fixtures_dir) / "sample_ledger.png"
            if sample_img.exists():
                st.image(str(sample_img), caption="Realistic Kirana Bahi-Khata Sample Page", use_container_width=True)
                image_path_to_process = str(sample_img)
            else:
                st.info("सैंपल इमेज बनाई जा रही है...")

        elif upload_choice == "कंप्यूटर से अपलोड करें (File Upload)":
            uploaded_file = st.file_uploader("बही-खाते का फोटो चुनें (PNG, JPG, JPEG)", type=["png", "jpg", "jpeg"])
            if uploaded_file is not None:
                # Save to temporary file
                tfile = tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded_file.name).suffix)
                tfile.write(uploaded_file.read())
                tfile.close()
                st.image(tfile.name, caption="Uploaded Ledger Photo", use_container_width=True)
                image_path_to_process = tfile.name

        elif upload_choice == "कैमरे से फोटो लें (Camera)":
            cam_file = st.camera_input("खाते की तस्वीर लें")
            if cam_file is not None:
                tfile = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tfile.write(cam_file.read())
                tfile.close()
                st.image(tfile.name, caption="Captured Ledger Photo", use_container_width=True)
                image_path_to_process = tfile.name

        read_btn = st.button("🔍 बही-खाता पढ़ें (Read Ledger with Sarvam AI)", type="primary", use_container_width=True)

    with col_up2:
        if read_btn:
            if not image_path_to_process:
                st.warning("कृपया पहले फोटो अपलोड करें या सैंपल चुनें।")
            else:
                st.session_state.last_image_path = image_path_to_process
                with st.spinner("Sarvam Document AI से लिखावट पढ़ी जा रही है..."):
                    try:
                        status_placeholder = st.empty()

                        def on_status_update(st_dict):
                            status_placeholder.info(f"⏳ Document AI स्थिति: {st_dict.get('status', 'processing')}...")

                        start_t = time.time()
                        rows = extract_ledger(
                            image_path_to_process,
                            settings,
                            on_status=on_status_update,
                        )
                        elapsed = time.time() - start_t
                        status_placeholder.success(f"✅ बही-खाता सफलतापूर्वक पढ़ा गया! ({elapsed:.1f}s)")

                        add_debug_log("Extract Ledger Result", {
                            "image": image_path_to_process,
                            "row_count": len(rows),
                            "elapsed_seconds": round(elapsed, 2),
                        })

                        # Convert rows to dicts for st.data_editor
                        st.session_state.extracted_rows = [
                            {
                                "date": r.date,
                                "customer_name": r.customer_name,
                                "description": r.description,
                                "amount": r.amount if r.amount is not None else 0.0,
                                "entry_type": r.entry_type,
                                "needs_review": r.needs_review,
                                "review_reason": r.review_reason,
                            }
                            for r in rows
                        ]
                    except SarvamError as exc:
                        st.error(f"❌ Sarvam AI त्रुटि: {exc}")
                    except Exception as exc:
                        st.error(f"❌ त्रुटि: {exc}")

        if st.session_state.extracted_rows:
            st.markdown("#### ✏️ पहचानी गई प्रविष्टियां (Review & Edit Rows)")
            st.caption("जांच लें और यदि कोई गलती हो तो सीधे टेबल में सुधारें:")

            # Check if any row has needs_review
            has_reviews = any(r.get("needs_review") for r in st.session_state.extracted_rows)
            if has_reviews:
                st.warning("⚠️ कुछ प्रविष्टियों में समीक्षा की आवश्यकता है (needs_review = True)। कृपया जांचें।")

            df = pd.DataFrame(st.session_state.extracted_rows)

            edited_df = st.data_editor(
                df,
                column_config={
                    "date": st.column_config.TextColumn("तारीख (Date)", required=True),
                    "customer_name": st.column_config.TextColumn("ग्राहक (Customer Name)", required=True),
                    "description": st.column_config.TextColumn("सामान विवरण (Items/Description)"),
                    "amount": st.column_config.NumberColumn("रुपये (Amount ₹)", required=True, min_value=0.0, format="₹%.2f"),
                    "entry_type": st.column_config.SelectboxColumn(
                        "प्रकार (Type)",
                        options=["credit_given", "payment_received"],
                        required=True,
                        help="credit_given = उधार दिया; payment_received = ग्राहक ने चुकाया",
                    ),
                    "needs_review": st.column_config.CheckboxColumn("समीक्षा?", default=False),
                    "review_reason": st.column_config.TextColumn("कारण"),
                },
                use_container_width=True,
                num_rows="dynamic",
                key="editor_ledger_table",
            )

            col_sv1, col_sv2 = st.columns(2)
            with col_sv1:
                if st.button("💾 पुष्टि करें और खाते में सेव करें (Save to DB)", type="primary", use_container_width=True):
                    saved_rows = edited_df.to_dict(orient="records")
                    count = insert_rows(conn, saved_rows, source_image=st.session_state.last_image_path or "")
                    st.success(f"🎉 {count} प्रविष्टियां आपके डिजिटल बही-खाते में सुरक्षित कर दी गईं!")
                    add_debug_log("Saved to Database", {"count": count, "rows": saved_rows})
                    time.sleep(1)
                    st.rerun()

            with col_sv2:
                if st.button("🔄 रीसेट करें", use_container_width=True):
                    st.session_state.extracted_rows = []
                    st.rerun()
        else:
            st.info("👈 बाईं तरफ फोटो चुनकर 'बही-खाता पढ़ें' पर क्लिक करें।")


# ===========================================================================
# TAB 2: Voice & Typed Questions (Saaras STT + Sarvam-105B + Bulbul TTS)
# ===========================================================================
with tab_voice:
    st.markdown("### 🎙️ बोलकर पूछें — अपने बही-खाते से सवाल")
    st.caption("Ask in Hindi, English, or code-mixed speech. Sarvam Saaras v3 transcribes, Sarvam-105B extracts intent, SQL computes the balance, and Bulbul v3 speaks the reply.")

    col_q1, col_q2 = st.columns([1, 1])

    with col_q1:
        st.markdown("#### 1. माइक से बोलें (Voice Input)")
        audio_input = st.audio_input("माइक बटन दबाएं और अपना सवाल बोलें (उदा. 'शर्मा जी का कितना बाकी है?')")

        st.markdown("#### या सवाल टाइप करें (Typed Fallback)")
        query_text = st.text_input(
            "लिखकर पूछें:",
            placeholder="उदा. शर्मा जी का कितना बाकी है? / Who owes me the most?",
        )

        st.markdown("##### 💡 उदाहरण सवाल (Click to ask):")
        chip_col1, chip_col2 = st.columns(2)
        preset_query = None
        if chip_col1.button("🗣️ शर्मा जी का कितना बाकी है?", use_container_width=True):
            preset_query = "शर्मा जी का कितना बाकी है?"
        if chip_col2.button("🗣️ सबका कुल कितना उधार है?", use_container_width=True):
            preset_query = "सबका कुल कितना उधार है?"
        if chip_col1.button("🗣️ सबसे ज़्यादा उधार किसका है?", use_container_width=True):
            preset_query = "सबसे ज़्यादा उधार किसका है?"
        if chip_col2.button("🗣️ गुप्ता जी के सौदे दिखाओ", use_container_width=True):
            preset_query = "गुप्ता जी के सभी सौदे दिखाओ"

        active_query = preset_query or query_text
        ask_btn = st.button("🔎 हिसाब निकालें (Get Answer)", type="primary", use_container_width=True)

    with col_q2:
        transcript_to_process = None
        detected_lang = "hi-IN"

        # Handle voice input
        if audio_input is not None:
            audio_bytes = audio_input.read()
            with st.spinner("🎙️ Saaras v3 से आपकी आवाज़ समझी जा रही है..."):
                try:
                    stt_result = speech_to_text(audio_bytes, settings)
                    if stt_result.get("error"):
                        st.error(f"⚠️ {stt_result['error']}")
                    else:
                        transcript_to_process = stt_result.get("transcript", "")
                        detected_lang = stt_result.get("language_code", "hi-IN")
                        st.session_state.last_transcript = transcript_to_process
                        st.session_state.last_language = detected_lang
                        add_debug_log("Saaras STT Result", stt_result)
                except Exception as exc:
                    st.error(f"❌ आवाज़ पहचान में त्रुटि: {exc}")

        elif ask_btn and active_query:
            transcript_to_process = active_query
            detected_lang = "hi-IN"
            st.session_state.last_transcript = transcript_to_process
            st.session_state.last_language = detected_lang

        # Process the query if we have a transcript
        if transcript_to_process:
            st.markdown("#### 2. प्रश्न विश्लेषण (Pipeline Analysis)")
            st.markdown(
                f'<span class="info-pill">📝 सवाल: <b>{transcript_to_process}</b></span>'
                f'<span class="info-pill">🌐 भाषा: <b>{detected_lang}</b></span>',
                unsafe_allow_html=True,
            )

            with st.spinner("🤖 Sarvam-105B से प्रश्न का अर्थ समझा जा रहा है..."):
                known_custs = list_customers(conn)
                intent = parse_intent(transcript_to_process, known_custs, settings)
                st.session_state.last_intent = intent
                add_debug_log("Intent Classifier", {
                    "transcript": transcript_to_process,
                    "intent": intent.intent,
                    "customer_name": intent.customer_name,
                    "customer_key": intent.customer_key,
                })

            st.markdown(
                f'<span class="info-pill">🎯 Intent: <b>{intent.intent}</b></span>'
                + (f'<span class="info-pill">👤 ग्राहक: <b>{intent.customer_name or intent.customer_key}</b></span>' if intent.customer_key else ""),
                unsafe_allow_html=True,
            )

            # Generate Answer strictly from SQLite DB
            with st.spinner("🔢 डेटाबेस से हिसाब निकाला जा रहा है..."):
                ans = generate_answer(intent, conn, settings, detected_language=detected_lang)
                st.session_state.last_answer = ans

            # Generate TTS voice audio using Bulbul v3
            with st.spinner("🔊 Bulbul v3 आवाज़ में उत्तर तैयार किया जा रहा है..."):
                try:
                    tts_bytes = text_to_speech(ans.text, detected_lang, settings)
                    st.session_state.last_tts_audio = tts_bytes
                except Exception as exc:
                    logger.warning("TTS generation error: %s", exc)
                    st.session_state.last_tts_audio = None

        # Display answer if available
        if st.session_state.last_answer:
            ans = st.session_state.last_answer
            st.markdown("#### 3. उत्तर (Spoken Answer)")

            st.markdown(
                f'<div class="answer-box">🗣️ {ans.text}</div>',
                unsafe_allow_html=True,
            )

            # Audio Player
            if st.session_state.last_tts_audio:
                st.audio(st.session_state.last_tts_audio, format="audio/wav", autoplay=True)

            # Audit badge: Core Hackathon Principle
            st.markdown(
                '<div class="audit-badge">'
                '🛡️ <b>सत्यापित हिसाब:</b> यह गणना सीधे डेटाबेस SQL क्वेरी से की गई है, LLM के अंदाज़े से नहीं।'
                '</div>',
                unsafe_allow_html=True,
            )

            # Supporting rows
            if ans.rows:
                st.markdown("##### 📋 संबंधित बही-खाता प्रविष्टियां (Audit Trail):")
                df_rows = pd.DataFrame(ans.rows)[["entry_date", "customer_raw", "description", "amount", "entry_type"]]
                df_rows.columns = ["तारीख", "ग्राहक", "सामान", "रकम (₹)", "प्रकार"]
                st.dataframe(df_rows, use_container_width=True)

            # Stretch goal: Reminder message for debtor
            if (
                st.session_state.last_intent
                and st.session_state.last_intent.customer_key
                and ans.numbers.get("balance", 0) > 0
            ):
                st.markdown("---")
                st.markdown("#### 📲 तकादा संदेश (WhatsApp Payment Reminder)")

                cust_name = st.session_state.last_intent.customer_name or st.session_state.last_intent.customer_key
                balance_val = ans.numbers.get("balance", 0)

                reminder_data = generate_reminder(
                    customer_name=cust_name,
                    amount=balance_val,
                    shop_name=shop_name,
                    language=st.session_state.last_language,
                    settings=settings if not settings.demo_mode else None,
                )

                st.markdown(
                    f'<div class="reminder-box">'
                    f'<b>व्हाट्सएप संदेश:</b><br>{reminder_data["message"].replace(chr(10), "<br>")}'
                    f'<br><br>'
                    f'<a href="{reminder_data["whatsapp_url"]}" target="_blank" class="wa-button">'
                    f'💬 WhatsApp पर भेजें (Open WhatsApp)'
                    f'</a>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("👈 बाईं तरफ माइक से बोलें या कोई सवाल चुनकर 'हिसाब निकालें' दबाएं।")


# ===========================================================================
# TAB 3: Complete Customer Ledger & Audit Trail
# ===========================================================================
with tab_ledger:
    st.markdown("### 📒 डिजिटल खाता बही (Customer Accounts & Statements)")

    cust_list = list_customers(conn)
    disp_names = customer_display_names(conn)

    if not cust_list:
        st.info("अभी खाते में कोई प्रविष्टियां नहीं हैं। '1. फोटो से बही-खाता' टैब में जाकर फोटो स्कैन करें या साइडबार से 'लोड करें सैंपल डेटा' पर क्लिक करें।")
    else:
        col_l1, col_l2 = st.columns([1, 2])

        with col_l1:
            st.markdown("#### 🏆 सबसे ज़्यादा बकाया (Top Debtors)")
            top_5 = top_debtors(conn, n=5)
            if top_5:
                for i, d in enumerate(top_5, 1):
                    c_name = disp_names.get(d.customer_key, d.customer_raw)
                    st.markdown(f"**{i}. {c_name}**: :red[₹{d.balance:,.0f}] (उधार: ₹{d.total_credit:,.0f}, जमा: ₹{d.total_paid:,.0f})")
            else:
                st.success("किसी का भी बकाया नहीं है! सब चुकता है।")

            st.markdown("---")
            selected_key = st.selectbox(
                "विस्तृत खाता देखने के लिए ग्राहक चुनें:",
                options=cust_list,
                format_func=lambda k: f"{disp_names.get(k, k)} (खाता: {k})",
            )

        with col_l2:
            if selected_key:
                c_bal = balance_of(conn, selected_key)
                c_name = disp_names.get(selected_key, selected_key)

                st.markdown(f"#### 📄 खाता विवरण: {c_name}")

                col_m1, col_m2, col_m3 = st.columns(3)
                col_m1.metric("कुल उधार (Credit)", f"₹{c_bal.total_credit:,.0f}")
                col_m2.metric("कुल जमा (Paid)", f"₹{c_bal.total_paid:,.0f}")
                balance_color = "red" if c_bal.balance > 0 else "green"
                col_m3.metric("बाकी हिसाब (Balance)", f"₹{c_bal.balance:,.0f}")

                # Timeline of entries
                c_entries = entries_of(conn, selected_key)
                if c_entries:
                    st.markdown("##### सभी लेन-देन (All Transactions):")
                    df_c = pd.DataFrame(c_entries)[["entry_date", "description", "amount", "entry_type", "created_at"]]
                    df_c.columns = ["तारीख", "सामान विवरण", "रकम (₹)", "प्रकार", "दर्ज समय"]
                    st.dataframe(df_c, use_container_width=True)
