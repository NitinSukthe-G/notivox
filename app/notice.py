"""
notice.py - turns the collected facts into a formal legal notice.

  draft()        asks the AI to write the notice, checks it, translates it, saves it in the case
  update_draft() saves the user's own edits from the web page
  as_text()      the notice as plain text (for the terminal and for checking)

The finished notice is a dictionary (not a PDF yet - the PDF is made from it in Step 8):
  {"date", "mode", "from_name", "from_address", "to_name", "to_role", "to_address",
   "subject", "opening", "paragraphs": [...], "legal_paragraph", "demand", "consequence",
   "annexures": [...], "deadline_days", "place"}
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor   # do several translations at the same time
from datetime import datetime

from app import catalog, db, prompts, sarvam
from app.agent import IST

# Words that must never appear in a notice (threats / insults). Lower-case.
BAD_WORDS = ["kill", "beat you", "destroy you", "teach you a lesson", "goons", "expose you",
             "bastard", "idiot", "fraudster", "cheater", "thief"]

# Text that looks like a law reference: "Section 12", "Act, 2019", "Sanhita", "Penal Code"
LAW_PATTERN = re.compile(r"Section\s+\d+|\bAct,?\s+\d{4}|Sanhita|Penal Code|Code of Civil", re.I)

# Fields of the notice that contain readable text (these get translated)
TEXT_FIELDS = ["subject", "opening", "legal_paragraph", "demand", "consequence"]
LIST_FIELDS = ["paragraphs", "annexures"]


class DraftRefused(Exception):
    """The AI refused to write this notice (e.g. it was harassment)."""


# ================================================================ checks
def _strip_unknown_laws(text: str, allowed: list[str], problems: list[str]) -> str:
    """Remove any sentence that mentions a law which is NOT on the allowed list."""
    kept = []
    for sentence in re.split(r"(?<=[.;])\s+", text):
        if LAW_PATTERN.search(sentence) and not any(law.lower() in sentence.lower() for law in allowed):
            problems.append(f"Removed a sentence that mentioned an unverified law: \"{sentence[:80]}...\"")
            continue
        kept.append(sentence)
    return " ".join(kept)


def _numbers(text: str) -> set[str]:
    """All numbers with 3+ digits in a text, without commas: 'Rs. 30,000' -> {'30000'}."""
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]{2,}", text)}


def check_notice(notice: dict, facts: dict, allowed_laws: list[str]) -> list[str]:
    """Safety checks. Fixes what it can and returns a list of things the user should look at."""
    problems: list[str] = []

    # 1. laws: only whitelisted laws may appear
    for f in ["subject", "opening", "demand", "consequence"]:
        notice[f] = _strip_unknown_laws(notice.get(f, ""), allowed_laws, problems)
    notice["paragraphs"] = [p for p in (_strip_unknown_laws(p, allowed_laws, problems) for p in notice["paragraphs"]) if p]

    # 2. numbers: every amount/number in the notice should come from the user's facts
    known = _numbers(json.dumps(facts, ensure_ascii=False)) | _numbers(" ".join(allowed_laws))  # law numbers are fine
    for n in sorted(_numbers(as_text(notice)) - known):
        if not (1900 <= int(n) <= 2100):                    # skip years like 2026
            problems.append(f"Please check the number {n} - it was not in what you told me.")

    # 3. tone: no threats or insults
    lower = as_text(notice).lower()
    for w in BAD_WORDS:
        if w in lower:
            problems.append(f"The word \"{w}\" sounds threatening or insulting - please edit it out.")
    return problems


# ================================================================ writing the notice
def _legal_paragraph(laws: list[str], kind: str = "notice") -> str:
    """Build the law paragraph ourselves, from whitelisted laws only."""
    if not laws:
        return ""
    joined = laws[0] if len(laws) == 1 else ", ".join(laws[:-1]) + " and " + laws[-1]
    if kind == "letter":                                     # letters: a polite, neutral sentence
        return f"This is submitted under {joined}."
    return (f"That your aforesaid acts and omissions are contrary to {joined}, "
            "and you are liable for the same.")


def _write_english(case: dict, device_id: str) -> tuple[dict, list[str]]:
    """Ask the AI to write the notice or letter in English, then check it."""
    rules = catalog.get(case["notice_type"])
    kind = rules["kind"]                                     # "notice" or "letter"
    facts = case["facts"]
    raw_deadline = (facts.get("demand") or {}).get("deadline_days") or rules["deadline_days"]
    deadline = int(raw_deadline) if raw_deadline else None   # letters often have no deadline
    deadline_text = f", within {deadline} days of receiving this" if deadline else ""
    today = datetime.now(IST).strftime("%d %B %Y")

    system = prompts.LETTER_SYSTEM if kind == "letter" else prompts.DRAFT_SYSTEM
    out = sarvam.chat_json(
        system.format(label=rules["label"], deadline_text=deadline_text),
        prompts.DRAFT_INPUT.format(label=rules["label"], laws=json.dumps(rules["laws"]),
                                   deadline=f"{deadline} days" if deadline else "none",
                                   today=today, facts=json.dumps(facts, ensure_ascii=False)),
        device_id=device_id, purpose="draft", max_tokens=2500, temperature=0.2,
    )
    if out.get("refused"):
        raise DraftRefused(out.get("reason") or "This document cannot be drafted.")

    laws = [law for law in out.get("legal_basis") or [] if law in rules["laws"]]   # whitelist only
    sender, recipient = facts.get("sender") or {}, facts.get("recipient") or {}
    labels = dict(prompts.LETTER_LABELS if kind == "letter" else prompts.LABELS)
    labels["title"] = rules["title"]                         # e.g. "LEGAL NOTICE", "COMPLAINT", "APPLICATION UNDER ..."
    notice = {
        "kind": kind,
        "date": today,
        "mode": rules["mode"],
        "from_name": sender.get("name", ""),
        "from_address": sender.get("address", ""),
        "to_name": recipient.get("name", ""),
        "to_role": recipient.get("role_or_company", ""),
        "to_address": recipient.get("address", ""),
        "subject": out.get("subject", ""),
        "opening": out.get("opening", ""),
        "paragraphs": [p for p in out.get("paragraphs") or [] if p.strip()],
        "legal_paragraph": _legal_paragraph(laws, kind),
        "laws": laws,
        "demand": out.get("request" if kind == "letter" else "demand", ""),   # letters "request", notices "demand"
        "consequence": "" if kind == "letter" else out.get("consequence", ""),  # letters don't warn
        "annexures": out.get("annexures") or [],
        "deadline_days": deadline,
        "place": facts.get("place", ""),
        "labels": labels,                        # fixed words like "From", "Subject", "Yours faithfully,"
    }
    problems = check_notice(notice, facts, rules["laws"])
    return notice, problems


def translate_notice(notice: dict, language: str, device_id: str) -> dict:
    """Make a translated copy of the notice. Names and addresses stay as they are."""
    jobs = {f: notice[f] for f in TEXT_FIELDS if notice.get(f)}
    for f in LIST_FIELDS:
        for i, item in enumerate(notice.get(f) or []):
            jobs[f"{f}.{i}"] = item
    jobs["mode"] = notice["mode"]
    for name, word in (notice.get("labels") or prompts.LABELS).items():
        jobs[f"labels:{name}"] = word

    # Translate up to 4 pieces at the same time (much faster than one by one)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(zip(jobs, pool.map(lambda t: sarvam.translate(t, language, device_id=device_id), jobs.values())))

    local = json.loads(json.dumps(notice, default=str))     # a deep copy we can change safely
    local.setdefault("labels", dict(prompts.LABELS))
    for key, value in results.items():
        if key.startswith("labels:"):                       # "labels:from" -> local["labels"]["from"]
            local["labels"][key.split(":", 1)[1]] = value
        elif "." in key:                                    # "paragraphs.2" -> local["paragraphs"][2]
            field, i = key.split(".")
            local[field][int(i)] = value
        else:
            local[key] = value
    return local


def _spoken_guide(talk_language: str | None, device_id: str) -> str:
    """The short 'your notice is ready, sign here...' message, in the language the user talks in."""
    if talk_language == "hi-IN":
        return prompts.SIGN_SPOKEN_HI
    if not talk_language or talk_language == "en-IN":
        return prompts.SIGN_SPOKEN_EN
    try:
        return sarvam.translate(prompts.SIGN_SPOKEN_EN, talk_language, device_id=device_id)
    except Exception:
        return prompts.SIGN_SPOKEN_EN


def draft(device_id: str, case_id: str) -> dict:
    """Write, check, translate and save the notice. Returns {"notice", "local", "problems", ...}."""
    case = db.get_case(device_id, case_id)
    if not case:
        raise ValueError("Case not found")
    if case["stage"] not in ("ready", "drafted"):
        raise ValueError("The case is not ready yet - finish the conversation first.")

    english, problems = _write_english(case, device_id)
    language = case.get("language") or "en-IN"
    local = translate_notice(english, language, device_id) if language != "en-IN" else None

    # signing steps, with the last step ("how to send") specific to this document type
    sign_guide = prompts.SIGN_GUIDE[:-1] + [catalog.get(case["notice_type"])["how_to_send"]]
    result = {"notice": english, "local": local, "language": language, "problems": problems,
              "sign_guide": sign_guide, "spoken": _spoken_guide(case.get("talk_language"), device_id),
              "created_at": db.now()}
    db.update_case(device_id, case_id, {"draft": result, "stage": "drafted"})
    return result


def set_language(device_id: str, case_id: str, code: str) -> dict:
    """
    The user picked the NOTICE language from the dropdown.
      - before the notice is written: just remember it (the agent then won't ask for it)
      - after it is written: re-translate the notice into the new language right away
    Returns the case summary (+ the new draft if it was re-translated).
    """
    from app import agent                                    # imported here to avoid a circular import
    if code not in sarvam.LANGUAGES:
        raise ValueError("Unsupported language")
    case = db.get_case(device_id, case_id)
    if not case:
        raise ValueError("Case not found")

    changes = {"language": code}
    if case["stage"] == "language":                          # we were just about to ask -> move on to the summary
        changes["stage"] = agent.decide_stage(case["facts"], case.get("notice_type"), code, False, case.get("talk_language"))
    draft_out = None
    if case.get("draft"):                                    # already written -> translate again
        d = case["draft"]
        d["language"] = code
        d["local"] = translate_notice(d["notice"], code, device_id) if code != "en-IN" else None
        changes["draft"] = d
        draft_out = d
    db.update_case(device_id, case_id, changes)
    case.update(changes)
    return {**agent.summary_for_ui(case), "draft": draft_out}


def update_draft(device_id: str, case_id: str, which: str, notice: dict) -> dict:
    """Save the user's edits. which = "notice" (English) or "local" (translated copy)."""
    case = db.get_case(device_id, case_id)
    if not case or not case.get("draft"):
        raise ValueError("No draft to edit yet")
    if which not in ("notice", "local"):
        raise ValueError("which must be 'notice' or 'local'")
    old = case["draft"][which] or {}
    allowed = set(TEXT_FIELDS + LIST_FIELDS + ["from_name", "from_address", "to_name", "to_role",
                                               "to_address", "place", "date"])
    cleaned = {k: v for k, v in notice.items() if k in allowed}   # ignore unknown fields
    case["draft"][which] = {**old, **cleaned}
    db.update_case(device_id, case_id, {"draft": case["draft"]})
    return case["draft"]


# ================================================================ plain text
def as_text(n: dict) -> str:
    """The notice as plain text (used by the terminal and by check_notice)."""
    lines = [
        (n.get("labels") or {}).get("title", "LEGAL NOTICE"), n.get("mode", ""), f"Date: {n.get('date', '')}", "",
        "From:", n.get("from_name", ""), n.get("from_address", ""), "",
        "To:", n.get("to_name", ""), n.get("to_role", ""), n.get("to_address", ""), "",
        f"Subject: {n.get('subject', '')}", "", "Sir/Madam,", "", n.get("opening", ""), "",
    ]
    paras = list(n.get("paragraphs") or []) + [n.get("legal_paragraph", "")]
    lines += [f"{i}. {p}" for i, p in enumerate([p for p in paras if p], 1)]
    lines += ["", n.get("demand", ""), "", n.get("consequence", ""), ""]
    if n.get("annexures"):
        lines += ["Annexures:"] + [f"  {i}. {a}" for i, a in enumerate(n["annexures"], 1)] + [""]
    lines += ["Yours faithfully,", "", "[ Sign here ]", n.get("from_name", ""),
              f"Place: {n.get('place', '')}", f"Date: {n.get('date', '')}"]
    return "\n".join(lines)
