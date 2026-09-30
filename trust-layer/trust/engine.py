"""Trust-engine: drie gescheiden signalen en drie harde regels.

  trust     = intrinsiek en deterministisch (authority, recency, approval, owner, consistency)
  relevance = per vraag (topic, scope/land, woordoverlap)
  human     = swipes, gewogen naar rol en ouderdom van de stem

Harde regels, in deze volgorde:
  1. gates: eigen company -> relevant genoeg -> geldig op de peildatum
  2. binnen een versieketen wint de versie die geldt op de peildatum; oudere versies
     gaan naar de historie, hoe populair ze ook zijn
  3. spreken geldige claims elkaar tegen, dan beslist alleen trust (nooit swipes)
"""
import math
import re
from datetime import date

WEIGHTS = {"trust": 0.5, "relevance": 0.3, "human": 0.2}
TRUST_WEIGHTS = {"authority": 0.35, "recency": 0.15, "approval": 0.20, "owner": 0.10, "consistency": 0.20}
AUTHORITY = {"contract": 1.0, "policy": 1.0, "legal": 0.95, "procedure": 0.8, "handbook": 0.6, "mail": 0.4, "teams": 0.3}
EXPERT_ROLES = {"payroll_expert", "payroll_consultant", "hr"}
RELEVANCE_GATE = 0.6
REVIEW_GAP = 0.15

MONTHS = {m: i + 1 for i, m in enumerate(
    ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
     "september", "oktober", "november", "december"])}
TOPIC_HINTS = {
    "homework_allowance.amount": ["vergoeding", "euro", "bedrag", "hoeveel krijg"],
    "remote_work.days": ["dagen", "per week", "hoeveel dag"],
    "meal_voucher.amount": ["bedrag", "euro"],
    "payroll.cutoff_day": ["doorgeven", "tegen wanneer", "uiterlijk"],
    "year_end_bonus.payment": ["wanneer", "uitbetaald"],
    "overtime.surcharge": ["toeslag", "procent", "%"],
}
STOPWORDS = {"hoeveel", "wanneer", "welke", "wordt", "worden", "mijn", "voor", "deze", "zijn", "moet",
             "mag", "mocht", "tegen", "krijg", "bedraagt", "geldt", "gold", "huidige", "vandaag"}


def d(value):
    return value if isinstance(value, date) else date.fromisoformat(value)


# ================================================================ vraag begrijpen

def parse_question(question, topics, today):
    q = question.lower()
    scores = {}
    for t in topics.values():
        s = sum(1.0 for k in t["keywords"] if k in q)
        if s:
            s += 0.5 * sum(1 for h in TOPIC_HINTS.get(t["id"], []) if h in q)
            scores[t["id"]] = s
    topic_id = max(scores, key=scores.get) if scores else None
    topic_match = min(1.0, scores[topic_id] / 1.5) if topic_id else 0.0

    country = None
    if re.search(r"\b(nl|nederland|nederlandse|venlo)\b", q):
        country = "NL"
    elif re.search(r"\b(be|belgië|belgie|belgische|antwerpen|gent|luik)\b", q):
        country = "BE"

    return {
        "topic_id": topic_id,
        "topic_match": round(topic_match, 2),
        "country": country,
        "peildatum": parse_date(q, today),
        "tokens": {w for w in re.findall(r"[a-zà-ÿ0-9-]{4,}", q) if w not in STOPWORDS},
    }


def parse_date(q, today):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", q)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", q)
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    months = "|".join(MONTHS)
    m = re.search(rf"\b(\d{{1,2}})\s+({months})\s+(\d{{4}})\b", q)
    if m:
        return date(int(m[3]), MONTHS[m[2]], int(m[1]))
    m = re.search(rf"\b({months})\s+(\d{{4}})\b", q)
    if m:
        return date(int(m[2]), MONTHS[m[1]], 15)
    m = re.search(r"\b(vanaf|per|in|op)\s+(20\d{2})\b", q)
    if m:
        return date(int(m[2]), 1, 1) if m[1] in ("vanaf", "per") else date(int(m[2]), 7, 1)
    if "volgend jaar" in q:
        return date(today.year + 1, 1, 15)
    return None


