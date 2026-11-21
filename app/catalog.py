"""
catalog.py - the "rule book" of document types: LEGAL NOTICES and LEGAL LETTERS.

  kind = "notice" -> a formal warning before legal action ("That ..." paragraphs, a demand + deadline)
  kind = "letter" -> a formal complaint / application / request (police complaint, RTI, bank complaint...)

For every type, this file says:
  - what it is called and some example situations (helps the AI recognise it)
  - which facts we MUST collect before we can write it
  - which laws it is ALLOWED to mention (a whitelist, so the AI never invents laws)
  - how many days the other side gets to respond (None = no deadline)
  - warnings the user must hear (e.g. strict time limits)
  - its title, how it is sent ("mode"), and the last "how to send" step for the checklist page

The AI talks naturally; this file makes sure nothing important is forgotten.
"""

import json

# ---------------------------------------------------------------- facts every document needs
# (path in the facts dictionary, label we show, hint for the AI on how to ask)
COMMON_REQUIRED = [
    ("sender.name", "Your full name", "the user's own full name"),
    ("sender.address", "Your address", "the user's full postal address with PIN code"),
    ("recipient.name", "Addressed to", "full name or designation of the person, company or office it is addressed to"),
    ("recipient.address", "Their address", "full postal address of the recipient (for post or delivery)"),
    ("incident.summary", "What happened", "the story in the user's words"),
    ("demand.what", "What you want", "the outcome the user wants: refund, payment, action, information, stop doing something..."),
]

# Values every type gets unless it says otherwise
DEFAULTS = {
    "kind": "notice",
    "title": "LEGAL NOTICE",
    "mode": "By Registered Post A.D. / Speed Post",
    "how_to_send": "Send one signed copy by Registered Post A.D. or Speed Post. Keep the receipt and tracking number.",
    "warnings": [],
}

