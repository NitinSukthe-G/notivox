"""
sarvam.py - everything that talks to Sarvam AI lives here.

Sarvam gives NOTIVOX four abilities, each through an "API"
(a web address our code sends a request to, and gets an answer back from):

  chat_json()       the BRAIN  - understands the user and writes the notice  (model: sarvam-105b)
  speech_to_text()  the EARS   - turns a voice recording into text           (model: saaras:v4)
  text_to_speech()  the MOUTH  - turns the agent's reply into voice          (model: bulbul:v4-flash)
  translate()       the TRANSLATOR - English notice -> Hindi/Tamil/...        (model: sarvam-translate:v1)

Adapted from the knot project's sarvam.py (same retry + spend-guard ideas),
but spending is now saved in MongoDB instead of SQLite.
"""
import base64     # Sarvam sends audio as base64 text; this turns it back into sound bytes
import json       # converts between JSON text and Python dictionaries
import re         # "regular expressions": find/clean patterns in text
import sys        # sys.argv = the extra words typed after the command (e.g. "voices")
import time       # time.sleep() = wait a bit before retrying

import httpx      # sends requests over the internet

from app import config, db

# Languages NOTIVOX supports (Sarvam's codes -> names we show to the user)
LANGUAGES = {
    "en-IN": "English", "hi-IN": "Hindi", "bn-IN": "Bengali", "ta-IN": "Tamil",
    "te-IN": "Telugu", "kn-IN": "Kannada", "ml-IN": "Malayalam", "mr-IN": "Marathi",
    "gu-IN": "Gujarati", "pa-IN": "Punjabi", "od-IN": "Odia",
}


# ================================================================ errors
class BudgetExceeded(Exception):
    """Raised when a call would cross our spending limit. The app then uses free fallbacks."""


class SarvamError(Exception):
    """Raised when Sarvam fails (network down, bad key, server error...)."""


# ================================================================ sending requests
# One shared "client" (re-used connection). Timeout: wait max 45 s for an answer.
_client = httpx.Client(timeout=httpx.Timeout(45.0, connect=10.0))
RETRY_STATUS = {429, 500, 502, 503, 504}   # "too busy" / "server hiccup" codes worth retrying


def _post(path: str, **kwargs) -> httpx.Response:
    """
    Send a POST request to Sarvam, retrying up to 3 times on temporary problems.
    path = the part after the base address, e.g. "/translate".
    """
    url = config.SARVAM_BASE + path
    for attempt in range(3):                      # attempt = 0, 1, 2
        try:
            resp = _client.post(url, **kwargs)
            if resp.status_code not in RETRY_STATUS or attempt == 2:
                return resp                       # success, or a real error - stop retrying
        except httpx.TransportError as e:         # no internet, timeout...
            if attempt == 2:
                raise SarvamError("Sarvam is not responding. Please check your internet and try again.") from e
        time.sleep(1.5 * (attempt + 1))           # wait 1.5 s, then 3 s, before trying again
    raise SarvamError("Sarvam request failed")


def _headers(json_body: bool = True) -> dict:
    """The 'envelope' of every request: our API key proves who we are."""
    if not config.SARVAM_API_KEY:
        raise SarvamError("SARVAM_API_KEY is missing in .env")
    h = {"api-subscription-key": config.SARVAM_API_KEY}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _raise_for_error(resp: httpx.Response, what: str):
    """Turn Sarvam's error answers into clear Python errors."""
    if resp.status_code == 402:
        raise BudgetExceeded("Sarvam credits are exhausted")
    if resp.status_code >= 400:
        raise SarvamError(f"{what} error {resp.status_code}: {resp.text[:300]}")


# ================================================================ spend guard
def _check_budget(device_id: str | None, voice: bool):
    """Stop BEFORE spending if we are over a limit."""
    spent = db.total_spend()
    cap = config.VOICE_CAP_INR if voice else config.HARD_CAP_INR
    if spent >= cap:
        raise BudgetExceeded(f"Spending limit reached (Rs {spent:.2f} of Rs {cap:.0f})")
    if device_id and db.device_calls_today(device_id) >= config.DAILY_DEVICE_LIMIT:
        raise BudgetExceeded("Daily limit for this device reached. Please try again tomorrow.")


def voice_available() -> bool:
    """Can we still afford Sarvam voice? (If not, the web page uses the browser's free voice.)"""
    return bool(config.SARVAM_API_KEY) and db.total_spend() < config.VOICE_CAP_INR


