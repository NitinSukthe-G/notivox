"""
agent.py - the conversation brain of NOTIVOX.

Each time the user says something, turn() does this:
  1. load the case from MongoDB (story so far, facts, stage)
  2. ask the AI: "here is everything we know + what the user just said - reply, and tell me any new facts"
  3. merge the new facts into the case
  4. decide the next STAGE in code (so the AI can never skip a required step)
  5. save everything and return the reply

Stages:  choose_language -> listening -> clarifying -> language -> confirm -> ready -> drafted
         (choose_language = "which language shall we TALK in?"; language = "which language for the NOTICE?")
"""
import difflib    # finds "similar" words - used to understand misspelled language names
import json
import re
from datetime import datetime, timedelta, timezone

from app import catalog, db, prompts, sarvam

IST = timezone(timedelta(hours=5, minutes=30))   # India Standard Time = UTC + 5:30

# Words people may say when choosing a language -> Sarvam language code.
# Includes English names, common spellings and the name in its own script.
LANGUAGE_WORDS = {
    "hi-IN": ["hindi", "hinglish", "हिंदी", "हिन्दी"],
    "en-IN": ["english", "angrezi", "angreji", "अंग्रेज़ी", "अंग्रेजी", "इंग्लिश"],
    "ta-IN": ["tamil", "தமிழ்"],
    "te-IN": ["telugu", "తెలుగు"],
    "kn-IN": ["kannada", "ಕನ್ನಡ"],
    "ml-IN": ["malayalam", "മലയാളം"],
    "mr-IN": ["marathi", "मराठी"],
    "gu-IN": ["gujarati", "ગુજરાતી"],
    "bn-IN": ["bengali", "bangla", "বাংলা"],
    "pa-IN": ["punjabi", "ਪੰਜਾਬੀ"],
    "od-IN": ["odia", "oriya", "ଓଡ଼ିଆ"],
}


# Real words that LOOK like language names but are not requests - never "fix" these
# (e.g. "I live in Punjab" must not become "I live in Punjabi")
REAL_WORDS = {"punjab", "gujarat", "bengal", "hindu", "hindus", "bangle", "bangles", "tamils",
              "marathon", "kerala", "odisha", "orissa", "telangana", "karnataka", "maharashtra"}


def _fix_typos(text: str) -> str:
    """
    Replace misspelled language names with the correct spelling, so "engilsh" -> "english",
    "telgu" -> "telugu", "hindhi" -> "hindi". Uses difflib, Python's built-in "how similar
    are these two words?" tool. Only words of 4+ letters that are at least 80% similar are fixed.
    """
    names = [w for words in LANGUAGE_WORDS.values() for w in words if w.isascii()]
    fixed = []
    for word in re.findall(r"\w+|\W+", text):            # keep spaces and punctuation as they are
        lower = word.lower()
        if len(lower) >= 4 and lower.isascii() and lower not in names and lower not in REAL_WORDS:
            # 0.85 = "85% similar": catches typos (engilsh = 0.86) but not real words (hindu = 0.80)
            close = difflib.get_close_matches(lower, names, n=1, cutoff=0.85)
            if close:
                word = close[0]
        fixed.append(word)
    return "".join(fixed)


def detect_language_choice(text: str) -> str | None:
    """
    Find which language the user named, e.g. "Hindi mein baat karte hain" -> "hi-IN".
    Small spelling mistakes are fixed first ("engilsh" -> "english").
    If several are named, the one mentioned FIRST wins. Returns None if none found.
    """
    lower = _fix_typos(text).lower()
    # "no"-words, matched as WHOLE words only (so "matladandi" is not mistaken for "mat")
    # (Hindi "नहीं" is checked without \b, because \b doesn't work well with Devanagari letters)
    negation_after = re.compile(r"^\W*((nahi|nahin|not|no|mat)\b|नहीं|नही)")
    best_code, best_pos = None, len(lower) + 1
    for code, words in LANGUAGE_WORDS.items():
        for w in words:
            pos = lower.find(w.lower())
            if pos == -1:
                continue
            after = lower[pos + len(w): pos + len(w) + 10]       # the few letters right after the word
            before = lower[max(0, pos - 7): pos]                 # the few letters right before the word
            if negation_after.search(after):                     # "Hindi nahi" -> the user does NOT want Hindi
                continue
            if any(n in before for n in ("not ", "no ", "don't ")):   # "not Hindi" -> same
                continue
            if pos < best_pos:
                best_code, best_pos = code, pos
    return best_code