# ================================================================ signalen

def authority(doc):
    if doc["source_type"] == "teams" and (doc.get("author") or {}).get("role") in EXPERT_ROLES:
        return 0.45  # Teams van een payroll-expert weegt iets zwaarder, nooit als een policy
    return AUTHORITY.get(doc["source_type"], 0.3)


def state_of(kb, claim, peildatum):
    """current | upcoming | superseded | not_yet  (op de peildatum)."""
    doc = kb.doc_of(claim)
    if doc.get("status") != "published":
        return "excluded"
    if d(doc["valid_from"]) > peildatum:
        return "upcoming" if doc.get("chain_id") else "not_yet"
    if not doc.get("chain_id"):
        return "current"
    # versieketen: een nieuwere geldige versie vervangt deze, mits gelijke of hogere authority
    for other in kb.chain_members(doc):
        if other["id"] != doc["id"] and d(doc["valid_from"]) < d(other["valid_from"]) <= peildatum \
                and authority(other) >= authority(doc):
            return "superseded"
    return "current"


def superseded_by(kb, doc, peildatum):
    """De versie uit dezelfde keten die deze vervangt op de peildatum (of None)."""
    newer = [o for o in kb.chain_members(doc) if o["id"] != doc["id"]
             and d(doc["valid_from"]) < d(o["valid_from"]) <= peildatum and authority(o) >= authority(doc)]
    return max(newer, key=lambda o: o["valid_from"]) if newer else None


def trust(kb, claim, peildatum, peers):
    doc = kb.doc_of(claim)
    age_years = max(0.0, (peildatum - d(doc["published"])).days / 365.25)
    owner = doc.get("owner")
    agreeing = sum(authority(kb.doc_of(p)) for p in peers if p["value"] == claim["value"])
    total = sum(authority(kb.doc_of(p)) for p in peers)
    factors = {
        "authority": authority(doc),
        "recency": round(max(0.2, 1 - 0.2 * age_years), 2),
        "approval": 1.0 if doc.get("validated") else 0.5,
        "owner": 1.0 if owner and owner.get("active") else (0.6 if owner else 0.4),
        "consistency": round(agreeing / total, 2) if total else 1.0,
    }
    score = sum(TRUST_WEIGHTS[k] * v for k, v in factors.items())
    return {"score": round(score, 3), "factors": factors}


def relevance(parsed, claim, scope_country):
    scope = 1.0 if claim.get("country") == scope_country else (0.8 if not claim.get("country") else 0.2)
    words = set(re.findall(r"[a-zà-ÿ0-9-]{4,}", claim["sentence"].lower()))
    lexical = len(parsed["tokens"] & words) / len(parsed["tokens"]) if parsed["tokens"] else 0.0
    topic = parsed["topic_match"] if claim["topic"] == parsed["topic_id"] else 0.0
    score = 0.6 * topic + 0.3 * scope + 0.1 * lexical
    return {"score": round(score, 3), "factors": {"topic": topic, "scope": scope, "lexical": round(lexical, 2)}}


def human(kb, claim, ref_date):
    right_w = left_w = 0.0
    right = 0
    left = {"verouderd": 0, "onduidelijk": 0, "fout": 0}
    for e in kb.votes_for(claim):
        age_days = max(0, (ref_date - d(e["date"])).days)
        decay = 0.5 ** (age_days / 365)
        role_w = e["hr_share"] * 2.0 + (1 - e["hr_share"]) * 1.0
        w = e["count"] * role_w * decay
        if e["direction"] == "right":
            right_w += w
            right += e["count"]
        else:
            left_w += w
            left[e.get("reason") or "onduidelijk"] += e["count"]
    score = (right_w + 1) / (right_w + left_w + 2)  # Laplace: zonder stemmen 0,5
    return {"score": round(score, 3), "right": right, "left": left, "votes": right + sum(left.values())}