# ================================================================ 1. BRAIN
def _parse_json(text: str) -> dict:
    """Pull a JSON object out of the model's answer, even if it added ``` fences or extra words."""
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()   # drop hidden "thinking"
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def chat_json(system: str, user: str, *, device_id: str | None = None, purpose: str = "llm",
              max_tokens: int = 1500, temperature: float = 0.2) -> dict:
    """
    Ask the LLM something and get back a Python dictionary.

    system = the rules / role ("You are NOTIVOX, ...")
    user   = the actual input for this turn
    Returns the JSON object the model wrote, as a dict.
    """
    _check_budget(device_id, voice=False)
    body = {
        "model": config.LLM_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,                  # low = more careful and consistent
        "max_tokens": max_tokens,                    # upper limit on answer length
        "response_format": {"type": "json_object"},  # force a JSON answer
        "reasoning_effort": None,                    # no long "thinking": faster and cheaper
    }
    resp = _post("/v1/chat/completions", headers=_headers(), json=body)
    if resp.status_code == 400 and "reasoning" in resp.text.lower():
        body.pop("reasoning_effort", None)           # older API versions: just drop that option
        resp = _post("/v1/chat/completions", headers=_headers(), json=body)
    _raise_for_error(resp, "LLM")

    data = resp.json()
    usage = data.get("usage") or {}
    # Price: Rs 15 per 1M input tokens, Rs 60 per 1M output tokens
    cost = usage.get("prompt_tokens", 0) * 15 / 1e6 + usage.get("completion_tokens", 0) * 60 / 1e6
    db.log_spend(device_id, purpose, cost, {"usage": usage})

    content = data["choices"][0]["message"].get("content") or ""
    try:
        return _parse_json(content)
    except (json.JSONDecodeError, ValueError) as e:
        raise SarvamError(f"The AI returned an unreadable answer: {content[:200]}") from e


# ================================================================ 2. EARS
def speech_to_text(audio: bytes, filename: str, content_type: str, *, device_id: str | None,
                   duration_s: float | None = None, language_code: str | None = None) -> tuple[str, str | None]:
    """
    Turn a voice recording (max ~30 seconds) into text.
    language_code = the language the user talks in (a hint, so English with an Indian accent
    is not mistaken for Hindi). Returns (transcript, language_code).
    """
    _check_budget(device_id, voice=True)
    files = {"file": (filename, audio, content_type or "audio/wav")}
    data = {"model": config.STT_MODEL, "mode": config.STT_MODE}
    if language_code:
        data["language_code"] = language_code
    resp = _post("/speech-to-text", headers=_headers(json_body=False), files=files, data=data)
    if resp.status_code == 400 and language_code and "language" in resp.text.lower():
        data.pop("language_code")                        # hint not accepted: let Sarvam detect the language
        resp = _post("/speech-to-text", headers=_headers(json_body=False), files=files, data=data)
    _raise_for_error(resp, "Speech-to-text")
    out = resp.json()
    secs = duration_s if duration_s and duration_s > 0 else 15.0
    db.log_spend(device_id, "stt", min(secs, 30) / 60 * 0.5, {"secs": secs})   # ~Rs 0.5 per minute
    return (out.get("transcript") or "").strip(), out.get("language_code")


# ================================================================ 3. MOUTH
def language_of(text: str) -> str:
    """Guess the language code from the letters used (Devanagari -> hi-IN, Tamil -> ta-IN...)."""
    ranges = [
        ("hi-IN", 0x0900, 0x097F), ("bn-IN", 0x0980, 0x09FF), ("pa-IN", 0x0A00, 0x0A7F),
        ("gu-IN", 0x0A80, 0x0AFF), ("od-IN", 0x0B00, 0x0B7F), ("ta-IN", 0x0B80, 0x0BFF),
        ("te-IN", 0x0C00, 0x0C7F), ("kn-IN", 0x0C80, 0x0CFF), ("ml-IN", 0x0D00, 0x0D7F),
    ]
    counts = {code: 0 for code, _, _ in ranges}
    latin = 0
    for ch in text:
        if ch.isascii() and ch.isalpha():
            latin += 1
        for code, lo, hi in ranges:
            if lo <= ord(ch) <= hi:
                counts[code] += 1
    best = max(counts, key=counts.get)
    # Whichever script has the MOST letters wins (so even a short "సరే." counts as Telugu).
    # All-Latin text, including Hinglish ("kya hua?"), is en-IN.
    return best if counts[best] > 0 and counts[best] >= latin else "en-IN"


_tts_cache: dict[str, bytes] = {}   # remembers recent voice clips so repeats cost nothing


class VoiceUnavailable(Exception):
    """Ishita's voice can't speak this sentence. We stay silent rather than use a different voice."""


