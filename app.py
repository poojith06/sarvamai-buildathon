"""Sarkari Sahayak: speak your situation, hear which government schemes you can claim."""
import hashlib
import json
import time
from pathlib import Path

import streamlit as st

import sarvam

HERE = Path(__file__).parent

LANGS = {
    "Same as speaker": None,
    "తెలుగు (Telugu)": "te-IN",
    "हिन्दी (Hindi)": "hi-IN",
    "English": "en-IN",
    "தமிழ் (Tamil)": "ta-IN",
    "ಕನ್ನಡ (Kannada)": "kn-IN",
    "മലയാളം (Malayalam)": "ml-IN",
    "मराठी (Marathi)": "mr-IN",
    "বাংলা (Bengali)": "bn-IN",
    "ગુજરાતી (Gujarati)": "gu-IN",
    "ਪੰਜਾਬੀ (Punjabi)": "pa-IN",
    "ଓଡ଼ିଆ (Odia)": "od-IN",
}

DEMOS = {
    "🛵 Ravi, delivery rider (Telugu)": "నేను హైదరాబాద్‌లో స్విగ్గీ డెలివరీ చేస్తాను. నెలకి 18 వేలు వస్తుంది. నా వయసు 28. పెళ్లి అయ్యింది, ఇద్దరు పిల్లలు ఉన్నారు.",
    "🌾 Ramesh, farmer (Hindi)": "मैं उत्तर प्रदेश में किसान हूँ, मेरे पास दो एकड़ ज़मीन है। मेरी उम्र 45 साल है, घर में पत्नी और तीन बच्चे हैं।",
    "👵 Lakshmi, widow (Telugu)": "నా పేరు లక్ష్మి, వయసు 66. నా భర్త చనిపోయారు. వరంగల్ దగ్గర గ్రామంలో ఉంటాను, నాకు ఆదాయం ఏమీ లేదు.",
    "🧕 Ayesha, street vendor (Hinglish)": "Main Hyderabad mein sabzi ka thela lagati hoon, mahine ka 12 hazaar kamati hoon. Age 34, ek beti hai 6 saal ki.",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "profile": {
            "type": "object",
            "properties": {
                **{k: {"type": "string"} for k in
                   ["occupation", "age", "gender", "monthly_income", "state", "family", "other"]},
                "age_years": {"type": "integer", "description": "-1 if unknown"},
                "monthly_income_rupees": {"type": "integer", "description": "-1 if unknown"},
            },
            "required": ["occupation", "age", "gender", "monthly_income", "state", "family", "other",
                         "age_years", "monthly_income_rupees"],
        },
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scheme_id": {"type": "string"},
                    "fit": {"type": "string", "enum": ["likely", "possible"]},
                    "reason": {"type": "string"},
                },
                "required": ["scheme_id", "fit", "reason"],
            },
        },
        "reply_language_code": {"type": "string", "enum": sorted(sarvam.TTS_LANGS)},
        "spoken_summary": {"type": "string"},
        "follow_up_question": {"type": "string"},
        "follow_up_question_en": {"type": "string"},
    },
    "required": ["profile", "matches", "reply_language_code", "spoken_summary",
                 "follow_up_question", "follow_up_question_en"],
}


@st.cache_data
def load_schemes():
    return json.loads((HERE / "schemes.json").read_text(encoding="utf-8"))


