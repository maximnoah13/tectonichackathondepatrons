"""Test Agent: beoordeelt de Specialist Agent en rapporteert hogerop.

Twee niveaus:
  * deterministische asserties in code (pass/fail, geen mening)
      - gouden vragen met verwacht gedrag
      - invarianten over alle companies x topics x peildata
      - stresstest: 1000 swipes op oude/zwakke claims mogen de winnaar niet veranderen
  * optionele LLM-judge (Claude) voor de kwaliteit van het geformuleerde antwoord

Het rapport wordt bewaard in data/state/reports/ en, als TRUST_LAYER_REPORT_URL gezet
is, als JSON naar die URL gepost (de "bedrijfsagent" of jury-laag van SD Worx).
"""
import json
import os
import urllib.request
from datetime import date, datetime
from pathlib import Path

from . import engine, llm
from .kb import STATE

TODAY = date(2026, 9, 30)  # vaste referentiedatum: tests zijn reproduceerbaar

GOLDEN = [
    {"id": "G1", "company": "brouwer", "q": "Hoeveel bedraagt de maaltijdcheque?",
     "expect": {"value": 7.0, "doc": "b-maaltijd-2026", "conflict": True, "conflict_with": ["b-handboek-2025"],
                "upcoming": [("2027-01-01", 8.0)]},
     "why": "geldig op de peildatum wint; toekomstige versie als badge; conflict met handboek zichtbaar"},
    {"id": "G2", "company": "brouwer", "q": "Hoeveel bedraagt de maaltijdcheque op 1 februari 2027?",
     "expect": {"value": 8.0, "doc": "b-maaltijd-2027", "peildatum": "2027-02-01"},
     "why": "peildatum uit de vraag: vanaf 2027 geldt de nieuwe versie"},
    {"id": "G3", "company": "brouwer", "q": "Hoeveel dagen per week mag ik thuiswerken?",
     "expect": {"value": 2, "doc": "b-telewerk-2026", "conflict": True, "popular_superseded": ["b-telewerk-2024"]},
     "why": "populaire oude versie (96 duimpjes) wint nooit van de geldige versie"},
    {"id": "G4", "company": "brouwer", "q": "Hoeveel dagen mocht ik thuiswerken in maart 2025?",
     "expect": {"value": 3, "doc": "b-telewerk-2024", "peildatum": "2025-03-15"},
     "why": "historische vraag: de oude versie gold toen wel"},
    {"id": "G5", "company": "brouwer", "q": "Tegen wanneer moet ik de loongegevens doorgeven?",
     "expect": {"value": 15, "doc": "b-contract-v3", "conflict": True, "conflict_with": ["b-handleiding"],
                "history": ["b-contract-v2", "b-contract-v1"]},
     "why": "contract v3 vervangt v1/v2; de handleiding spreekt het tegen"},
    {"id": "G6", "company": "brouwer", "q": "Wanneer wordt de eindejaarspremie uitbetaald?",
     "expect": {"value": "juni + december", "doc": "b-contract-v3", "conflict": True, "conflict_with": ["b-mail-eindejaar"]},
     "why": "informele mail uit 2024 verliest van het contract"},
    {"id": "G7", "company": "brouwer", "q": "Hoeveel is de thuiswerkvergoeding?",
     "expect": {"value": 157.83, "doc": "b-thuiswerk-v2", "conflict": False, "history": ["b-thuiswerk-v1"]},
     "why": "geïndexeerd bedrag vervangt het oude"},
    {"id": "G8", "company": "brouwer", "q": "Hoeveel toeslag krijg ik voor overuren in Nederland?",
     "expect": {"value": 30, "doc": "b-overuren-nl", "scope": "NL"},
     "why": "scope: land in de vraag bepaalt de regel"},
    {"id": "G9", "company": "brouwer", "q": "Wat is de toeslag voor overuren?",
     "expect": {"value": 50, "conflict": False, "other_scope": ["b-overuren-nl"]},
     "why": "zonder land: thuisland, NL apart vermeld en geen vals conflict"},
    {"id": "G10", "company": "verhoeven", "q": "Hoeveel bedraagt de maaltijdcheque?",
     "expect": {"value": 5.5, "doc": "v-maaltijd", "conflict": True, "conflict_with": ["t-v-maaltijd"]},
     "why": "tenant-isolatie: Brouwer-bedragen mogen hier nooit opduiken"},
    {"id": "G11", "company": "verhoeven", "q": "Wat is de cut-off voor de loongegevens?",
     "expect": {"value": 18, "doc": "v-sla", "conflict": False},
     "why": "tweede tenant, eigen SLA"},
]

NOISE_DOCS = ["t-b-lunch", "t-b-maaltijdvraag", "t-b-eindejaarvraag", "t-v-vakantie", "t-v-parking"]
INVARIANT_DATES = [date(2024, 6, 1), date(2025, 3, 15), TODAY, date(2027, 2, 1)]


