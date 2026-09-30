"""Specialist Agent: smal en doelgericht.

1. claim-extractie in HR/payroll-vocabulaire (regels, optioneel Claude voor nieuwe documenten)
2. topic-normalisatie naar een vaste taxonomie (meal_voucher.amount, ...)
3. ruisfilter voor Teams (vragen en small talk leveren geen claims op)
4. antwoord + Trust Receipt formuleren op basis van de ranking

Waarden worden altijd door de regels hieronder genormaliseerd, ook als Claude de
claim vond. Zo blijft vergelijken en conflictdetectie deterministisch.
"""
import re

from . import llm

NUMBER_WORDS = {"een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5}

_AMOUNT = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*(?:euro\b|eur\b|€)|€\s*(\d+(?:[.,]\d{1,2})?)", re.I)
_CUTOFF = re.compile(r"(\d{1,2})e\s+van\s+de\s+maand|de\s+(\d{1,2})e\b", re.I)
_PERCENT = re.compile(r"(\d{1,3})\s*%\s*toeslag|toeslag\s+van\s+(\d{1,3})\s*%", re.I)
_DAYS = re.compile(r"\b(\d|een|één|twee|drie|vier|vijf)\s+dag(?:en)?\s+per\s+week", re.I)
_TWO_PARTS = re.compile(r"twee schijven|2 schijven|in juni en|juni en december", re.I)


def _euro(value):
    return f"€{value:,.2f}"


def _first_group(match):
    return next(g for g in match.groups() if g is not None)


def normalize_value(topic, sentence):
    """Haalt de waarde voor `topic` uit een zin. Geeft (value, display) of None."""
    s = sentence
    if topic == "payroll.cutoff_day":
        m = _CUTOFF.search(s)
        if m:
            day = int(_first_group(m))
            if 1 <= day <= 31:
                return day, f"{day}th of the month"
    elif topic == "year_end_bonus.payment":
        if _TWO_PARTS.search(s):
            return "juni + december", "in two instalments (June and December)"
        if re.search(r"december", s, re.I):
            return "december", "in full in December"
    elif topic in ("meal_voucher.amount", "homework_allowance.amount"):
        m = _AMOUNT.search(s)
        if m:
            value = float(_first_group(m).replace(",", "."))
            per = "per day" if topic == "meal_voucher.amount" else "per month"
            return value, f"{_euro(value)} {per}"
    elif topic == "overtime.surcharge":
        m = _PERCENT.search(s)
        if m:
            pct = int(_first_group(m))
            return pct, f"{pct}% surcharge"
    elif topic == "remote_work.days":
        m = _DAYS.search(s)
        if m:
            raw = m.group(1).lower()
            days = NUMBER_WORDS.get(raw) or int(raw)
            return days, f"max. {days} day{'s' if days != 1 else ''} per week"
    return None


def split_sentences(text):
    # zinnen eindigen op . ! ? of op een regeleinde (titels en opsommingen in Word/PDF)
    return [s.strip(" \t-•*") for s in re.split(r"(?<=[.!?])\s+|\s*\n+\s*", text or "") if s.strip(" \t-•*")]


def topics_in(sentence, topics):
    low = sentence.lower()
    return [t for t in topics if any(k in low for k in t["keywords"])]


def extract_rules(doc, topics):
    """Deterministische extractie. Geeft (claims, noise_sentences)."""
    claims, noise = [], []
    for i, sentence in enumerate(split_sentences(doc["text"])):
        if sentence.endswith("?"):
            noise.append({"sentence": sentence, "reason": "question, not a statement"})
            continue
        found = False
        for topic in topics_in(sentence, topics):
            norm = normalize_value(topic["id"], sentence)
            if norm is None:
                continue
            value, display = norm
            claims.append(_claim(doc, topic["id"], value, display, sentence, i))
            found = True
            break
        if not found:
            noise.append({"sentence": sentence, "reason": "no HR/payroll claim"})
    return claims, noise


def extract_llm(doc, topics):
    """Claude kiest topic + zin, de regels normaliseren de waarde. None bij geen LLM."""
    schema = {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "enum": [t["id"] for t in topics]},
                        "sentence": {"type": "string"},
                    },
                    "required": ["topic", "sentence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["claims"],
        "additionalProperties": False,
    }
    topic_list = "\n".join(f"- {t['id']}: {t['label']} ({t['unit']})" for t in topics)
    result = llm.structured(
        system=(
            "Je bent de Specialist Agent van een HR/payroll-kennisplatform. Je haalt alleen "
            "beweringen (claims) uit tekst die een concrete regel of waarde vastleggen. Vragen, "
            "small talk en vermoedens zijn geen claims."
        ),
        prompt=(
            f"Topics:\n{topic_list}\n\nDocument ({doc['source_type']}): {doc['title']}\n\n{doc['text']}\n\n"
            "Geef per claim het topic en de letterlijke zin uit het document waarin de waarde staat."
        ),
        schema=schema,
    )
    if result is None:
        return None
    claims = []
    for i, item in enumerate(result["claims"]):
        norm = normalize_value(item["topic"], item["sentence"])
        if norm is None:
            continue  # geen normaliseerbare waarde: liever geen claim dan een gok
        claims.append(_claim(doc, item["topic"], norm[0], norm[1], item["sentence"], i))
    return claims