def text_to_speech(text: str, *, device_id: str | None, language_code: str | None = None) -> bytes:
    """
    Turn text into speech - ALWAYS in Ishita's voice (config.TTS_SPEAKER), in every language.
    There is deliberately NO fallback to another voice: if Sarvam refuses, VoiceUnavailable is raised
    and the web page shows the text without speaking. Returns WAV bytes.
    """
    text = text.strip()[:1500]
    lang = language_code or language_of(text)
    key = f"{lang}|{config.TTS_SPEAKER}|{text}"
    if key in _tts_cache:
        return _tts_cache[key]
    _check_budget(device_id, voice=True)

    body = {"text": text, "target_language_code": lang, "model": config.TTS_MODEL, "speaker": config.TTS_SPEAKER}
    resp = _post("/text-to-speech", headers=_headers(), json=body)
    if resp.status_code == 400:
        print(f"[voice] Ishita could not speak {lang}: {resp.text[:200]}")
        raise VoiceUnavailable(f"Ishita's voice is not available for {LANGUAGES.get(lang, lang)}")
    _raise_for_error(resp, "Text-to-speech")
    wav = base64.b64decode("".join(resp.json().get("audios") or []))
    db.log_spend(device_id, "tts", len(text) / 1000 * 1.0, {"chars": len(text), "lang": lang})
    if len(_tts_cache) > 200:
        _tts_cache.clear()
    _tts_cache[key] = wav
    return wav


# ================================================================ 4. TRANSLATOR
def translate(text: str, target: str, *, device_id: str | None, source: str = "en-IN") -> str:
    """
    Translate text, e.g. translate("Refund my deposit.", "hi-IN").
    Long text is split into chunks (the API accepts ~2000 characters at a time).
    """
    if target == source or not text.strip():
        return text
    _check_budget(device_id, voice=False)
    out = []
    for chunk in _chunks(text, 1800):
        body = {"input": chunk, "source_language_code": source, "target_language_code": target,
                "model": "sarvam-translate:v1"}
        resp = _post("/translate", headers=_headers(), json=body)
        _raise_for_error(resp, "Translate")
        out.append(resp.json().get("translated_text", ""))
        db.log_spend(device_id, "translate", len(chunk) / 10000 * 20, {"chars": len(chunk)})  # ~Rs 20 per 10k chars
    return " ".join(out).strip()


def _chunks(text: str, size: int) -> list[str]:
    """Split long text at sentence ends so each piece is at most `size` characters."""
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.!?।])\s+", text):
        if len(current) + len(sentence) + 1 > size and current:
            parts.append(current)
            current = ""
        current = (current + " " + sentence).strip()
    if current:
        parts.append(current)
    return parts


# ================================================================ self-test
# Run:  python -m app.sarvam
if __name__ == "__main__" and len(sys.argv) == 1:
    print("1. BRAIN  (chat_json) ...")
    answer = chat_json(
        'Reply only with JSON like {"reply": "..."}',
        "In one short sentence, what is a legal notice?",
        device_id="selftest",
    )
    print("   ->", answer.get("reply"))

    print("2. TRANSLATOR (translate to Hindi) ...")
    hindi = translate("Please refund my security deposit of Rs 30,000 within 15 days.", "hi-IN", device_id="selftest")
    print("   ->", hindi)

    print("3. MOUTH  (text_to_speech) ...")
    wav = text_to_speech("Namaste, main NOTIVOX hoon. Bataiye, kya hua?", device_id="selftest")
    with open("selftest_voice.wav", "wb") as f:       # (test_voice.wav, your reference voice, is never overwritten)
        f.write(wav)
    print("   -> saved selftest_voice.wav (", len(wav) // 1024, "KB ) - double-click it to listen")

    print("4. EARS   (speech_to_text on that same clip) ...")
    text, lang = speech_to_text(wav, "selftest_voice.wav", "audio/wav", device_id="selftest", duration_s=4)
    print("   ->", text, "| language:", lang)

    print("Total spent so far: Rs", round(db.total_spend(), 4))
    print("Sarvam OK")


# ================================================================ Ishita in every language
# Run:  python -m app.sarvam voices
# Tries Ishita's voice in all 11 languages, saves ishita_<language>.wav for each one that works,
# and tells you which languages (if any) Ishita can't speak.
if __name__ == "__main__" and sys.argv[1:] == ["voices"]:
    english = "Namaste, I am NOTIVOX. Please tell me, what happened?"
    for code, name in LANGUAGES.items():
        line = english if code == "en-IN" else translate(english, code, device_id="selftest")
        try:
            wav = text_to_speech(line, device_id="selftest", language_code=code)
            with open(f"ishita_{code}.wav", "wb") as f:
                f.write(wav)
            print(f"  {name:10} OK      -> ishita_{code}.wav")
        except VoiceUnavailable:
            print(f"  {name:10} NOT AVAILABLE in Ishita's voice (text only for this language)")
