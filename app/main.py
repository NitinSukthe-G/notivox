"""
main.py - the web server. It connects the browser to everything we built.

The browser never talks to Sarvam or MongoDB directly. It calls OUR server at
addresses like /api/cases/abc123/message, and the server does the work using
agent.py, notice.py, pdf.py, sarvam.py and db.py.

Each address + method (GET = read, POST = create/do, PUT = change, DELETE = remove)
is called a "route". FastAPI turns each Python function below into one route.

Start the server:   uvicorn app.main:app --reload
Then open:          http://localhost:8000        (the app)
                    http://localhost:8000/docs   (a built-in page to try every route)
"""
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import agent, catalog, config, db, notice, pdf, prompts, sarvam


# ================================================================ start-up
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once when the server starts (before any request)."""
    problems = config.check()
    if problems:
        print("WARNING - settings problems:", problems)
    else:
        db.ensure_indexes()
    yield                                   # the server runs while we are "inside" here


app = FastAPI(title="NOTIVOX", description="Voice agent that drafts legal notices", lifespan=lifespan)


# ================================================================ who is calling?
DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def device(header_value: str | None, query_value: str | None = None) -> str:
    """
    Every browser sends its anonymous device id in the X-User-Id header
    (or as ?u=... in a download link, because plain links can't send headers).
    We check it looks like an id, so nobody can send something strange.
    """
    value = header_value or query_value or ""
    if not DEVICE_RE.match(value):
        raise HTTPException(status_code=400, detail="Missing or invalid device id")
    return value


def load_case(device_id: str, case_id: str) -> dict:
    """Get a case or answer '404 Not Found'."""
    case = db.get_case(device_id, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


# ================================================================ friendly errors
# Instead of crashing, turn our own errors into clear JSON answers for the web page.
@app.exception_handler(sarvam.BudgetExceeded)
def on_budget(request: Request, exc: Exception):
    return JSONResponse(status_code=402, content={"detail": str(exc), "fallback": True})


@app.exception_handler(sarvam.SarvamError)
def on_sarvam(request: Request, exc: Exception):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(sarvam.VoiceUnavailable)
def on_no_voice(request: Request, exc: Exception):
    # 409 = "can't do this one" -> the page shows the text without speaking (never another voice)
    return JSONResponse(status_code=409, content={"detail": str(exc), "no_voice": True})


@app.exception_handler(notice.DraftRefused)
def on_refused(request: Request, exc: Exception):
    return JSONResponse(status_code=422, content={"detail": str(exc), "refused": True})


@app.exception_handler(ValueError)
def on_value(request: Request, exc: Exception):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ================================================================ shapes of incoming data
# Pydantic "models" describe what the browser must send. FastAPI checks it for us
# (e.g. text must be 1 to 1500 characters) and rejects anything else automatically.
class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


class LanguageIn(BaseModel):
    code: str


class DraftEditIn(BaseModel):
    which: str = "notice"          # "notice" (English) or "local" (translated copy)
    notice: dict


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    language: str | None = None


# ================================================================ general routes
@app.get("/api/health")
def health():
    """Is the server alive? (Used to check the server is running.)"""
    return {"ok": True}


@app.get("/api/config")
def get_config():
    """Things the web page needs to know at start: languages and whether Sarvam voice is available."""
    return {"languages": sarvam.LANGUAGES, "voice": sarvam.voice_available(), "sign_guide": prompts.SIGN_GUIDE}


# ================================================================ cases
@app.post("/api/cases")
def new_case(x_user_id: str | None = Header(None)):
    """Start a new conversation. Returns the greeting (which asks the talk language)."""
    return agent.start_case(device(x_user_id))


@app.get("/api/cases")
def my_cases(x_user_id: str | None = Header(None)):
    """List this device's recent cases (for a 'my notices' list)."""
    d = device(x_user_id)
    return [
        {"case_id": c["_id"], "stage": c["stage"], "updated_at": c["updated_at"],
         "notice_label": catalog.get(c.get("notice_type"))["label"] if c.get("notice_type") else None,
         "against": ((c.get("facts") or {}).get("recipient") or {}).get("name")}
        for c in db.list_cases(d)
    ]


@app.get("/api/cases/{case_id}")
def open_case(case_id: str, x_user_id: str | None = Header(None)):
    """Everything about one case: summary, conversation and draft."""
    case = load_case(device(x_user_id), case_id)
    return {**agent.summary_for_ui(case), "messages": case.get("messages", []), "draft": case.get("draft")}


@app.delete("/api/cases/{case_id}")
def remove_case(case_id: str, x_user_id: str | None = Header(None)):
    """Delete a case permanently (the user's data, the user's choice)."""
    if not db.delete_case(device(x_user_id), case_id):
        raise HTTPException(status_code=404, detail="Case not found")
    return {"deleted": True}


@app.post("/api/cases/{case_id}/language")
def choose_language(case_id: str, body: LanguageIn, x_user_id: str | None = Header(None)):
    """The user tapped a language button."""
    return agent.set_talk_language(device(x_user_id), case_id, body.code)


@app.post("/api/cases/{case_id}/notice-language")
def choose_notice_language(case_id: str, body: LanguageIn, x_user_id: str | None = Header(None)):
    """The user picked the language of the NOTICE from the dropdown (re-translates if already written)."""
    return notice.set_language(device(x_user_id), case_id, body.code)


@app.post("/api/cases/{case_id}/message")
def send_message(case_id: str, body: MessageIn, x_user_id: str | None = Header(None)):
    """The user typed something."""
    return agent.turn(device(x_user_id), case_id, body.text)


@app.post("/api/cases/{case_id}/voice")
async def send_voice(case_id: str, file: UploadFile = File(...), duration: float = Form(0),
                     x_user_id: str | None = Header(None)):
    """The user spoke. We turn the recording into text, then handle it like a typed message."""
    d = device(x_user_id)
    case = load_case(d, case_id)
    audio = await file.read()                       # "await" = wait for the upload without blocking others
    if not audio:
        raise HTTPException(status_code=400, detail="Empty recording")
    if len(audio) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Recording too long - please keep it under 30 seconds")
    # "audio/webm;codecs=opus" -> "audio/webm": Sarvam rejects the ";codecs=..." part.
    # (The web page now sends WAV, which Sarvam always accepts.)
    content_type = (file.content_type or "audio/wav").split(";")[0].strip()
    # Tell the speech recogniser which language the user talks in (unknown before they choose one)
    text, lang = sarvam.speech_to_text(audio, file.filename or "voice.wav", content_type,
                                       device_id=d, duration_s=duration, language_code=case.get("talk_language"))
    if not text:
        return {"transcript": "", "reply": None, "detail": "I couldn't hear anything. Please try again."}
    result = agent.turn(d, case_id, text)
    return {"transcript": text, "heard_language": lang, **result}


# ================================================================ the notice
@app.post("/api/cases/{case_id}/draft")
def make_draft(case_id: str, x_user_id: str | None = Header(None)):
    """Write the notice (only after the user confirmed the summary). Takes ~10-30 seconds."""
    return notice.draft(device(x_user_id), case_id)


@app.put("/api/cases/{case_id}/draft")
def edit_draft(case_id: str, body: DraftEditIn, x_user_id: str | None = Header(None)):
    """Save the user's edits to the notice text."""
    return notice.update_draft(device(x_user_id), case_id, body.which, body.notice)


@app.get("/api/cases/{case_id}/notice.pdf")
def download_pdf(case_id: str, u: str | None = Query(None), x_user_id: str | None = Header(None)):
    """Build the PDF fresh from the saved draft and send it as a download."""
    case = load_case(device(x_user_id, u), case_id)
    if not case.get("draft"):
        raise HTTPException(status_code=400, detail="The notice has not been written yet")
    rules = catalog.get(case.get("notice_type"))
    data = pdf.build_pdf(case["draft"], rules["warnings"], rules["how_to_send"])
    kind = "legal-notice" if rules["kind"] == "notice" else "letter"
    name = f"NOTIVOX-{kind}-{case_id[:8]}.pdf"
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ================================================================ voice
@app.post("/api/tts")
def speak(body: SpeakIn, x_user_id: str | None = Header(None)):
    """Turn the agent's reply into Ishita's voice (WAV audio). Always Ishita - never another voice."""
    lang = body.language if body.language in sarvam.LANGUAGES else None
    wav = sarvam.text_to_speech(body.text, device_id=device(x_user_id), language_code=lang)
    return Response(content=wav, media_type="audio/wav")


# ================================================================ the web page
# Everything that is not /api/... is a file from the static/ folder (index.html, app.js, app.css).
# This must come LAST, so it doesn't hide the /api routes above.
if config.STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
else:
    @app.get("/")
    def no_page_yet():
        return {"message": "NOTIVOX server is running, but the static/ folder with the web page was not found. Try /docs"}