# ================================================================ resolve

def resolve(kb, company_id, topic_id, peildatum, country=None, parsed=None, today=None):
    """Past de gates en de ranking toe voor één company + topic + scope."""
    today = today or date.today()
    company = kb.companies[company_id]
    scope = country or company["home_country"]
    parsed = parsed or {"topic_id": topic_id, "topic_match": 1.0, "tokens": set()}

    in_company = kb.company_claims(company_id)                         # gate 1: tenant
    on_topic = [c for c in in_company if c["topic"] == topic_id]

    buckets = {"current": [], "upcoming": [], "superseded": [], "other_scope": []}
    for c in on_topic:
        st = state_of(kb, c, peildatum)                                 # gate 3: geldigheid
        if st in ("excluded", "not_yet"):
            continue
        rel = relevance(parsed, c, scope)                               # gate 2: relevantie
        item = {"claim": c, "doc": kb.doc_of(c), "state": st, "relevance": rel}
        if rel["score"] < RELEVANCE_GATE or rel["factors"]["scope"] < 0.8:
            if st == "current":
                buckets["other_scope"].append(item)
            continue
        buckets[st].append(item)

    current_claims = [i["claim"] for i in buckets["current"]]
    for bucket in buckets.values():
        for item in bucket:
            c = item["claim"]
            same_scope = [i["claim"] for i in buckets["current"] if i["claim"]["country"] == c["country"]] \
                if item in buckets["current"] else current_claims
            peers = [p for p in same_scope if p["doc_id"] != c["doc_id"]]
            item["trust"] = trust(kb, c, peildatum, peers)
            item["human"] = human(kb, c, today)
            item["score"] = round(WEIGHTS["trust"] * item["trust"]["score"]
                                  + WEIGHTS["relevance"] * item["relevance"]["score"]
                                  + WEIGHTS["human"] * item["human"]["score"], 3)

    current = buckets["current"]
    values = {repr(i["claim"]["value"]) for i in current}
    conflict = len(values) > 1
    if conflict:   # regel 3: bij conflict beslist alleen trust
        current.sort(key=lambda i: (i["trust"]["score"], i["score"]), reverse=True)
    else:
        current.sort(key=lambda i: i["score"], reverse=True)

    needs_review = False
    if conflict:
        top = current[0]
        rival = next(i for i in current if i["claim"]["value"] != top["claim"]["value"])
        needs_review = top["trust"]["score"] - rival["trust"]["score"] < REVIEW_GAP

    top_votes = current[0]["human"]["votes"] if current else 0
    for item in buckets["superseded"]:   # regel 2: populair maar vervangen = alleen historie
        h = item["human"]
        item["popular_but_superseded"] = h["votes"] >= 20 and h["score"] >= 0.7 and h["votes"] > top_votes
        newer = superseded_by(kb, item["doc"], peildatum)
        item["superseded_by"] = {"id": newer["id"], "label": _label(newer), "valid_from": newer["valid_from"]} if newer else None

    buckets["upcoming"].sort(key=lambda i: i["claim"]["valid_from"])
    buckets["superseded"].sort(key=lambda i: i["claim"]["valid_from"], reverse=True)
    buckets["other_scope"].sort(key=lambda i: i["score"], reverse=True)

    answer = current[0] if current else None
    answer_score = None
    if answer:
        penalty = (0.25 if needs_review else 0.1) if conflict else 0.0
        answer_score = round(max(0.0, answer["score"] - penalty), 2)

    return {
        "status": "ok" if answer else "no_claim",
        "company": {"id": company["id"], "name": company["name"]},
        "topic": kb.topics[topic_id],
        "scope": scope,
        "peildatum": peildatum.isoformat(),
        "answer": answer,
        "answer_score": answer_score,
        "conflict": conflict,
        "needs_review": needs_review,
        "current": current,
        "upcoming": buckets["upcoming"],
        "history": buckets["superseded"],
        "other_scope": buckets["other_scope"],
        "gates": {
            "company": len(in_company),
            "topic": len(on_topic),
            "relevant_in_scope": sum(len(buckets[k]) for k in ("current", "upcoming", "superseded")),
            "valid_now": len(current),
        },
    }


