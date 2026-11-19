"""
db.py - everything that talks to MongoDB lives here.

MongoDB stores data as "documents" (like Python dictionaries) inside
"collections" (like folders). NOTIVOX uses two collections:

  cases      -> one document per legal-notice conversation
  spend_log  -> one document per paid Sarvam call (so we can stop before credits run out)

Other files never touch MongoDB directly - they call the small functions below.
"""
import uuid                                   # makes random unique ids
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient, DESCENDING   # the MongoDB driver

from app import config

_client = None   # the connection to MongoDB; created once, then re-used


# ================================================================ connection
def get_db():
    """Return the NOTIVOX database. Connects the first time, then re-uses the connection."""
    global _client
    if _client is None:
        if not config.MONGODB_URI:
            raise RuntimeError("MONGODB_URI is missing in .env")
        # serverSelectionTimeoutMS: give up after 8 seconds instead of hanging forever
        _client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=8000)
    return _client[config.MONGODB_DB]


def cases():
    """The 'cases' collection."""
    return get_db()["cases"]


def spend_log():
    """The 'spend_log' collection."""
    return get_db()["spend_log"]


def ping() -> bool:
    """Ask MongoDB 'are you there?'. Returns True if it answers."""
    get_db().command("ping")
    return True


def ensure_indexes():
    """
    An index is like the index at the back of a book: it lets MongoDB find
    documents fast without reading every one. Safe to call many times.
    """
    cases().create_index([("device_id", 1), ("updated_at", DESCENDING)])
    spend_log().create_index([("ts", DESCENDING)])
    spend_log().create_index([("device_id", 1), ("ts", DESCENDING)])


def now() -> datetime:
    """Current time in UTC (one world clock, so times never get confused)."""
    return datetime.now(timezone.utc)


# ================================================================ cases
def create_case(device_id: str) -> dict:
    """Make a new, empty case for this device and save it. Returns the case."""
    case = {
        "_id": uuid.uuid4().hex,      # a random id like "3f9c1e..." (also used in the web address)
        "device_id": device_id,       # which browser/phone owns this case
        "created_at": now(),
        "updated_at": now(),
        "stage": "choose_language",   # where the conversation is (choose_language -> listening -> ... -> ready)
        "talk_language": None,        # language the agent SPEAKS in, e.g. "hi-IN" (asked first)
        "notice_type": None,          # e.g. "deposit_refund" (decided by the agent later)
        "language": None,             # language the NOTICE is written in, e.g. "en-IN", "hi-IN"
        "facts": {},                  # everything the user has told us, in structured form
        "messages": [],               # the conversation: [{"role": "user"/"agent", "text": ..., "ts": ...}]
        "draft": None,                # the finished notice text (Step 7)
    }
    cases().insert_one(case)
    return case


def get_case(device_id: str, case_id: str) -> dict | None:
    """Find one case. We also check device_id so nobody can open someone else's case."""
    return cases().find_one({"_id": case_id, "device_id": device_id})


def list_cases(device_id: str, limit: int = 20) -> list[dict]:
    """This device's most recent cases (newest first), without the long message lists."""
    cursor = (
        cases()
        .find({"device_id": device_id}, {"messages": 0})
        .sort("updated_at", DESCENDING)
        .limit(limit)
    )
    return list(cursor)


def update_case(device_id: str, case_id: str, changes: dict) -> None:
    """Change some fields of a case, e.g. update_case(d, c, {"stage": "ready"})."""
    changes = {**changes, "updated_at": now()}
    cases().update_one({"_id": case_id, "device_id": device_id}, {"$set": changes})


def add_message(device_id: str, case_id: str, role: str, text: str) -> None:
    """Append one line of conversation. Keeps only the latest 60 messages."""
    msg = {"role": role, "text": text, "ts": now()}
    cases().update_one(
        {"_id": case_id, "device_id": device_id},
        {"$push": {"messages": {"$each": [msg], "$slice": -60}}, "$set": {"updated_at": now()}},
    )


def delete_case(device_id: str, case_id: str) -> bool:
    """Delete a case for good. Returns True if something was deleted."""
    return cases().delete_one({"_id": case_id, "device_id": device_id}).deleted_count == 1


# ================================================================ spending
def log_spend(device_id: str | None, kind: str, inr: float, meta: dict | None = None) -> None:
    """Remember the cost of one Sarvam call (kind = "llm", "stt", "tts", "translate")."""
    spend_log().insert_one(
        {"ts": now(), "device_id": device_id, "kind": kind, "inr": round(inr, 5), "meta": meta or {}}
    )


def total_spend() -> float:
    """Total rupees spent on Sarvam so far (all users together)."""
    result = list(spend_log().aggregate([{"$group": {"_id": None, "sum": {"$sum": "$inr"}}}]))
    return float(result[0]["sum"]) if result else 0.0


def device_calls_today(device_id: str) -> int:
    """How many paid Sarvam calls this device made in the last 24 hours."""
    since = now() - timedelta(hours=24)
    return spend_log().count_documents({"device_id": device_id, "ts": {"$gte": since}})


# ================================================================ self-test
# Run:  python -m app.db
if __name__ == "__main__":
    print("1. Connecting to MongoDB ...")
    ping()
    print("   connected to database:", config.MONGODB_DB)

    ensure_indexes()
    print("2. Indexes ready")

    test_device = "test-device-123"
    case = create_case(test_device)
    print("3. Created a test case with id:", case["_id"])

    add_message(test_device, case["_id"], "user", "Mere landlord ne deposit wapas nahi kiya")
    update_case(test_device, case["_id"], {"stage": "clarifying"})
    loaded = get_case(test_device, case["_id"])
    print("4. Read it back -> stage:", loaded["stage"], "| messages:", len(loaded["messages"]))
    print("   first message:", loaded["messages"][0]["text"])

    delete_case(test_device, case["_id"])
    print("5. Deleted the test case (exists now?):", get_case(test_device, case["_id"]) is not None)
    print("Spent on Sarvam so far: Rs", round(total_spend(), 2))
    print("MongoDB OK")