# ---------------------------------------------------------------- the document types
TYPES = {
    "deposit_refund": {
        "label": "Security deposit refund",
        "examples": "landlord not returning rent deposit after vacating; PG/hostel deposit kept",
        "details": [
            ("deposit_amount", "Deposit amount", "how much deposit was paid"),
            ("vacated_date", "Date you vacated", "when the user left and handed over the keys"),
        ],
        "laws": [
            "the terms of the Rent / Leave and Licence Agreement between the parties",
            "Section 73 of the Indian Contract Act, 1872",
        ],
        "deadline_days": 15,
        "warnings": [],
    },
    "unpaid_salary": {
        "label": "Unpaid salary / dues",
        "examples": "employer not paying salary, full and final settlement, bonus or reimbursement held back",
        "details": [
            ("amount_due", "Amount due", "total unpaid amount"),
            ("period", "Unpaid period", "which months or which dues are unpaid"),
            ("job_role", "Your job and joining/leaving date", "designation and dates of employment"),
        ],
        "laws": ["the Code on Wages, 2019", "Section 73 of the Indian Contract Act, 1872"],
        "deadline_days": 15,
        "warnings": ["You can also file a complaint with the Labour Commissioner of your area."],
    },
    "defective_product": {
        "label": "Defective product (consumer)",
        "examples": "phone/appliance/vehicle stopped working, seller or brand refusing repair, replacement or refund",
        "details": [
            ("product", "Product and purchase date", "what was bought, when, and the price"),
            ("defect", "The defect", "what is wrong and what the seller/brand said"),
        ],
        "laws": ["Section 2(10) of the Consumer Protection Act, 2019", "Section 35 of the Consumer Protection Act, 2019"],
        "deadline_days": 15,
        "warnings": [],
    },
    "service_deficiency": {
        "label": "Poor or failed service (consumer)",
        "examples": "bank, telecom, courier, airline, hospital, coaching, travel agent or repair shop gave bad service",
        "details": [
            ("service", "Service and date", "which service, when, and what was paid"),
            ("deficiency", "What went wrong", "how the service failed"),
        ],
        "laws": ["Section 2(11) of the Consumer Protection Act, 2019", "Section 35 of the Consumer Protection Act, 2019"],
        "deadline_days": 15,
        "warnings": [],
    },
    "refund_non_delivery": {
        "label": "Online order: refund / not delivered",
        "examples": "e-commerce order not delivered, wrong item, refund promised but not paid",
        "details": [
            ("order", "Order details", "website/app, order number, date and amount"),
        ],
        "laws": ["the Consumer Protection Act, 2019", "the Consumer Protection (E-Commerce) Rules, 2020"],
        "deadline_days": 15,
        "warnings": ["You can also complain on the National Consumer Helpline (1915)."],
    },
    "money_recovery": {
        "label": "Money lent / payment due",
        "examples": "friend or relative not returning a loan; client not paying an invoice",
        "details": [
            ("amount", "Amount", "how much is owed"),
            ("when_given", "When and how it was given", "date and mode: cash, UPI, bank transfer, invoice"),
        ],
        "laws": ["Section 73 of the Indian Contract Act, 1872", "Order XXXVII of the Code of Civil Procedure, 1908"],
        "deadline_days": 15,
        "warnings": [],
    },
    "cheque_bounce": {
        "label": "Cheque bounce",
        "examples": "a cheque given to the user was returned unpaid by the bank",
        "details": [
            ("cheque", "Cheque details", "cheque number, date, amount and bank"),
            ("return_date", "Date of bank's return memo", "when the user got the bank's 'cheque returned' memo"),
            ("reason", "Reason for return", "e.g. funds insufficient"),
        ],
        "laws": ["Section 138 of the Negotiable Instruments Act, 1881"],
        "deadline_days": 15,
        "warnings": [
            "This notice must be sent within 30 days of receiving the bank's return memo.",
            "Cheque bounce cases have strict time limits; showing the notice to an advocate is strongly recommended.",
        ],
    },
    "builder_delay": {
        "label": "Builder: flat possession delayed",
        "examples": "builder has not given possession of a flat/plot by the promised date",
        "details": [
            ("project", "Project and unit", "project name, flat/unit number"),
            ("promised_date", "Promised possession date", "date in the agreement"),
            ("amount_paid", "Amount paid", "how much the user has paid so far"),
        ],
        "laws": ["Section 18 of the Real Estate (Regulation and Development) Act, 2016",
                 "the Consumer Protection Act, 2019"],
        "deadline_days": 30,
        "warnings": ["You can also file a complaint with your state's RERA authority."],
    },
    "insurance_claim": {
        "label": "Insurance claim rejected / delayed",
        "examples": "health, vehicle or life insurance claim rejected or not settled",
        "details": [
            ("policy", "Policy details", "insurer, policy number, type"),
            ("claim", "Claim details", "claim number, date, amount and the reason given for rejection"),
        ],
        "laws": ["the Consumer Protection Act, 2019", "the Insurance Ombudsman Rules, 2017"],
        "deadline_days": 15,
        "warnings": ["Also complain to the insurer's grievance officer; if unresolved, approach the Insurance Ombudsman."],
    },
    "nuisance": {
        "label": "Neighbour nuisance / encroachment",
        "examples": "noise, water leakage, blocking the way, construction on the user's land",
        "details": [
            ("problem", "The problem", "what the neighbour is doing, since when, and its effect"),
        ],
        "laws": ["the law of torts relating to nuisance", "Section 270 of the Bharatiya Nyaya Sanhita, 2023"],
        "deadline_days": 15,
        "warnings": [],
    },
    "defamation": {
        "label": "Defamation (false statements about you)",
        "examples": "someone spreading false statements that harm the user's reputation, online or offline",
        "details": [
            ("statement", "The statement", "what was said or posted, where, and when"),
            ("harm", "The harm", "how it affected the user"),
        ],
        "laws": ["Section 356 of the Bharatiya Nyaya Sanhita, 2023"],
        "deadline_days": 7,
        "warnings": ["Defamation is a serious matter; showing the notice to an advocate is strongly recommended."],
    },
    "reply_to_notice": {
        "label": "Reply to a legal notice you received",
        "examples": "the user RECEIVED a legal notice and wants to answer it",
        "title": "REPLY TO LEGAL NOTICE",
        "details": [
            ("notice_received", "The notice you received", "who sent it, its date, and when the user received it"),
            ("allegations", "What they claim", "the main claims or demands in that notice"),
            ("your_side", "Your side", "the user's facts and why the claims are wrong"),
        ],
        "laws": [],
        "deadline_days": None,
        "warnings": ["Reply soon, ideally within the time given in their notice.",
                     "Showing their notice and your reply to an advocate is recommended."],
    },
    "other": {
        "label": "Other legal notice",
        "examples": "a legal notice that does not fit the types above",
        "details": [
            ("key_details", "Key dates and amounts", "important dates, amounts and documents"),
        ],
        "laws": ["Section 73 of the Indian Contract Act, 1872"],
        "deadline_days": 15,
    },

    # ============================================================ LEGAL LETTERS
    "police_complaint": {
        "kind": "letter",
        "label": "Police complaint (request to register an FIR)",
        "examples": "theft, cheating or fraud, threats, harassment, missing documents - a written complaint to the police station",
        "title": "COMPLAINT",
        "mode": "By hand / By Registered Post",
        "details": [
            ("police_station", "Police station", "name and address of the police station of the area where it happened"),
            ("incident_time_place", "When and where", "date, time and place of the incident"),
            ("accused", "Who did it", "name or description of the person(s), if known"),
        ],
        "laws": ["Section 173 of the Bharatiya Nagarik Suraksha Sanhita, 2023"],
        "deadline_days": None,
        # the letter goes to the police station: fill the recipient from what the user already said
        "recipient_default": {"name": "The Station House Officer", "from_detail": "police_station"},
        "warnings": ["In an emergency, call 112 first.",
                     "For online or money fraud, also call 1930 and report it on cybercrime.gov.in."],
        "how_to_send": "Submit it at the police station in person and ask for a signed and stamped acknowledgement on "
                       "your copy. If they refuse to register an FIR, you can send it to the Superintendent of Police by post.",
    },
    "rti_application": {
        "kind": "letter",
        "label": "RTI application (information from a government office)",
        "examples": "the user wants information or documents from a government department, e.g. status of an application, file notings, expenses",
        "title": "APPLICATION UNDER SECTION 6(1) OF THE RIGHT TO INFORMATION ACT, 2005",
        "mode": "By Registered Post / By hand",
        "details": [
            ("department", "Government office", "name and address of the public authority / department"),
            ("information", "Information wanted", "the exact information or documents wanted, as separate points"),
            ("period", "Time period", "the period the information is about, if any"),
        ],
        "laws": ["Section 6(1) of the Right to Information Act, 2005", "Section 7(1) of the Right to Information Act, 2005"],
        "deadline_days": 30,
        "recipient_default": {"name": "The Public Information Officer", "from_detail": "department"},
        "warnings": ["Attach the Rs. 10 application fee (Indian Postal Order or demand draft, or as your state allows). "
                     "BPL card holders pay no fee - attach a copy of the card instead."],
        "how_to_send": "Attach the Rs. 10 fee and send it to the Public Information Officer by registered post, or submit "
                       "it in person and get an acknowledgement. For central government offices you can also apply at rtionline.gov.in.",
    },
    "bank_complaint": {
        "kind": "letter",
        "label": "Complaint to a bank",
        "examples": "unauthorised or failed transaction, wrong charges, loan or card problems, bank not responding",
        "title": "COMPLAINT",
        "mode": "By hand / By email / By Registered Post",
        "details": [
            ("account", "Account and branch", "bank name, branch, and last 4 digits of the account or card"),
            ("transaction", "Transaction details", "date, amount, reference number and what went wrong"),
        ],
        "laws": ["the Reserve Bank - Integrated Ombudsman Scheme, 2021"],
        "deadline_days": 30,
        "warnings": ["Report an unauthorised transaction to your bank immediately - reporting within 3 working days limits your liability.",
                     "If the bank does not resolve it within 30 days, complain to the RBI Ombudsman at cms.rbi.org.in."],
        "how_to_send": "Give it at your branch and get a stamped acknowledgement, or email it to the bank's grievance "
                       "officer and keep the complaint number.",
    },
    "company_complaint": {
        "kind": "letter",
        "label": "Complaint to a company / customer care",
        "examples": "a first written complaint to a company before sending a legal notice",
        "title": "COMPLAINT",
        "mode": "By email / By Registered Post",
        "details": [
            ("order_or_service", "Product or service", "what, when, order or invoice number, and amount"),
            ("problem", "The problem", "what went wrong and what the company said"),
        ],
        "laws": ["the Consumer Protection Act, 2019"],
        "deadline_days": 15,
        "warnings": ["You can also call the National Consumer Helpline (1915)."],
        "how_to_send": "Email it to the company's grievance officer (the address is on their website) and keep their "
                       "reply, or send it by registered post.",
    },
    "authority_complaint": {
        "kind": "letter",
        "label": "Complaint to a government office or authority",
        "examples": "municipal corporation (garbage, water, roads, drainage), electricity board, college or school administration, a senior officer",
        "title": "COMPLAINT",
        "mode": "By hand / By Registered Post",
        "details": [
            ("office", "Office and officer", "which department and officer it is addressed to"),
            ("problem", "The problem", "what the problem is, since when, and any earlier complaints"),
        ],
        "laws": [],
        "deadline_days": None,
        "recipient_default": {"from_detail": "office"},
        "how_to_send": "Submit it at the office and get a stamped acknowledgement on your copy, or send it by registered post.",
    },
    "other_letter": {
        "kind": "letter",
        "label": "Other formal letter",
        "examples": "any other formal or legal letter: a request, application, complaint or authorisation",
        "title": "",
        "mode": "",
        "details": [
            ("key_details", "Key details", "important dates, amounts and documents"),
        ],
        "laws": [],
        "deadline_days": None,
        "how_to_send": "Give or send it to the addressee and keep a copy with their acknowledgement.",
    },
}