# Phrases that ASK for another language. {L} is replaced by the language word.
#   "speak in telugu", "talk telugu", "telugu mein baat karo", "telugu lo matladandi", "in telugu please"
SWITCH_PATTERNS = [
    r"\b(speak|talk|reply|answer|chat|baat|bolo|boliye|bol|matladu|matladandi|pesu|pesungal)\s+(in\s+)?{L}",
    r"{L}\s+(mein|me|main|mai|lo|il|la|language|bhasha|please|plz)\b",
    r"\b(in|use|switch to|change to)\s+{L}",
]


def detect_language_switch(text: str, stage: str) -> str | None:
    """
    Does the user want to change the TALK language? Returns the new language code, or None.
      "speak in telugu"          -> "te-IN"   (a short, clear request - any stage)
      "telugu"  (just the name)  -> "te-IN"   (only while telling the story - later, a bare
                                               language name answers "which language for the notice?")
      "my landlord speaks Telugu and refused..." -> None  (that's part of the story)
    """
    code = detect_language_choice(text)
    if not code:
        return None
    lower = _fix_typos(text).lower().strip(" .!?")
    words = len(lower.split())
    if words <= 3 and stage in ("listening", "clarifying"):
        return code                                           # just the language name
    if words <= 8:                                            # a long sentence is probably the story
        for word in LANGUAGE_WORDS[code]:
            for pattern in SWITCH_PATTERNS:
                if re.search(pattern.replace("{L}", re.escape(word.lower())), lower):
                    return code
    return None


def in_language(code: str, english: str, hindi: str, device_id: str | None) -> str:
    """
    Say a fixed sentence in the user's talk language:
    Hindi and English are written by hand; other languages are translated by Sarvam.
    """
    if code == "hi-IN":
        return hindi
    if code == "en-IN" or not code:
        return english
    try:
        return sarvam.translate(english, code, device_id=device_id)
    except Exception:
        return english                     # if translation fails, English is better than silence


# Languages that share one script (so the script alone can't tell them apart)
SAME_SCRIPT = {"hi-IN": {"hi-IN", "mr-IN"}, "mr-IN": {"hi-IN", "mr-IN"}}


def ensure_language(reply: str, talk: str, device_id: str | None) -> str:
    """
    LANGUAGE GUARD: make sure the reply is really in the user's talk language before it is spoken.
    We look at the script the reply is written in (Latin, Devanagari, Telugu...). If it does not match,
    we translate the reply into the talk language. So even if the AI slips, the user hears their language.
    """
    written_in = sarvam.language_of(reply)              # e.g. "te-IN" if it is in Telugu letters, "en-IN" for Latin
    ok = SAME_SCRIPT.get(talk, {talk})
    if written_in in ok:
        return reply
    try:
        print(f"[language guard] reply was in {written_in}, user talks in {talk} -> translating")
        return sarvam.translate(reply, talk, device_id=device_id, source=written_in)
    except Exception:
        return reply


def opener(code: str, device_id: str | None, switching: bool = False) -> str:
    """What the agent says right after a language is chosen - in THAT language."""
    name = sarvam.LANGUAGES[code]
    if switching:
        return in_language(code, prompts.SWITCH_EN.format(name=name), prompts.SWITCH_HI, device_id)
    return in_language(code, prompts.OPENER_EN.format(name=name), prompts.OPENER_HI, device_id)


