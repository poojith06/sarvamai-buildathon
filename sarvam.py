"""Thin wrappers around the three Sarvam APIs used by the app."""
import base64
import json
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.sarvam.ai"
TTS_LANGS = {"bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"}


class SarvamError(RuntimeError):
    pass


def _key():
    key = os.getenv("SARVAM_API_KEY", "").strip()
    if not key or key == "paste_your_key_here":
        raise SarvamError("SARVAM_API_KEY is not set. Paste your key into the .env file.")
    return key


def _check(resp):
    if resp.status_code != 200:
        try:
            msg = resp.json().get("error", {}).get("message") or resp.text
        except ValueError:
            msg = resp.text
        raise SarvamError(f"Sarvam API {resp.status_code}: {msg}")
    return resp.json()


def speech_to_text(audio_bytes, filename="audio.wav", language_code="unknown"):
    """Saaras v4: audio -> (transcript, detected language code)."""
    resp = requests.post(
        f"{BASE}/speech-to-text",
        headers={"api-subscription-key": _key()},
        files={"file": (filename, audio_bytes, "audio/wav")},
        data={"model": "saaras:v4", "language_code": language_code},
        timeout=60,
    )
    data = _check(resp)
    return data.get("transcript", ""), data.get("language_code")


def chat_json(system, user, schema, reasoning_effort="low"):
    """Sarvam-105B -> parsed dict. reasoning_effort=None turns thinking off (fast, less careful).

    The schema goes in the prompt rather than response_format: json_schema mode made the
    model loop on whitespace inside string fields.
    """
    key = _key()
    system += "\n\nReturn ONLY a JSON object (no markdown) matching this JSON Schema:\n" + json.dumps(schema)
    resp = requests.post(
        f"{BASE}/v1/chat/completions",
        headers={"api-subscription-key": key, "Authorization": f"Bearer {key}"},
        json={
            "model": "sarvam-105b",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
            # Reasoning tokens count against max_tokens; 4096 was fully consumed by thinking.
            "max_tokens": 16000 if reasoning_effort else 4096,
            "reasoning_effort": reasoning_effort,
        },
        timeout=180,
    )
    content = _check(resp)["choices"][0]["message"]["content"] or ""
    # Be tolerant of code fences around the JSON
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise SarvamError(f"Model did not return JSON: {content[:300]}")
    return json.loads(m.group(0))


def text_to_speech(text, language_code="hi-IN", speaker="shubh"):
    """Bulbul v3: text -> WAV bytes."""
    if language_code not in TTS_LANGS:
        language_code = "hi-IN"
    resp = requests.post(
        f"{BASE}/text-to-speech",
        headers={"api-subscription-key": _key()},
        json={"text": text[:2500], "language_code": language_code, "model": "bulbul:v3", "speaker": speaker},
        timeout=60,
    )
    return base64.b64decode(_check(resp)["audios"][0])
