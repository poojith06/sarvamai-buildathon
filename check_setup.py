"""Run `python check_setup.py` to confirm the API key works with all three Sarvam APIs."""
import sarvam

print("1/3 Bulbul v3 (text -> speech)…", end=" ", flush=True)
wav = sarvam.text_to_speech("నమస్కారం, నేను సర్కారీ సహాయక్.", "te-IN")
print(f"OK ({len(wav):,} bytes)")

print("2/3 Saaras v4 (speech -> text)…", end=" ", flush=True)
text, lang = sarvam.speech_to_text(wav)
print(f"OK [{lang}] {text}")

print("3/3 Sarvam-105B (structured JSON)…", end=" ", flush=True)
out = sarvam.chat_json(
    "Reply with JSON only.",
    "Translate 'hello friend' to Hindi.",
    {"type": "object", "properties": {"hindi": {"type": "string"}}, "required": ["hindi"]},
)
print(f"OK {out}")
print("\nAll good! Now run:  streamlit run app.py")