def extract(doc, topics, use_llm=False):
    claims, noise = extract_rules(doc, topics)
    method = "regels"
    if use_llm:
        llm_claims = extract_llm(doc, topics)
        if llm_claims is not None:
            claims, method = llm_claims, "claude"
    return claims, noise, method


def _claim(doc, topic, value, display, sentence, i):
    return {
        "id": f"{doc['id']}#{topic}#{i}",
        "company_id": doc["company_id"],
        "doc_id": doc["id"],
        "topic": topic,
        "value": value,
        "display": display,
        "valid_from": doc["valid_from"],
        "country": doc.get("country"),
        "sentence": sentence,
    }


# ---------------------------------------------------------------- antwoorden

def _src(item):
    return doc_label(item["doc"])


def doc_label(d):
    version = d.get("version")
    if version in (None, "-") or version in d["title"]:
        return d["title"]
    return f"{d['title']} {version}"


def compose_answer(result):
    """Deterministische formulering. Gebruikt alleen feiten uit de ranking."""
    if result["status"] == "no_topic":
        return "No HR/payroll topic or document in this company matches your search."
    if result["status"] == "search":
        n = len({c["doc"]["id"] for c in result["cards"]})
        return (f"No specific rule recognised. {n} document{'s' if n != 1 else ''} "
                f"match your search: swipe through them.")
    if result["status"] == "no_claim":
        return (f"No rule for '{result['topic']['label']}' is valid on "
                f"{result['peildatum']} at {result['company']['name']}.")
    top = result["answer"]
    lines = [f"{top['claim']['display'][0].upper()}{top['claim']['display'][1:]}, according to {_src(top)} "
             f"(valid from {top['claim']['valid_from']})."]
    for up in result["upcoming"]:
        lines.append(f"Note: from {up['claim']['valid_from']} this becomes {up['claim']['display']} ({_src(up)}).")
    if result["conflict"]:
        others = [c for c in result["current"][1:] if c["claim"]["value"] != top["claim"]["value"]]
        for c in others:
            lines.append(f"Conflicting source: {_src(c)} says {c['claim']['display']}. "
                         f"Vouch follows the source with the highest trust.")
        if result["needs_review"]:
            lines.append("The trust gap is small: ask HR to confirm.")
    for c in result["other_scope"][:2]:
        lines.append(f"For {c['claim']['country']}: {c['claim']['display']} ({_src(c)}).")
    return " ".join(lines)


def phrase_with_llm(result, answer):
    """Laat Claude het antwoord vlotter formuleren, met een harde feitencheck."""
    if result["status"] != "ok" or not llm.available():
        return None
    out = llm.structured(
        system=(
            "Je herformuleert een HR/payroll-antwoord voor een medewerker in helder Nederlands. "
            "Gebruik uitsluitend de gegeven feiten. Voeg niets toe. Behoud elk bedrag, elke datum "
            "en elke vermelding van conflicten of toekomstige wijzigingen."
        ),
        prompt=f"Vraag: {result['question']}\n\nFeitelijk antwoord:\n{answer}",
        schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )
    if not out:
        return None
    text = out["answer"]
    # feitencheck: de kernwaarde en elke wijzigingsdatum moeten erin blijven staan
    must = [_core_token(result["answer"]["claim"])] + [u["claim"]["valid_from"] for u in result["upcoming"]]
    if all(m in text for m in must):
        return text
    return None


def _core_token(claim):
    v = claim["value"]
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v).split(" ")[0]