# ---------------------------------------------------------------- helper functions
def get(notice_type: str | None) -> dict:
    """Return the rules for a type, with DEFAULTS filled in (falls back to 'other' if unknown)."""
    return {**DEFAULTS, **TYPES.get(notice_type or "", TYPES["other"])}


def types_for_prompt() -> str:
    """A short list of all types, written into the AI's instructions so it can choose one."""
    lines = []
    for kind, heading in (("notice", "LEGAL NOTICES (a formal warning before legal action)"),
                          ("letter", "LEGAL LETTERS (complaints, applications, requests)")):
        lines.append(heading + ":")
        for key in TYPES:
            t = get(key)
            if t["kind"] == kind:
                lines.append(f'- "{key}": {t["label"]} (e.g. {t["examples"]})')
    return "\n".join(lines)


def _value(facts: dict, path: str):
    """Read a nested value: _value(facts, "sender.name") -> facts["sender"]["name"] (or None)."""
    node = facts
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def apply_recipient_default(facts: dict, notice_type: str | None) -> dict:
    """
    Letters to an office are addressed to the office itself. If the user already told us the office
    (e.g. "SR Nagar Police Station, Hyderabad 500038") we use it as the recipient, so we never ask twice:
      police complaint -> "The Station House Officer", <police station and its address>
      RTI application  -> "The Public Information Officer", <department and its address>
    Only EMPTY fields are filled; anything the user said is kept.
    """
    rule = get(notice_type).get("recipient_default")
    if not notice_type or not rule:
        return facts
    recipient = dict(facts.get("recipient") or {})
    office = ((facts.get("details") or {}).get(rule["from_detail"]) or "").strip()
    if rule.get("name") and not (recipient.get("name") or "").strip():
        recipient["name"] = rule["name"]
    if office:
        if not (recipient.get("address") or "").strip():
            recipient["address"] = office
        if not (recipient.get("role_or_company") or "").strip() and office != recipient.get("address"):
            recipient["role_or_company"] = office
    return {**facts, "recipient": recipient}