def _ids(items):
    return [i["doc"]["id"] for i in items]


def check_golden(kb, case):
    r = engine.ask(kb, case["company"], case["q"], today=TODAY)
    e, checks = case["expect"], []

    def add(name, ok, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    top = r.get("answer")
    add("antwoord gevonden", top is not None)
    if top is None:
        return r, checks
    add("juiste waarde", top["claim"]["value"] == e["value"], f"kreeg {top['claim']['display']}")
    if "doc" in e:
        add("juiste bron", top["doc"]["id"] == e["doc"], f"kreeg {top['doc']['id']}")
    if "peildatum" in e:
        add("peildatum uit vraag", r["peildatum"] == e["peildatum"], f"kreeg {r['peildatum']}")
    if "conflict" in e:
        add("conflict correct gemeld", r["conflict"] == e["conflict"])
    for doc_id in e.get("conflict_with", []):
        add(f"conflict toont {doc_id}", doc_id in _ids(r["current"]))
        add("conflict staat in antwoordtekst", "Conflicting source" in r["answer_text"])
    for when, value in e.get("upcoming", []):
        add(f"badge wijziging {when}", any(u["claim"]["valid_from"] == when and u["claim"]["value"] == value for u in r["upcoming"]))
    for doc_id in e.get("history", []):
        add(f"{doc_id} in historie", doc_id in _ids(r["history"]))
    for doc_id in e.get("popular_superseded", []):
        add(f"{doc_id} populair maar vervangen", any(i["doc"]["id"] == doc_id and i.get("popular_but_superseded") for i in r["history"]))
    for doc_id in e.get("other_scope", []):
        add(f"{doc_id} apart als andere scope", doc_id in _ids(r["other_scope"]))
    if "scope" in e:
        add("scope uit vraag", r["scope"] == e["scope"])
    add("geen claims van andere company", _isolated(r, case["company"]))
    return r, checks


def _isolated(r, company_id):
    return all(i["claim"]["company_id"] == company_id
               for k in ("current", "upcoming", "history", "other_scope") for i in r.get(k, []))


def check_invariants(kb):
    results = {"isolation_ok": [], "version_rule_ok": [], "validity_ok": [], "conflict_visible_ok": []}
    for company_id in kb.companies:
        countries = {c["country"] for c in kb.company_claims(company_id)}
        for topic_id in kb.topics:
            for peildatum in INVARIANT_DATES:
                for country in countries:
                    r = engine.resolve(kb, company_id, topic_id, peildatum, country, today=TODAY)
                    tag = f"{company_id}/{topic_id}/{country}/{peildatum}"
                    results["isolation_ok"].append((tag, _isolated(r, company_id)))
                    results["version_rule_ok"].append((tag, all(i["state"] == "current" for i in r["current"])))
                    top = r["answer"]
                    results["validity_ok"].append((tag, top is None or engine.d(top["claim"]["valid_from"]) <= peildatum))
                    values = {repr(i["claim"]["value"]) for i in r["current"]}
                    results["conflict_visible_ok"].append((tag, (len(values) > 1) == r["conflict"]))
    return {k: {"pass": all(ok for _, ok in v), "checked": len(v), "failed": [t for t, ok in v if not ok]}
            for k, v in results.items()}


def check_human_not_over_version(kb):
    """Duizend HR-swipes naar rechts op elke vervangen of verliezende claim: de waarde blijft gelijk.

    Swipes mogen wel de volgorde bepalen tussen bronnen die hetzelfde zeggen (G9: BE-beleid en
    PC 140 zeggen allebei 50%); ze mogen nooit een andere waarde of een oude versie naar boven halen.
    """
    def winner(c):
        top = engine.ask(kb, c["company"], c["q"], today=TODAY)["answer"]
        return (repr(top["claim"]["value"]), top["state"])

    before = {c["id"]: winner(c) for c in GOLDEN}
    flood = []
    for c in GOLDEN:
        r = engine.ask(kb, c["company"], c["q"], today=TODAY)
        losers = r["history"] + r["current"][1:] + r["upcoming"]
        for item in losers:
            flood += [{"claim_id": item["claim"]["id"], "direction": "right", "role": "hr", "date": TODAY.isoformat()}] * 1000
    with kb.vote_overlay(flood):
        after = {c["id"]: winner(c) for c in GOLDEN}
    changed = [k for k in before if before[k] != after[k]]
    return {"pass": not changed, "checked": len(before), "failed": changed, "fake_votes": len(flood)}


def check_noise(kb):
    leaked = [doc_id for doc_id in NOISE_DOCS if any(c["doc_id"] == doc_id for c in kb.claims.values())]
    return {"pass": not leaked, "checked": len(NOISE_DOCS), "failed": leaked}


def judge(case, r):
    schema = {
        "type": "object",
        "properties": {
            "faithfulness": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
            "clarity": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
            "uncertainty_shown": {"type": "boolean"},
            "comment": {"type": "string"},
        },
        "required": ["faithfulness", "clarity", "uncertainty_shown", "comment"],
        "additionalProperties": False,
    }
    facts = {
        "answer": r["answer"]["claim"]["display"] if r.get("answer") else None,
        "source": r["answer"]["doc"]["title"] if r.get("answer") else None,
        "conflict": r["conflict"], "upcoming": [u["claim"]["display"] + " vanaf " + u["claim"]["valid_from"] for u in r["upcoming"]],
    }
    return llm.structured(
        system="Je bent een strenge beoordelaar van HR/payroll-antwoorden. Beoordeel alleen wat gegeven is.",
        prompt=(f"Vraag: {case['q']}\nFeiten uit de Trust Receipt: {json.dumps(facts, ensure_ascii=False)}\n"
                f"Antwoord aan de medewerker: {r['answer_text']}\n\n"
                "faithfulness: klopt het antwoord met de feiten (5 = volledig)? clarity: begrijpt een medewerker het? "
                "uncertainty_shown: worden conflict en toekomstige wijziging getoond als ze er zijn?"),
        schema=schema,
    )


def run(kb, use_judge=True, save=True):
    cases = []
    for case in GOLDEN:
        r, checks = check_golden(kb, case)
        entry = {"id": case["id"], "company": case["company"], "question": case["q"], "why": case["why"],
                 "answer": r["answer_text"], "checks": checks, "pass": all(c["pass"] for c in checks)}
        if use_judge and llm.available():
            entry["judge"] = judge(case, r)
        cases.append(entry)

    invariants = check_invariants(kb)
    rules = {
        "version_rule_ok": invariants["version_rule_ok"],
        "isolation_ok": invariants["isolation_ok"],
        "validity_ok": invariants["validity_ok"],
        "conflict_visible_ok": invariants["conflict_visible_ok"],
        "human_signal_respected_but_not_over_version": check_human_not_over_version(kb),
        "teams_noise_filtered": check_noise(kb),
    }
    total = sum(len(c["checks"]) for c in cases) + sum(r["checked"] for r in rules.values())
    failed = sum(1 for c in cases for x in c["checks"] if not x["pass"]) + sum(len(r["failed"]) for r in rules.values())
    judged = [c["judge"] for c in cases if c.get("judge")]
    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "reference_date": TODAY.isoformat(),
        "overall_score": round((total - failed) / total, 3),
        "checks_total": total,
        "checks_failed": failed,
        "golden_passed": sum(c["pass"] for c in cases),
        "golden_total": len(cases),
        "rules": {k: {kk: vv for kk, vv in v.items()} for k, v in rules.items()},
        "judge": {
            "status": "uitgevoerd" if judged else f"overgeslagen ({llm.status()[1]})",
            "faithfulness_avg": round(sum(j["faithfulness"] for j in judged) / len(judged), 2) if judged else None,
            "clarity_avg": round(sum(j["clarity"] for j in judged) / len(judged), 2) if judged else None,
        },
        "cases": cases,
    }
    if save:
        report["delivered_to"] = _deliver(report)
    return report