def build_system_prompt(schemes, reply_lang):
    compact = [{k: s.get(k) for k in ["id", "name", "level", "category", "benefit", "eligibility",
                                       "exclusions", "age_min", "age_max", "documents"]} for s in schemes]
    lang_rule = (f"Write spoken_summary and follow_up_question in language {reply_lang} and set "
                 f"reply_language_code to {reply_lang}." if reply_lang else
                 "Write spoken_summary and follow_up_question in the SAME language the citizen used "
                 "(if they used romanised Hindi/Hinglish, reply in Hindi, hi-IN) and set reply_language_code accordingly.")
    return f"""You are Sarkari Sahayak, a friendly government-scheme helper for Indian citizens.
The citizen describes their life situation (possibly across several messages, in any Indian language or code-mixed).

SCHEMES DATABASE (the ONLY schemes you may recommend; use their exact "id"):
{json.dumps(compact, ensure_ascii=False)}

Rules:
1. Extract the citizen's profile in English. Use "unknown" for anything not stated. Never guess facts
   (do not infer gender from a name or job).
2. For each scheme, check the eligibility, exclusions and age limits against the profile.
   - "likely": every stated criterion is met by what the citizen told you.
   - "possible": nothing rules them out, but some criterion depends on unknown information.
   - Do NOT include schemes the citizen is clearly ineligible for.
   Order matches by how valuable they are to this person. "reason" is one short English sentence.
3. spoken_summary: 3-5 short, warm, simple sentences meant to be read aloud by a voice. Address the person
   respectfully. Mention the top 2-3 schemes by name, what they get, and the key documents to keep ready.
   Only mention schemes that appear in "matches"; if you want to mention a scheme, add it to matches first.
   No markdown, no bullet points, no URLs. Write money like "2,00,000 rupees" or "3,000 rupees".
   Always write Indian languages in their native script (Hindi in Devanagari, Telugu in Telugu script),
   even if the citizen typed in Roman letters; scheme names like e-Shram or PM-KISAN may stay in English.
4. follow_up_question: ONE question asking for the single missing fact that would most change the result
   (e.g. land ownership, bank account, income tax payer, age). Empty string if nothing important is missing.
5. {lang_rule}"""


def passes_hard_limits(scheme, profile):
    """Deterministic age/income check, so an arithmetic slip by the model never reaches the citizen."""
    age, income = profile.get("age_years", -1), profile.get("monthly_income_rupees", -1)
    if isinstance(age, int) and age >= 0:
        if scheme.get("age_min") is not None and age < scheme["age_min"]:
            return False
        if scheme.get("age_max") is not None and age > scheme["age_max"]:
            return False
    if isinstance(income, int) and income >= 0 and scheme.get("income_max_monthly") is not None:
        if income > scheme["income_max_monthly"]:
            return False
    return True


def analyse(statements, reply_lang, careful=True):
    schemes = {s["id"]: s for s in load_schemes()}
    user = "What the citizen has said so far:\n" + "\n".join(f"- {s}" for s in statements)
    system = build_system_prompt(list(schemes.values()), reply_lang)
    try:
        result = sarvam.chat_json(system, user, SCHEMA, reasoning_effort="low" if careful else None)
    except sarvam.SarvamError:
        if not careful:
            raise
        # Thinking occasionally runs past the token budget and leaves no answer; retry without it.
        result = sarvam.chat_json(system, user, SCHEMA, reasoning_effort=None)
    result["matches"] = [m for m in result.get("matches", [])
                         if m.get("scheme_id") in schemes
                         and passes_hard_limits(schemes[m["scheme_id"]], result.get("profile", {}))]
    return result


def process(text):
    """Add a new statement from the citizen and recompute everything."""
    st.session_state.statements.append(text)
    reply_lang = LANGS[st.session_state.reply_choice] or None
    timings = st.session_state.timings
    with st.status("Checking schemes for you…", expanded=False) as status:
        t = time.time()
        result = analyse(st.session_state.statements, reply_lang, st.session_state.careful)
        timings["Sarvam-105B (eligibility)"] = time.time() - t
        status.update(label="Preparing voice reply…")
        speech = result["spoken_summary"]
        if result.get("follow_up_question"):
            speech += " " + result["follow_up_question"]
        t = time.time()
        st.session_state.audio = sarvam.text_to_speech(speech, result["reply_language_code"],
                                                       st.session_state.voice)
        timings["Bulbul v3 (voice)"] = time.time() - t
        status.update(label="Done", state="complete")
    st.session_state.result = result


def reset():
    for k in ["result", "audio", "last_audio"]:
        st.session_state.pop(k, None)
    st.session_state.statements = []
    st.session_state.timings = {}
    st.session_state.mic_key = st.session_state.get("mic_key", 0) + 1


# ---------------------------------------------------------------- UI
st.set_page_config(page_title="Sarkari Sahayak", page_icon="🏛️", layout="wide")
st.session_state.setdefault("statements", [])
st.session_state.setdefault("timings", {})
st.session_state.setdefault("mic_key", 0)