# ================================================================ small helpers
def deep_merge(base: dict, patch: dict) -> dict:
    """
    Combine two nested dictionaries. Values in `patch` win, but empty values are ignored.
      deep_merge({"sender": {"name": "Priya"}}, {"sender": {"address": "Pune"}})
      -> {"sender": {"name": "Priya", "address": "Pune"}}
    """
    result = dict(base)
    for key, value in (patch or {}).items():
        if value is None or value == "" or value == [] or value == {}:
            continue                                         # the AI left it empty: keep what we had
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)     # go one level deeper
        elif isinstance(value, list) and isinstance(result.get(key), list):
            result[key] = result[key] + [v for v in value if v not in result[key]]  # add new list items
        else:
            result[key] = value
    return result


# Phrases that mean "I will fill the details myself - just write the document" (English, Hinglish, Hindi)
SKIP_PATTERNS = [
    r"\b(fill|filling|write|add)\b.{0,25}\b(myself|my own|by hand|later|on my own)\b",
    r"\b(myself|my own|on my own)\b.{0,25}\b(fill|write|add)\b",
    r"\bjust (write|make|prepare|draft)\b",
    r"\bleave\b.{0,20}\bblank\b|\bblank\b",
    r"khud\s*(se)?\s*(bhar|likh)|\bbas\s+(letter|notice|patra)\b|blank\s*chhod",
    r"खुद\s*(से)?\s*(भर|लिख)|खाली\s*छोड़|बस\s*(पत्र|लेटर|नोटिस)",
]


def detect_skip(text: str) -> bool:
    """Did the user say they will fill in the details themselves? ("I will fill my details, just write the letter")"""
    lower = text.lower()
    return any(re.search(p, lower) for p in SKIP_PATTERNS)


def decide_stage(facts: dict, notice_type: str | None, language: str | None, confirmed: bool,
                 talk_language: str | None = "set", skip: bool = False) -> str:
    """Work out where the conversation is, using rules (not the AI)."""
    if not talk_language:
        return "choose_language"              # first we must know which language to talk in
    if not facts and not notice_type:
        return "listening"                    # the user hasn't told us anything yet
    if not notice_type or (catalog.missing_facts(facts, notice_type) and not skip):
        return "clarifying"                   # still collecting facts (unless the user chose to fill them in by hand)
    if not language:
        return "language"                     # facts complete; ask the notice language
    if not confirmed:
        return "confirm"                      # read the summary back and ask "shall I prepare it?"
    return "ready"                            # time to write the document


def _history(messages: list[dict], n: int = 8) -> str:
    """The last n lines of conversation as plain text for the AI."""
    lines = [f'{"User" if m["role"] == "user" else "NOTIVOX"}: {m["text"]}' for m in messages[-n:]]
    return "\n".join(lines) or "(no conversation yet)"


def _today() -> str:
    """Today's date in India, e.g. '19 November 2026' (so the AI can turn 'kal' into a real date)."""
    return datetime.now(IST).strftime("%d %B %Y")


def summary_for_ui(case: dict) -> dict:
    """The small set of fields the web page needs to show the case."""
    rules = catalog.get(case.get("notice_type"))
    return {
        "case_id": case["_id"],
        "stage": case["stage"],
        "talk_language": case.get("talk_language"),
        "talk_language_name": sarvam.LANGUAGES.get(case.get("talk_language") or "", None),
        "notice_type": case.get("notice_type"),
        "notice_label": rules["label"] if case.get("notice_type") else None,
        "kind": rules["kind"] if case.get("notice_type") else None,      # "notice" or "letter"
        "language": case.get("language"),
        "language_name": sarvam.LANGUAGES.get(case.get("language") or "", None),
        "facts": case.get("facts", {}),
        "skip_details": bool(case.get("skip_details")),
        "missing": [m["label"] for m in catalog.missing_facts(case.get("facts", {}), case.get("notice_type"))]
                   if case.get("notice_type") and not case.get("skip_details") else [],
        "blanks": [m["label"] for m in catalog.missing_facts(case.get("facts", {}), case.get("notice_type"))]
                  if case.get("notice_type") and case.get("skip_details") else [],
        "warnings": rules["warnings"] if case.get("notice_type") else [],
        "has_draft": bool(case.get("draft")),
    }