def ask(kb, company_id, question, peildatum=None, today=None):
    from . import specialist
    today = today or date.today()
    if company_id not in kb.companies:
        raise KeyError(company_id)
    parsed = parse_question(question, kb.topics, today)
    when = parsed["peildatum"] or (d(peildatum) if peildatum else today)
    if not parsed["topic_id"]:
        # geen bekend topic: gewoon zoeken in de documenten van deze company
        cards = search_docs(kb, company_id, question, when, today)
        result = {"status": "search" if cards else "no_topic", "company": kb.companies[company_id],
                  "peildatum": when.isoformat(), "current": [], "upcoming": [], "history": [], "other_scope": [],
                  "conflict": False, "cards": cards}
    else:
        result = resolve(kb, company_id, parsed["topic_id"], when, parsed["country"], parsed, today)
        result["cards"] = cards_for_result(kb, result, when)
    result["question"] = question
    result["peildatum_from_question"] = parsed["peildatum"] is not None
    result["answer_text"] = specialist.compose_answer(result)
    result["answer_source"] = "specialist-regels"
    return result


# ================================================================ Trust View + Health

def topic_overview(kb, company_id, peildatum, today=None):
    rows = []
    company = kb.companies[company_id]
    countries = sorted({c["country"] for c in kb.company_claims(company_id)} | {company["home_country"]})
    for topic_id in kb.topics:
        for country in countries:
            r = resolve(kb, company_id, topic_id, peildatum, country, today=today)
            if r["status"] != "ok" and not r["upcoming"]:
                continue
            if country != company["home_country"] and all(i["claim"]["country"] != country for i in r["current"]):
                continue
            rows.append(_row(r))
    return rows


def _row(r):
    reasons, level = [], "green"
    top = r["answer"]
    if r["conflict"]:
        level = "red"
        reasons.append("conflicting valid sources" + (" (HR review)" if r["needs_review"] else ""))
    if r["upcoming"]:
        reasons.append(f"changes on {r['upcoming'][0]['claim']['valid_from']}")
    if any(i.get("popular_but_superseded") for i in r["history"]):
        reasons.append("popular old version in history")
    if top and top["human"]["votes"] >= 5 and top["human"]["score"] < 0.45:
        reasons.append("high trust, low human score")
    if top and top["trust"]["factors"]["owner"] < 1.0:
        reasons.append("no active owner")
    if level != "red" and reasons:
        level = "orange"
    return {
        "topic": r["topic"], "scope": r["scope"], "level": level, "reasons": reasons,
        "answer": top["claim"]["display"] if top else None,
        "source": _label(top['doc']) if top else None,
        "answer_score": r["answer_score"], "trust": top["trust"]["score"] if top else None,
        "human": top["human"]["score"] if top else None,
        "counts": {k: len(r[k]) for k in ("current", "upcoming", "history")},
    }