def _deliver(report):
    out = []
    folder = STATE / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"eval-{report['run_at'].replace(':', '')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    out.append(str(path.relative_to(STATE.parent.parent)))
    url = os.environ.get("TRUST_LAYER_REPORT_URL")
    if url:
        req = urllib.request.Request(url, data=json.dumps(report).encode(), headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            out.append(url)
        except OSError as e:
            out.append(f"{url} (mislukt: {e})")
    return out


def history():
    folder = STATE / "reports"
    if not folder.exists():
        return []
    rows = []
    for p in sorted(folder.glob("eval-*.json"))[-20:]:
        r = json.loads(p.read_text(encoding="utf-8"))
        rows.append({"run_at": r["run_at"], "overall_score": r["overall_score"],
                     "golden": f"{r['golden_passed']}/{r['golden_total']}"})
    return rows


if __name__ == "__main__":
    from .kb import KnowledgeBase
    rep = run(KnowledgeBase(persist=False), save=False)
    print(f"overall_score {rep['overall_score']}  ({rep['checks_total'] - rep['checks_failed']}/{rep['checks_total']} checks)")
    for c in rep["cases"]:
        print(("PASS " if c["pass"] else "FAIL ") + c["id"], c["question"])
        for x in c["checks"]:
            if not x["pass"]:
                print("     x", x["check"], x["detail"])
    for k, v in rep["rules"].items():
        print(("PASS " if v["pass"] else "FAIL ") + k, v.get("failed") or "")
    print("judge:", rep["judge"]["status"])