# ================================================================ the two main functions
def start_case(device_id: str) -> dict:
    """Create a new case and save NOTIVOX's greeting as the first message."""
    case = db.create_case(device_id)
    db.add_message(device_id, case["_id"], "agent", prompts.GREETING)
    case["messages"] = [{"role": "agent", "text": prompts.GREETING}]
    return {"reply": prompts.GREETING, **summary_for_ui(case)}


def set_talk_language(device_id: str, case_id: str, code: str) -> dict:
    """Save the talk language (from a spoken answer or a button on the web page) and say the opener."""
    if code not in sarvam.LANGUAGES:
        raise ValueError("Unsupported language")
    case = db.get_case(device_id, case_id)
    if not case:
        raise ValueError("Case not found")
    switching = bool(case.get("talk_language"))            # True = changing language mid-conversation
    reply = opener(code, device_id, switching)
    # First choice: start listening. A switch later: stay at the same point in the conversation.
    stage = case["stage"] if switching else "listening"
    db.update_case(device_id, case_id, {"talk_language": code, "stage": stage})
    db.add_message(device_id, case_id, "agent", reply)
    case.update({"talk_language": code, "stage": stage})
    return {"reply": reply, **summary_for_ui(case)}


def turn(device_id: str, case_id: str, text: str) -> dict:
    """Handle one thing the user said. Returns the agent's reply plus the updated case summary."""
    case = db.get_case(device_id, case_id)
    if not case:
        raise ValueError("Case not found")
    text = text.strip()[:1500]
    case["messages"].append({"role": "user", "text": text})     # kept in memory; saved together with the reply

    # ---- 0. first question: which language shall we talk in?
    if not case.get("talk_language"):
        db.add_message(device_id, case_id, "user", text)
        code = detect_language_choice(text)
        if code:
            return set_talk_language(device_id, case_id, code)
        db.add_message(device_id, case_id, "agent", prompts.ASK_AGAIN)
        return {"reply": prompts.ASK_AGAIN, **summary_for_ui(case)}

    # ---- 0b. the user wants to switch the talk language ("speak in Telugu")
    switch_to = detect_language_switch(text, case["stage"])
    if switch_to and switch_to != case["talk_language"]:
        db.add_message(device_id, case_id, "user", text)
        return set_talk_language(device_id, case_id, switch_to)

    # ---- 1. ask the AI
    notice_type = case.get("notice_type")
    rules = catalog.get(notice_type)
    skip = bool(case.get("skip_details")) or detect_skip(text)       # user will fill the details by hand
    missing = catalog.missing_facts(case["facts"], notice_type) if notice_type and not skip else []
    talk = case["talk_language"]
    system = prompts.AGENT_SYSTEM.format(
        sign_guide=" ".join(prompts.SIGN_GUIDE), types=catalog.types_for_prompt(), today=_today(),
        talk_language=sarvam.LANGUAGES[talk],
        talk_style=prompts.TALK_STYLE.get(talk, f"{sarvam.LANGUAGES[talk]} in its own script"),
    )
    user = prompts.AGENT_TURN.format(
        skip="YES - do not ask for any more details" if skip else "no",
        stage=case["stage"],
        notice_type=notice_type or "not decided yet",
        detail_keys=", ".join(k for k, _, _ in rules["details"]) if notice_type else "(choose a type first)",
        language=sarvam.LANGUAGES.get(case.get("language") or "", "not chosen yet"),
        facts=json.dumps(case["facts"], ensure_ascii=False),
        missing="; ".join(f'{m["label"]} ({m["hint"]})' for m in missing) or ("nothing" if notice_type else "choose the notice type first"),
        history=_history(case["messages"][:-1]),
        text=text,
    )
    out = sarvam.chat_json(system, user, device_id=device_id, purpose="agent", max_tokens=700)
    if not (out.get("reply") or "").strip():
        # The AI sometimes returns an empty reply - ask once more before giving up
        out = sarvam.chat_json(system, user, device_id=device_id, purpose="agent", max_tokens=700, temperature=0.4)

    # ---- 1b. safety net: the AI noticed a language-switch request our word check missed
    wanted = out.get("talk_language")
    # Only believe it if the user really NAMED that language, and never while we are asking about the DOCUMENT
    # language ("telugu" then means "write the letter in Telugu", not "speak Telugu to me") - unless the user
    # clearly asks to switch ("speak in Telugu").
    asked_doc_language = case["stage"] in ("language", "confirm")
    really_asked = detect_language_choice(text) == wanted and (
        not asked_doc_language or detect_language_switch(text, case["stage"]) == wanted)
    if wanted in sarvam.LANGUAGES and wanted != talk and really_asked:
        patch = out.get("facts_patch") or {}
        db.add_message(device_id, case_id, "user", text)
        if patch:                                        # keep any facts from this message
            db.update_case(device_id, case_id, {"facts": deep_merge(case["facts"], patch)})
        return set_talk_language(device_id, case_id, wanted)

    # ---- 2. merge what the AI found
    facts = deep_merge(case["facts"], out.get("facts_patch") or {})
    new_type = out.get("notice_type")
    if new_type in catalog.TYPES:                       # only accept known types
        notice_type = new_type
    language = case.get("language")
    # The NOTICE language is only accepted once we have actually asked for it (or later, to change it).
    # Before that, a language name means the user is talking about the TALK language, not the notice.
    if out.get("language") in sarvam.LANGUAGES and case["stage"] in ("language", "confirm", "ready", "drafted"):
        language = out["language"]
    if notice_type and not (facts.get("demand") or {}).get("deadline_days"):
        facts = deep_merge(facts, {"demand": {"deadline_days": catalog.get(notice_type)["deadline_days"]}})
    # letters to an office: use the office the user already named as the recipient (never ask twice)
    facts = catalog.apply_recipient_default(facts, notice_type)

    # ---- 3. decide the stage with rules
    # "Yes, prepare it" only counts if we were actually waiting for that confirmation.
    # If the notice was already confirmed/drafted and nothing changed, it stays confirmed.
    # If the user changes a fact or the language after that, we confirm again (and re-draft).
    changed = facts != case["facts"] or language != case.get("language") or notice_type != case.get("notice_type")
    already_done = case["stage"] in ("ready", "drafted") and not changed
    confirmed = already_done or (bool(out.get("user_confirmed")) and case["stage"] == "confirm")
    skip = skip or bool(out.get("skip_details"))
    stage = decide_stage(facts, notice_type, language, confirmed, talk, skip)
    if stage == "ready" and case["stage"] == "drafted" and not changed:
        stage = "drafted"                                # the notice is already written

    # If the reply is still empty, use the backup line - in the user's OWN language (never a fixed Hindi line)
    reply = (out.get("reply") or "").strip() or in_language(talk, prompts.SORRY_EN, prompts.SORRY_HI, device_id)
    reply = ensure_language(reply, talk, device_id)      # never speak a different language than the user chose
    changes = {"facts": facts, "notice_type": notice_type, "language": language, "stage": stage, "skip_details": skip}
    db.save_turn(device_id, case_id, text, reply, changes)     # ONE database write for the whole turn

    case.update(changes)
    return {"reply": reply, **summary_for_ui(case)}