def health(kb, company_id, peildatum, today=None):
    today = today or date.today()
    out = {"conflicts": [], "popular_superseded": [], "high_trust_low_human": [], "no_owner": [],
           "stale": [], "archive_proposals": [], "upcoming": [], "swipe_signals": [], "noise": []}
    seen_archive = set()
    company = kb.companies[company_id]
    countries = sorted({c["country"] for c in kb.company_claims(company_id)} | {company["home_country"]})
    for topic_id in kb.topics:
        for country in countries:
            r = resolve(kb, company_id, topic_id, peildatum, country, today=today)
            label = f"{r['topic']['label']} ({country})"
            if r["conflict"]:
                top = r["answer"]
                losers = [i for i in r["current"] if i["claim"]["value"] != top["claim"]["value"]]
                out["conflicts"].append({
                    "topic": label, "winner": _ref(top), "against": [_ref(i) for i in losers],
                    "needs_review": r["needs_review"]})
                for i in losers:
                    if i["trust"]["score"] < top["trust"]["score"] and d(i["doc"]["published"]) < d(top["doc"]["published"]) \
                            and i["doc"]["id"] not in seen_archive and i["doc"]["source_type"] != "teams":
                        seen_archive.add(i["doc"]["id"])
                        out["archive_proposals"].append({"doc": _ref(i), "reason": f"contradicts {_ref(top)['label']} with lower trust"})
            for i in r["history"]:
                if i.get("popular_but_superseded"):
                    out["popular_superseded"].append({"topic": label, "old": _ref(i), "current": _ref(r["answer"]) if r["answer"] else None,
                                                      "votes": i["human"]["votes"]})
                if i["doc"]["id"] not in seen_archive:
                    seen_archive.add(i["doc"]["id"])
                    out["archive_proposals"].append({"doc": _ref(i), "reason": "replaced by a newer valid version"})
            for i in r["upcoming"]:
                out["upcoming"].append({"topic": label, "doc": _ref(i), "valid_from": i["claim"]["valid_from"]})
            for i in r["current"]:
                h, t = i["human"], i["trust"]
                if t["score"] >= 0.75 and h["votes"] >= 5 and h["score"] < 0.45:
                    out["high_trust_low_human"].append({"topic": label, "doc": _ref(i), "trust": t["score"], "human": h["score"],
                                                        "left": h["left"]})
                if h["left"]["verouderd"] >= 3:
                    out["swipe_signals"].append({"doc": _ref(i), "signal": "notify owner: is a newer version missing?",
                                                 "count": h["left"]["verouderd"]})
                if h["left"]["fout"] >= 2:
                    out["swipe_signals"].append({"doc": _ref(i), "signal": "escalate to HR: reported as wrong",
                                                 "count": h["left"]["fout"]})
    for doc in kb.company_docs(company_id):
        if doc["source_type"] == "teams":
            if not any(c["doc_id"] == doc["id"] for c in kb.claims.values()):
                out["noise"].append({"doc": {"id": doc["id"], "label": doc["title"]}, "text": doc["text"]})
            continue
        owner = doc.get("owner")
        if not owner or not owner.get("active"):
            out["no_owner"].append({"doc": {"id": doc["id"], "label": _label(doc)},
                                    "owner": owner["name"] + " (inactive)" if owner else "no owner"})
        age = (peildatum - d(doc["published"])).days / 365.25
        if age > 2.5 and all(state_of(kb, c, peildatum) == "current" for c in kb.claims.values() if c["doc_id"] == doc["id"]) \
                and any(c["doc_id"] == doc["id"] for c in kb.claims.values()):
            out["stale"].append({"doc": {"id": doc["id"], "label": _label(doc)}, "age_years": round(age, 1)})
    return out


def _ref(item):
    from .specialist import doc_label
    doc = item["doc"]
    return {"id": doc["id"], "claim_id": item["claim"]["id"], "label": doc_label(doc),
            "value": item["claim"]["display"], "trust": item["trust"]["score"] if "trust" in item else None}


# ================================================================ bronkaarten (zoeken = swipen)

VERDICT = {
    "answer": ("good", "Current answer"),
    "agrees": ("good", "Valid · confirms the answer"),
    "conflict": ("bad", "Conflicting"),
    "superseded": ("bad", "Outdated"),
    "upcoming": ("warn", "Not yet valid"),
    "other_scope": ("grey", "Other scope"),
    "document": ("grey", "Document"),
}
INFORMAL = {"teams": "Teams message", "mail": "email"}