BLANK = "____________"          # a blank line the user fills in by hand after printing


def fill_blanks(facts: dict, notice_type: str | None) -> dict:
    """
    Put a blank line (____________) in every required fact that is still missing, so the document can be
    written without it and the user fills it in by hand. Used when the user says "I will fill my details myself".
    Returns a new facts dictionary; the original is not changed.
    """
    result = json.loads(json.dumps(facts or {}))
    for item in missing_facts(result, notice_type):
        node = result
        *parents, last = item["key"].split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[last] = BLANK
    return result


def missing_facts(facts: dict, notice_type: str | None) -> list[dict]:
    """
    Which required facts are still empty?
    Returns e.g. [{"key": "recipient.address", "label": "Their address", "hint": "..."}]
    An empty list means we have everything needed to write the notice.
    """
    needed = list(COMMON_REQUIRED)
    if notice_type:
        needed += [(f"details.{k}", label, hint) for k, label, hint in get(notice_type)["details"]]
    missing = []
    for key, label, hint in needed:
        value = _value(facts, key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append({"key": key, "label": label, "hint": hint})
    return missing


# ---------------------------------------------------------------- self-test
# Run:  python -m app.catalog
if __name__ == "__main__":
    print(f"{len(TYPES)} document types:")
    for key in TYPES:
        t = get(key)
        deadline = f"reply within {t['deadline_days']} days" if t["deadline_days"] else "no deadline"
        print(f"  [{t['kind']:6s}] {key:20s} {t['label']}  ({deadline})")

    sample = {
        "sender": {"name": "Priya Nair"},
        "recipient": {"name": "Suresh Malhotra"},
        "incident": {"summary": "Landlord not returning deposit"},
        "details": {"deposit_amount": "30000"},
    }
    print("\nFor a half-finished deposit case, still missing:")
    for m in missing_facts(sample, "deposit_refund"):
        print("  -", m["label"])