with st.sidebar:
    st.header("⚙️ Settings")
    st.selectbox("Reply language", list(LANGS), key="reply_choice")
    st.selectbox("Voice", ["shubh", "kavya", "priya", "rahul", "anand", "ritu"], key="voice")
    st.toggle("🧠 Careful mode", value=True, key="careful",
              help="Sarvam-105B thinks before answering (~30s, more accurate). Off = ~5s.")
    st.divider()
    st.subheader("🎭 Demo citizens")
    st.caption("No mic? Try a sample citizen.")
    demo_clicked = None
    for label in DEMOS:
        if st.button(label, use_container_width=True):
            demo_clicked = label
    st.divider()
    st.button("🔄 Start over", on_click=reset, use_container_width=True)
    st.caption(f"{len(load_schemes())} official schemes loaded · Saaras v4 · Sarvam-105B · Bulbul v3")

st.title("🏛️ Sarkari Sahayak")
st.markdown("**Tell us about yourself in your own language and we'll find the government benefits you can claim.**  \n"
            "Your job, age, income, family, and where you live.")

col_mic, col_text = st.columns([1, 1])
with col_mic:
    audio = st.audio_input("🎙️ Tap and speak", key=f"mic_{st.session_state.mic_key}")
with col_text:
    typed = st.chat_input("…or type here")

try:
    if demo_clicked:
        reset()
        process(DEMOS[demo_clicked])
    elif audio is not None:
        data = audio.getvalue()
        h = hashlib.md5(data).hexdigest()
        if h != st.session_state.get("last_audio"):
            st.session_state.last_audio = h
            t = time.time()
            with st.spinner("Listening…"):
                transcript, _ = sarvam.speech_to_text(data)
            st.session_state.timings["Saaras v4 (speech→text)"] = time.time() - t
            if transcript.strip():
                process(transcript)
            else:
                st.warning("Couldn't hear anything. Please try again a little closer to the mic.")
    elif typed:
        process(typed)
except sarvam.SarvamError as e:
    st.error(str(e))

# ---------------------------------------------------------------- Results
if st.session_state.statements:
    with st.expander("🗣️ What we heard", expanded=True):
        for s in st.session_state.statements:
            st.markdown(f"> {s}")

result = st.session_state.get("result")
if result:
    if st.session_state.get("audio"):
        st.audio(st.session_state.audio, format="audio/wav", autoplay=True)

    st.markdown(f"#### 💬 {result['spoken_summary']}")
    if result.get("follow_up_question"):
        st.info(f"**❓ {result['follow_up_question']}**  \n_{result['follow_up_question_en']}_  \n"
                "Answer with the mic or by typing, and results will update.")

    p = result["profile"]
    st.subheader("👤 Your profile")
    cols = st.columns(6)
    for c, (label, key) in zip(cols, [("Job", "occupation"), ("Age", "age"), ("Gender", "gender"),
                                       ("Income/month", "monthly_income"), ("State", "state"),
                                       ("Family", "family")]):
        c.metric(label, p.get(key) or "unknown")

    schemes = {s["id"]: s for s in load_schemes()}
    st.subheader(f"✅ Schemes for you ({len(result['matches'])})")
    if not result["matches"]:
        st.write("No matching schemes found yet. Tell us a little more about yourself.")
    for m in result["matches"]:
        s = schemes[m["scheme_id"]]
        badge = "🟢 Likely eligible" if m["fit"] == "likely" else "🟡 Possibly eligible"
        with st.container(border=True):
            top, right = st.columns([4, 1])
            top.markdown(f"### {s['name']}  \n{s.get('full_name', '')}")
            right.markdown(f"**{badge}**")
            st.markdown(f"**🎁 Benefit:** {s['benefit']}")
            st.markdown(f"**🤔 Why you:** {m['reason']}")
            if s.get("documents"):
                st.markdown("**📄 Keep ready:** " + ", ".join(s["documents"]))
            if s.get("how_to_apply"):
                st.markdown(f"**📝 How to apply:** {s['how_to_apply']}")
            links = []
            if s.get("apply_url"):
                links.append(f"[Apply here]({s['apply_url']})")
            if s.get("source_url"):
                links.append(f"[Official source]({s['source_url']})")
            st.caption(" · ".join(links))

    if st.session_state.timings:
        with st.expander("⏱️ Pipeline timings"):
            for k, v in st.session_state.timings.items():
                st.write(f"{k}: **{v:.1f}s**")