def _years(doc, peildatum):
    return max(0.0, (peildatum - d(doc["published"])).days / 365.25)


def _nl(x):
    return f"{x:.1f}".replace(".", ",")


def _meta_flags(doc, peildatum):
    """Signalen die bij het document zelf horen: ouderdom, owner, validatie, informele bron."""
    flags = []
    age = _years(doc, peildatum)
    if age >= 2.5:
        flags.append({"level": "warn", "text": f"Old: published {_nl(age)} years ago ({doc['published']})"})
    elif age >= 1:
        flags.append({"level": "grey", "text": f"{_nl(age)} years old"})
    owner = doc.get("owner")
    if not owner:
        flags.append({"level": "grey", "text": "No owner"})
    elif not owner.get("active"):
        flags.append({"level": "grey", "text": f"Owner {owner['name']} is no longer active"})
    if not doc.get("validated") and doc["source_type"] not in INFORMAL:
        flags.append({"level": "grey", "text": "Not validated"})
    if doc["source_type"] in INFORMAL:
        flags.append({"level": "grey", "text": f"Informal source ({INFORMAL[doc['source_type']]}), weighs less"})
    return flags


def role_of(r, item):
    if item in r["current"]:
        if item is r["answer"]:
            return "answer"
        return "conflict" if item["claim"]["value"] != r["answer"]["claim"]["value"] else "agrees"
    if item in r["upcoming"]:
        return "upcoming"
    if item in r["history"]:
        return "superseded"
    return "other_scope"


def card(kb, r, item, peildatum):
    role = role_of(r, item)
    top = r["answer"]
    flags = []
    if role == "answer" and r["conflict"]:
        rivals = [i for i in r["current"] if i["claim"]["value"] != top["claim"]["value"]]
        flags.append({"level": "bad", "text": "Contradicted by " + ", ".join(
            f"{_label(i['doc'])} ({i['claim']['display']})" for i in rivals)})
        if r["needs_review"]:
            flags.append({"level": "bad", "text": "Small trust gap: HR review recommended"})
    elif role == "conflict":
        flags.append({"level": "bad", "text": f"Contradicts {_label(top['doc'])}: {item['claim']['display']} "
                                              f"instead of {top['claim']['display']}"})
    elif role == "agrees":
        flags.append({"level": "good", "text": f"Says the same as {_label(top['doc'])}"})
    elif role == "superseded":
        sb = item.get("superseded_by")
        flags.append({"level": "bad", "text": f"Replaced by {sb['label']} since {sb['valid_from']}" if sb
                      else "Replaced by a newer version"})
        if item.get("popular_but_superseded"):
            flags.append({"level": "warn", "text": f"Popular ({item['human']['votes']} votes), but no longer valid"})
    elif role == "upcoming":
        text = f"Takes effect on {item['claim']['valid_from']}"
        if top:
            text += f" and then replaces {top['claim']['display']}"
        flags.append({"level": "warn", "text": text})
    elif role == "other_scope":
        flags.append({"level": "grey", "text": f"Applies to {item['claim']['country']}, your scope is {r['scope']}"})
    if role == "answer" and r.get("upcoming"):
        u = r["upcoming"][0]
        flags.append({"level": "warn", "text": f"Changes on {u['claim']['valid_from']}: {u['claim']['display']}"})
    flags += _meta_flags(item["doc"], peildatum)
    level, verdict = VERDICT[role]
    if role == "upcoming":
        verdict = f"Valid from {item['claim']['valid_from']}"
    if role == "other_scope":
        verdict = f"Other scope ({item['claim']['country']})"
    if role == "answer" and r["conflict"]:
        level, verdict = "warn", "Current answer · disputed"
    return {
        "key": item["claim"]["id"], "claim_id": item["claim"]["id"], "role": role,
        "verdict": {"level": level, "text": verdict},
        "topic": r["topic"]["label"], "display": item["claim"]["display"], "sentence": item["claim"]["sentence"],
        "country": item["claim"]["country"], "doc": _doc_info(item["doc"]),
        "trust": item["trust"], "human": item["human"], "flags": flags,
        "why": explain_trust(item["doc"], item["trust"], peildatum), "topic_id": item["claim"]["topic"],
    }


