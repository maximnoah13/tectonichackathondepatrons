"""Knowledge base: documenten, geëxtraheerde claims en menselijke signalen.

Multi-tenant: alles draagt een company_id. De originele documenten blijven bewaard
(ook vervangen versies), voor audit en voor vragen over het verleden.
"""
import copy
import json
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from . import specialist

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus.json"
STATE = ROOT / "data" / "state"


class KnowledgeBase:
    def __init__(self, corpus_path=CORPUS, state_dir=STATE, persist=True):
        self.persist = persist
        self.state_dir = Path(state_dir)
        self._lock = threading.Lock()
        corpus = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
        self.companies = {c["id"]: c for c in corpus["companies"]}
        self.topics = {t["id"]: t for t in corpus["topics"]}
        self.seed_votes = {v["doc_id"]: v for v in corpus["seed_votes"]}
        self.docs, self.claims, self.noise = {}, {}, {}
        for doc in corpus["documents"]:
            self._index(doc)
        self.swipes = []
        self._overlay = []  # tijdelijke stemmen voor stresstests, nooit opgeslagen
        if persist:
            for doc in self._load("ingested.json", []):
                self._index(doc)
            self.swipes = self._load("swipes.json", [])

    # ------------------------------------------------------------ opslag
    def _load(self, name, default):
        path = self.state_dir / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return default

    def _save(self, name, data):
        if not self.persist:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def _index(self, doc, claims=None):
        if claims is None:
            claims, noise, _ = specialist.extract(doc, list(self.topics.values()))
        else:
            noise = []
        self.docs[doc["id"]] = doc
        self.noise[doc["id"]] = noise
        for c in claims:
            self.claims[c["id"]] = c

    # ------------------------------------------------------------ queries
    def company_claims(self, company_id):
        return [c for c in self.claims.values() if c["company_id"] == company_id]

    def company_docs(self, company_id):
        return [d for d in self.docs.values() if d["company_id"] == company_id]

    def doc_of(self, claim):
        return self.docs[claim["doc_id"]]

    def chain_members(self, doc):
        if not doc.get("chain_id"):
            return [doc]
        return sorted(
            (d for d in self.docs.values()
             if d["company_id"] == doc["company_id"] and d.get("chain_id") == doc["chain_id"]
             and d.get("status") == "published"),
            key=lambda d: d["valid_from"],
        )

    def votes_for(self, claim):
        """Seed-stemmen (documentniveau) + live swipes (claimniveau) + stresstest-overlay."""
        events = []
        seed = self.seed_votes.get(claim["doc_id"])
        if seed:
            events.append({"direction": "right", "count": seed["right"], "hr_share": seed["hr_share"], "date": seed["as_of"]})
            for reason, n in seed["left"].items():
                events.append({"direction": "left", "reason": reason, "count": n, "hr_share": seed["hr_share"], "date": seed["as_of"]})
        for s in self.swipes + self._overlay:
            if s["claim_id"] == claim["id"]:
                events.append({"direction": s["direction"], "reason": s.get("reason"), "count": 1,
                               "hr_share": 1.0 if s.get("role") == "hr" else 0.0, "date": s["date"]})
        return events

    # ------------------------------------------------------------ schrijven
    def add_swipe(self, user_id, weight_role, allowed_companies, claim_id, direction, reason=None):
        """Eén stem per gebruiker per claim (unieke sleutel user_id + claim_id).

        Een tweede stem vervangt de eerste (je mag van mening veranderen), maar telt nooit dubbel.
        Gebruiker en rol komen uit de sessie, niet uit het request. Geeft (event, vorige_stem).
        """
        claim = self.claims.get(claim_id)
        if claim is None or claim["company_id"] not in allowed_companies:
            raise LookupError("claim niet gevonden")  # zelfde antwoord voor 'bestaat niet' en 'andere tenant'
        if direction not in ("right", "left"):
            raise ValueError("direction must be 'right' or 'left'")
        if direction == "left" and reason not in ("verouderd", "onduidelijk", "fout"):
            raise ValueError("a left swipe needs a reason")
        event = {"user_id": user_id, "claim_id": claim_id, "company_id": claim["company_id"], "direction": direction,
                 "reason": reason if direction == "left" else None,
                 "role": "hr" if weight_role == "hr" else "employee", "date": date.today().isoformat()}
        with self._lock:
            previous = next((s for s in self.swipes if s.get("user_id") == user_id and s["claim_id"] == claim_id), None)
            self.swipes = [s for s in self.swipes if not (s.get("user_id") == user_id and s["claim_id"] == claim_id)]
            self.swipes.append(event)
            self._save("swipes.json", self.swipes)
        return event, previous

    def audit(self, user, action, **details):
        """Append-only auditlog: wie, wat, welke claim/document, wanneer, vorige en nieuwe staat."""
        if not self.persist:
            return
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "user_id": user["id"] if user else None, "role": user["role"] if user else None,
                 "action": action, **details}
        with self._lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with open(self.state_dir / "audit.log", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def add_document(self, doc, claims, original=None):
        """`original` = (bestandsnaam, bytes): het originele bestand wordt bewaard voor audit."""
        with self._lock:
            if original and self.persist:
                name, data = original
                folder = self.state_dir / "files"
                folder.mkdir(parents=True, exist_ok=True)
                stored = doc["id"] + Path(name).suffix.lower()
                (folder / stored).write_bytes(data)
                doc["file"] = {"name": name, "stored": stored}
            self._index(doc, claims)
            ingested = self._load("ingested.json", []) if self.persist else []
            ingested.append(doc)
            self._save("ingested.json", ingested)

    def original_file(self, doc):
        info = doc.get("file")
        if not info:
            return None
        path = self.state_dir / "files" / info["stored"]
        return (info["name"], path.read_bytes()) if path.exists() else None

    @contextmanager
    def vote_overlay(self, events):
        """Tijdelijke extra stemmen, bv. om te testen dat populariteit geen versie verslaat."""
        self._overlay = copy.deepcopy(events)
        try:
            yield self
        finally:
            self._overlay = []
