"""
cli.py - chat with NOTIVOX in the terminal (no web page needed).

Run:   python cli.py
Type what you would say. Commands:  /facts  (show collected facts)   /quit  (exit)
"""
import json
import sys

from app import agent, db, notice

sys.stdout.reconfigure(encoding="utf-8")      # so Hindi/Tamil text prints correctly on Windows

DEVICE = "terminal-user"


def main():
    db.ensure_indexes()
    result = agent.start_case(DEVICE)
    case_id = result["case_id"]
    print(f"\n[new case {case_id[:8]}]  type /facts or /quit\n")
    print("NOTIVOX:", result["reply"])

    while True:
        try:
            text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/facts":
            print(json.dumps(db.get_case(DEVICE, case_id)["facts"], indent=2, ensure_ascii=False))
            continue

        result = agent.turn(DEVICE, case_id, text)
        print("\nNOTIVOX:", result["reply"])
        info = f"[stage: {result['stage']}"
        if result["talk_language_name"]:
            info += f" | talking in: {result['talk_language_name']}"
        if result["notice_label"]:
            info += f" | type: {result['notice_label']}"
        if result["missing"]:
            info += f" | still missing: {', '.join(result['missing'])}"
        if result["language_name"]:
            info += f" | language: {result['language_name']}"
        print(info + "]")
        for w in result["warnings"]:
            print("  ! " + w)
        if result["stage"] == "ready":
            print("\n[READY - writing the notice, please wait ...]\n")
            try:
                d = notice.draft(DEVICE, case_id)
            except notice.DraftRefused as e:
                print("Could not draft this notice:", e)
                continue
            print(notice.as_text(d["notice"]))
            if d["local"]:
                print("\n----- translated copy -----\n")
                print(notice.as_text(d["local"]))
            for p in d["problems"]:
                print("  ! CHECK:", p)
            print("\n[Notice saved. You can keep chatting - e.g. ask where to sign - or type /quit]")


if __name__ == "__main__":
    main()