def _doc_info(doc):
    return {"id": doc["id"], "label": _label(doc), "title": doc["title"], "version": doc["version"],
            "published": doc["published"], "valid_from": doc["valid_from"], "source_type": doc["source_type"],
            "has_file": bool(doc.get("file"))}


def doc_card(doc, peildatum, note):
    flags = [{"level": "grey", "text": note}] + _meta_flags(doc, peildatum)
    text = doc["text"]
    return {"key": "doc:" + doc["id"], "claim_id": None, "role": "document",
            "verdict": {"level": "grey", "text": "Document"}, "topic": None, "display": None,
            "sentence": text[:240] + ("…" if len(text) > 240 else ""), "country": doc.get("country"),
            "doc": _doc_info(doc), "trust": None, "human": None, "flags": flags}


SOURCE_NAME = {"contract": "Signed contract", "policy": "Official policy", "legal": "Legal / collective agreement",
               "procedure": "Procedure", "handbook": "Handbook", "mail": "Email", "teams": "Teams message"}


def explain_trust(doc, trust_info, peildatum):
    """Why this source has this trust score: one line per factor, with its weight."""
    f = trust_info["factors"]
    age = _years(doc, peildatum)
    owner = doc.get("owner")
    lines = [
        ("authority", f"{SOURCE_NAME.get(doc['source_type'], doc['source_type'])}: authority {f['authority']:.2f}"),
        ("recency", f"Published {_nl(age).replace(',', '.')} years ago: recency {f['recency']:.2f}"),
        ("approval", f"Validated by {doc['validated']['by']}" if doc.get("validated") else "Not validated: approval 0.50"),
        ("owner", f"Active owner {owner['name']}" if owner and owner.get("active")
         else (f"Owner {owner['name']} inactive: 0.60" if owner else "No owner: 0.40")),
        ("consistency", "Consistent with every other valid source" if f["consistency"] == 1.0
         else ("Contradicted by all other valid sources: 0.00" if f["consistency"] == 0
               else f"Partly agrees with other valid sources: {f['consistency']:.2f}")),
    ]
    return [{"factor": k, "weight": TRUST_WEIGHTS[k], "value": f[k], "text": t} for k, t in lines]


def cards_for_result(kb, r, peildatum):
    return [card(kb, r, i, peildatum) for k in ("current", "upcoming", "history", "other_scope") for i in r[k]]


class _Resolver:
    """Cachet resolve() per topic + land, zodat lijsten met veel kaarten snel blijven."""

    def __init__(self, kb, company_id, peildatum, today):
        self.kb, self.company_id, self.peildatum, self.today, self.cache = kb, company_id, peildatum, today, {}

    def card_for(self, claim):
        key = (claim["topic"], claim["country"])
        if key not in self.cache:
            self.cache[key] = resolve(self.kb, self.company_id, claim["topic"], self.peildatum, claim["country"], today=self.today)
        r = self.cache[key]
        item = next((i for k in ("current", "upcoming", "history", "other_scope") for i in r[k]
                     if i["claim"]["id"] == claim["id"]), None)
        return card(self.kb, r, item, self.peildatum) if item else None


def search_docs(kb, company_id, query, peildatum, today=None, limit=8):
    """Vrije-tekstzoektocht als de vraag bij geen enkel topic past."""
    today = today or date.today()
    words = [w for w in re.findall(r"[a-zà-ÿ0-9-]{3,}", query.lower()) if w not in STOPWORDS]
    docs = kb.company_docs(company_id)
    if not words or not docs:
        return []
    hay = {doc["id"]: (doc["title"].lower(), doc["text"].lower()) for doc in docs}
    # woorden die in bijna elk document staan (zoals de bedrijfsnaam) wegen bijna niets
    idf = {w: math.log((len(docs) + 1) / (1 + sum(1 for t, x in hay.values() if w in t or w in x))) for w in words}
    scored = []
    for doc in docs:
        title, text = hay[doc["id"]]
        score = sum(idf[w] * (2 * title.count(w) + text.count(w)) for w in words)
        if score > 0.3:
            scored.append((score, doc))
    scored.sort(key=lambda x: (-x[0], x[1]["title"]))
    if scored:  # alleen documenten die minstens 40% zo goed matchen als de beste
        scored = [s for s in scored if s[0] >= 0.4 * scored[0][0]]
    res = _Resolver(kb, company_id, peildatum, today)
    cards = []
    for rank, (_, doc) in enumerate(scored[:limit]):
        made = doc_cards(kb, company_id, doc, peildatum, res)
        cards += [(rank, c) for c in made]
    # geldige bronnen eerst, verouderde achteraan; binnen een groep de beste zoekmatch eerst
    cards.sort(key=lambda rc: (ROLE_ORDER[rc[1]["role"]], rc[0]))
    return [c for _, c in cards]


ROLE_ORDER = {"answer": 0, "agrees": 1, "conflict": 2, "upcoming": 3, "other_scope": 4, "superseded": 5, "document": 6}


def doc_cards(kb, company_id, doc, peildatum, res=None, today=None):
    """Alle kaarten voor één document (één per claim), of een documentkaart zonder claims."""
    res = res or _Resolver(kb, company_id, peildatum, today or date.today())
    claims = [c for c in kb.claims.values() if c["doc_id"] == doc["id"]]
    made = [c for c in (res.card_for(cl) for cl in claims) if c]
    if made:
        return made
    if claims:
        return [doc_card(doc, peildatum, f"Only valid from {doc['valid_from']}")]
    return [doc_card(doc, peildatum, "No HR/payroll rule recognised in this document")]


def dashboard(kb, company_ids, peildatum, today=None):
    """Per company the user can access: its big categories, with topics, status and counts."""
    out = []
    for cid in company_ids:
        rows = topic_overview(kb, cid, peildatum, today)
        cats = {}
        for r in rows:
            cat = cats.setdefault(r["topic"]["category"], {"name": r["topic"]["category"], "topics": [],
                                                          "levels": {"red": 0, "orange": 0, "green": 0}})
            cat["topics"].append({"id": r["topic"]["id"], "label": r["topic"]["label"], "scope": r["scope"],
                                  "level": r["level"], "answer": r["answer"]})
            cat["levels"][r["level"]] += 1
        docs = kb.company_docs(cid)
        out.append({"id": cid, "name": kb.companies[cid]["name"], "documents": len(docs),
                    "claims": len(kb.company_claims(cid)), "categories": sorted(cats.values(), key=lambda c: c["name"])})
    return out


def rate_cards(kb, company_id, peildatum, today=None, category=None):
    """Kaarten zonder zoekvraag: eerst wat het antwoord bepaalt of betwist is, dan verouderd,
    dan de rest; binnen elke groep de minste stemmen eerst (daar leert het systeem het meest van)."""
    today = today or date.today()
    res = _Resolver(kb, company_id, peildatum, today)
    claims = [cl for cl in kb.company_claims(company_id)
              if not category or kb.topics[cl["topic"]].get("category") == category]
    cards = [c for c in (res.card_for(cl) for cl in claims
                         if state_of(kb, cl, peildatum) not in ("excluded", "not_yet")) if c]
    priority = {"answer": 0, "conflict": 0, "superseded": 1}
    cards.sort(key=lambda x: (priority.get(x["role"], 2), x["human"]["votes"], x["key"]))
    return cards


def _label(doc):
    from .specialist import doc_label
    return doc_label(doc)
