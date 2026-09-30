"""Vouch - demo-server (alleen Python-standaardbibliotheek).

    python server.py            -> http://localhost:8000
    python server.py --port 9000

Beveiliging (zie README, sectie Beveiliging):
  * elke /api-route behalve login vereist een geldige sessie (HttpOnly-cookie)
  * rol, gebruiker en toegestane companies komen uit de sessie, nooit uit het request
  * per route een permissie, strikte invoervalidatie en rate limiting
  * POST vereist een same-origin request met de header X-Requested-With (CSRF)
  * security headers op elke response, centrale foutafhandeling, auditlog
"""
import argparse
import copy
import base64
import json
import os
import re
import sys
import traceback
import uuid
from datetime import date
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from trust import auth, engine, files, llm, specialist, test_agent
from trust import validate as v
from trust.kb import STATE, KnowledgeBase

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
KB = KnowledgeBase()
AUTH = auth.Auth(STATE)
LIMITER = auth.RateLimiter()
PRODUCTION = os.environ.get("TRUST_LAYER_ENV") == "production"
SECURE_COOKIES = PRODUCTION or os.environ.get("TRUST_LAYER_SECURE_COOKIES") == "1"
MAX_BODY = 25 * 1024 * 1024

DOC_FIELDS = ("id", "title", "version", "source_type", "published", "valid_from", "country",
              "owner", "validated", "author", "chain_id")
SOURCE_TYPES = ("contract", "policy", "legal", "procedure", "handbook", "mail", "teams")
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                                "form-action 'self'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class RawFile:
    def __init__(self, name, data):
        self.name, self.data = name, data


class Ctx:
    """Alles wat een handler mag gebruiken: de sessiegebruiker en gevalideerde invoer."""

    def __init__(self, user, params, body, ip):
        self.user, self.params, self.body, self.ip = user, params, body, ip
        self.set_cookie = None

    def company(self, cid=None):
        cid = cid or self.params.get("company")
        if cid not in KB.companies:
            raise HTTPError(404, "company not found")
        if cid not in self.user["companies"]:
            raise HTTPError(403, "no access to this company")
        return cid

    def peildatum(self):
        raw = self.params.get("date")
        return date.fromisoformat(raw) if raw else date.today()


def item_json(item):
    out = {k: val for k, val in item.items() if k != "doc"}
    out["doc"] = {k: item["doc"].get(k) for k in DOC_FIELDS}
    out["doc"]["label"] = specialist.doc_label(item["doc"])
    return out


def result_json(r):
    out = {k: val for k, val in r.items() if k not in ("answer", "current", "upcoming", "history", "other_scope")}
    out["answer"] = item_json(r["answer"]) if r.get("answer") else None
    for k in ("current", "upcoming", "history", "other_scope"):
        out[k] = [item_json(i) for i in r.get(k, [])]
    return out


def public_doc(doc):
    out = {k: doc.get(k) for k in DOC_FIELDS + ("text", "company_id")}
    out["file"] = {"name": doc["file"]["name"]} if doc.get("file") else None  # nooit het interne opslagpad
    return out


def kb_for(user):
    """Read-only view of the knowledge base: restricted documents only for HR and supervisors."""
    if auth.sees_restricted(user):
        return KB
    view = copy.copy(KB)
    view.docs = {k: d for k, d in KB.docs.items() if d.get("visibility") != "restricted"}
    view.claims = {k: c for k, c in KB.claims.items() if c["doc_id"] in view.docs}
    return view


def find_doc(ctx, doc_id):
    doc = kb_for(ctx.user).docs.get(doc_id)
    # zelfde antwoord voor 'bestaat niet' en 'hoort bij een andere tenant' (geen IDOR-lek)
    if not doc or doc["company_id"] != ctx.company():
        raise HTTPError(404, "document not found")
    return doc


# ---------------------------------------------------------------- handlers

def api_login(ctx):
    b = ctx.body
    result = AUTH.login(b["email"], b["password"])
    if not result:
        KB.audit(None, "login_failed", ip=ctx.ip)
        raise HTTPError(401, "invalid email or password")
    token, user = result
    ctx.set_cookie = token
    KB.audit(user, "login", ip=ctx.ip)
    return {"user": auth.public_user(user)}


def api_logout(ctx):
    AUTH.logout(ctx.token)
    ctx.set_cookie = ""
    KB.audit(ctx.user, "logout")
    return {"ok": True}


def api_me(ctx):
    return {"user": auth.public_user(ctx.user),
            "permissions": sorted(auth.PERMISSIONS[ctx.user["role"]])}


def api_meta(ctx):
    ok, _ = llm.status()
    return {"companies": [KB.companies[c] for c in ctx.user["companies"] if c in KB.companies],
            "topics": list(KB.topics.values()),
            "llm": {"available": ok, "status": "aan" if ok else "not configured"},
            "today": date.today().isoformat()}


def api_ask(ctx):
    r = engine.ask(kb_for(ctx.user), ctx.company(), ctx.params["q"], ctx.peildatum())
    phrased = specialist.phrase_with_llm(r, r["answer_text"])
    if phrased:
        r["answer_text_llm"] = phrased
    return result_json(r)


def api_trustview(ctx):
    return {"rows": engine.topic_overview(kb_for(ctx.user), ctx.company(), ctx.peildatum())}


def api_health(ctx):
    return {"health": engine.health(kb_for(ctx.user), ctx.company(), ctx.peildatum()), "eval_history": test_agent.history()}


def api_cards(ctx):
    return {"cards": engine.rate_cards(kb_for(ctx.user), ctx.company(), ctx.peildatum(), category=ctx.params.get("category"))}


def api_dashboard(ctx):
    return {"companies": engine.dashboard(kb_for(ctx.user), [c for c in ctx.user["companies"] if c in KB.companies], ctx.peildatum())}


def api_swipe(ctx):
    b = ctx.body
    if b["claim_id"] not in kb_for(ctx.user).claims:
        raise HTTPError(404, "claim not found")
    try:
        event, previous = KB.add_swipe(ctx.user["id"], auth.VOTE_WEIGHT_ROLE[ctx.user["role"]], ctx.user["companies"],
                                       b["claim_id"], b["direction"], b["reason"])
    except LookupError:
        raise HTTPError(404, "claim not found")
    KB.audit(ctx.user, "vote_changed" if previous else "vote", claim_id=b["claim_id"],
             previous=({"direction": previous["direction"], "reason": previous["reason"]} if previous else None),
             new={"direction": event["direction"], "reason": event["reason"]})
    claim = KB.claims[event["claim_id"]]
    return {"vote": {"direction": event["direction"], "reason": event["reason"], "changed": bool(previous)},
            "human": engine.human(KB, claim, date.today())}


def api_doc(ctx):
    doc = find_doc(ctx, ctx.params["id"])
    return {"doc": public_doc(doc), "label": specialist.doc_label(doc),
            "claims": [c for c in KB.claims.values() if c["doc_id"] == doc["id"]],
            "noise": KB.noise.get(doc["id"], []),
            "cards": engine.doc_cards(kb_for(ctx.user), doc["company_id"], doc, ctx.peildatum()),
            "chain": [{"id": d["id"], "label": specialist.doc_label(d), "valid_from": d["valid_from"]}
                      for d in kb_for(ctx.user).chain_members(doc)]}


def api_file(ctx):
    doc = find_doc(ctx, ctx.params["id"])
    original = KB.original_file(doc)
    if not original:
        raise HTTPError(404, "no original file stored for this document")
    return RawFile(*original)


def api_docs(ctx):
    cid = ctx.company()
    docs = sorted(kb_for(ctx.user).company_docs(cid), key=lambda d: (d["title"], d["valid_from"]))
    return {"docs": [{"id": d["id"], "label": specialist.doc_label(d), "source_type": d["source_type"],
                      "chain_id": d.get("chain_id"), "valid_from": d["valid_from"]} for d in docs]}


def _decode_file(f):
    if not f:
        return None
    try:
        data = base64.b64decode(f["data"], validate=True)
    except ValueError:
        raise HTTPError(400, "file could not be decoded")
    if Path(f["name"]).suffix.lower() not in files.SUPPORTED:
        raise HTTPError(415, "file type not supported")
    return f["name"], data


def api_extract(ctx):
    original = _decode_file(ctx.body["file"])
    if not original:
        raise HTTPError(400, "no file sent")
    name, data = original
    out = files.extract_text(name, data)
    out["suggested"] = files.suggest(name, out["text"])
    out["filename"] = name
    out["size"] = len(data)
    return out


def api_ingest(ctx):
    b = ctx.body
    cid = ctx.company(b["company_id"])
    source_type = b["source_type"] or "policy"
    replaces = None
    if b["replaces"]:
        if ctx.user["role"] not in auth.PRIVILEGED:
            raise HTTPError(403, "only HR or supervisors can publish a new version of a document")
        replaces = kb_for(ctx.user).docs.get(b["replaces"])
        if not replaces or replaces["company_id"] != cid:
            raise HTTPError(404, "document to replace not found")
        # geldige overgang: alleen binnen een versieketen, en nooit door een zwakkere bron
        if not replaces.get("chain_id"):
            raise HTTPError(409, "this document has no version chain and cannot be replaced")
        if engine.AUTHORITY.get(source_type, 0) < engine.authority(replaces):
            raise HTTPError(409, "a lower-authority source cannot replace this document")
    published = b["published"] or date.today().isoformat()
    valid_from = b["valid_from"] or published
    slug = re.sub(r"[^a-z0-9]+", "-", b["title"].lower()).strip("-")[:30] or "document"
    doc = {
        "id": f"u-{slug}-{uuid.uuid4().hex[:8]}", "company_id": cid, "title": b["title"],
        "source_type": source_type,
        "chain_id": replaces["chain_id"] if replaces else None,
        "version": b["version"] or ("-" if source_type == "teams" else "v1"),
        "published": published, "valid_from": valid_from, "status": "published",
        "country": b["country"] or KB.companies[cid]["home_country"],
        "owner": {"name": b["owner"], "active": True} if b["owner"] else None,
        # wie valideert, bepaalt de server: de ingelogde HR/admin, met de ingevulde naam als notitie
        "validated": {"by": ctx.user["name"], "role": ctx.user["role"], "note": b["validated_by"], "date": date.today().isoformat()}
        if b["validated_by"] and ctx.user["role"] in auth.PRIVILEGED else None,  # employees cannot approve
        "author": {"name": b["owner"] or ctx.user["name"], "role": b["author_role"] or "employee"}
        if source_type == "teams" else None,
        "uploaded_by": ctx.user["id"],
        "text": b["text"],
    }
    claims, noise, method = specialist.extract(doc, list(KB.topics.values()), use_llm=llm.available())
    when = max(date.fromisoformat(valid_from), date.today())
    impact = []
    for c in claims:
        r = engine.resolve(kb_for(ctx.user), cid, c["topic"], when, c["country"])
        topic = KB.topics[c["topic"]]["label"]
        if replaces and any(i["doc"]["id"] == replaces["id"] for i in r["current"] + r["upcoming"]):
            impact.append({"claim": c["display"], "topic": topic, "kind": "replaces",
                           "text": f"replaces {specialist.doc_label(replaces)}; the old version moves to history (archive proposal)"})
        for i in r["current"]:
            if replaces and i["doc"]["id"] == replaces["id"]:
                continue
            same = i["claim"]["value"] == c["value"]
            impact.append({"claim": c["display"], "topic": topic, "kind": "confirms" if same else "conflict",
                           "text": f"{'confirms' if same else 'contradicts'}: {specialist.doc_label(i['doc'])} "
                                   f"({i['claim']['display']}, trust {i['trust']['score']})"})
        if not r["current"] and not replaces:
            impact.append({"claim": c["display"], "topic": topic, "kind": "new", "text": "new topic for this company"})
    committed = False
    if b["commit"]:
        if not claims:
            raise HTTPError(422, "no claims found: treated as noise and not saved")
        KB.add_document(doc, claims, original=_decode_file(b["file"]))
        committed = True
        KB.audit(ctx.user, "publish", doc_id=doc["id"], claim_ids=[c["id"] for c in claims],
                 previous=None, new={"state": "published", "valid_from": valid_from, "validated": bool(doc["validated"])})
        if replaces:
            KB.audit(ctx.user, "supersede", doc_id=replaces["id"], replaced_by=doc["id"],
                     previous={"state": "current"}, new={"state": "superseded", "from": valid_from})
    return {"doc": {k: doc.get(k) for k in DOC_FIELDS}, "claims": claims, "noise": noise, "method": method,
            "impact": impact, "committed": committed}


def api_eval(ctx):
    report = test_agent.run(KB, use_judge=True, save=True)
    KB.audit(ctx.user, "eval_run", overall_score=report["overall_score"])
    return report


def api_eval_history(ctx):
    return {"history": test_agent.history()}


# ---------------------------------------------------------------- routes: handler, permissie, rate limit/min, validatie

COMPANY_Q = {"company": v.ident(), "date": v.iso_date()}
ROUTES = {
    ("POST", "/api/login"): (api_login, None, 10, None, {"email": v.text(200, True), "password": v.text(200, True)}),
    ("POST", "/api/logout"): (api_logout, "", 30, None, {}),
    ("GET", "/api/me"): (api_me, "", 120, {}, None),
    ("GET", "/api/meta"): (api_meta, "", 120, {}, None),
    ("GET", "/api/ask"): (api_ask, "search", 60, {**COMPANY_Q, "q": v.text(500, True)}, None),
    ("GET", "/api/trustview"): (api_trustview, "search", 60, COMPANY_Q, None),
    ("GET", "/api/cards"): (api_cards, "search", 60, {**COMPANY_Q, "category": v.text(80)}, None),
    ("GET", "/api/dashboard"): (api_dashboard, "search", 60, {"date": v.iso_date()}, None),
    ("GET", "/api/health"): (api_health, "health", 30, COMPANY_Q, None),
    ("GET", "/api/doc"): (api_doc, "read_docs", 120, {**COMPANY_Q, "id": v.ident()}, None),
    ("GET", "/api/file"): (api_file, "read_docs", 30, {**COMPANY_Q, "id": v.ident()}, None),
    ("GET", "/api/docs"): (api_docs, "read_docs", 60, COMPANY_Q, None),
    ("GET", "/api/eval/history"): (api_eval_history, "health", 30, {}, None),
    ("POST", "/api/swipe"): (api_swipe, "vote", 60, None, {
        "claim_id": v.ident(), "direction": v.enum("right", "left", required=True),
        "reason": v.enum("verouderd", "onduidelijk", "fout")}),
    ("POST", "/api/extract"): (api_extract, "ingest", 10, None, {"file": v.upload(21 * 1024 * 1024)}),
    ("POST", "/api/ingest"): (api_ingest, "ingest", 20, None, {
        "company_id": v.ident(), "title": v.text(200, True), "source_type": v.enum(*SOURCE_TYPES),
        "version": v.text(40), "replaces": v.ident(required=False), "published": v.iso_date(),
        "valid_from": v.iso_date(), "country": v.enum("BE", "NL"), "owner": v.text(120),
        "validated_by": v.text(120), "text": v.text(200_000, True), "commit": v.boolean(),
        "author_role": v.enum("employee", "payroll_expert", "payroll_consultant"),
        "file": v.upload(21 * 1024 * 1024)}),
    ("POST", "/api/eval"): (api_eval, "eval", 3, None, {}),
}


class Handler(SimpleHTTPRequestHandler):
    server_version = "TrustLayer"
    sys_version = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def end_headers(self):
        for k, val in SECURITY_HEADERS.items():
            self.send_header(k, val)
        if PRODUCTION:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def _token(self):
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        return cookie[auth.COOKIE].value if auth.COOKIE in cookie else None

    def _same_origin(self):
        if self.headers.get("X-Requested-With") != "trustlayer":
            return False
        origin = self.headers.get("Origin")
        if origin:
            host = self.headers.get("Host", "")
            return urlparse(origin).netloc == host
        return True

    def _api(self, method):
        url = urlparse(self.path)
        route = ROUTES.get((method, url.path))
        ip = self.client_address[0]
        try:
            if route is None:
                raise HTTPError(404, "unknown route")
            handler, permission, limit, qspec, bspec = route
            if method == "POST" and not self._same_origin():
                raise HTTPError(403, "cross-site request refused")
            token = self._token()
            user = AUTH.user_for(token)
            if permission is not None:
                if user is None:
                    raise HTTPError(401, "not signed in")
                if permission and not auth.can(user, permission):
                    raise HTTPError(403, "your role is not allowed to do this")
            key = f"{user['id'] if user else ip}:{url.path}"
            if not LIMITER.allow(key, limit):
                raise HTTPError(429, "too many requests, try again shortly")
            params = v.query(parse_qs(url.query), qspec or {})
            body = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise HTTPError(413, "request too large")
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise HTTPError(400, "invalid JSON")
                body = v.body(body, bspec or {})
            ctx = Ctx(user, params, body, ip)
            ctx.token = token
            out = handler(ctx)
            if isinstance(out, RawFile):
                return self._send_file(out)
            self._send(200, out, ctx.set_cookie)
        except HTTPError as e:
            self._send(e.status, {"error": e.message})
        except v.Invalid as e:
            self._send(400, {"error": str(e)})
        except ValueError as e:  # nette meldingen uit onze eigen modules (bv. files.extract_text)
            self._send(400, {"error": str(e)})
        except Exception:  # centrale foutafhandeling: details alleen in de serverlog
            traceback.print_exc(file=sys.stderr)
            self._send(500, {"error": "internal error"})

    def _send(self, status, payload, cookie=None):
        data = json.dumps(payload, ensure_ascii=False, default=list).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            attrs = f"; Path=/; HttpOnly; SameSite=Strict{'; Secure' if SECURE_COOKIES else ''}"
            if cookie:
                self.send_header("Set-Cookie", f"{auth.COOKIE}={cookie}; Max-Age={auth.SESSION_TTL}{attrs}")
            else:
                self.send_header("Set-Cookie", f"{auth.COOKIE}=; Max-Age=0{attrs}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, f):
        self.send_response(200)
        self.send_header("Content-Type", files.content_type(f.name))
        disposition = "inline" if f.name.lower().endswith(".pdf") else "attachment"
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(f.name)}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(f.data)))
        self.end_headers()
        self.wfile.write(f.data)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api("GET")
        return super().do_GET()

    def do_POST(self):
        return self._api("POST")

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            # geen querystring in de log: zoekvragen kunnen persoonsgegevens bevatten
            args = (str(args[0]).split("?")[0],) + args[1:]
            super().log_message(fmt, *args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    ok, why = llm.status()
    print(f"Vouch op http://localhost:{args.port}")
    print(f"  {len(KB.docs)} documenten, {len(KB.claims)} claims, {len(KB.companies)} companies")
    print(f"  LLM: {'aan' if ok else 'uit'} ({why})")
    if not SECURE_COOKIES:
        print("  Let op: cookies zonder Secure-vlag (lokale HTTP). Zet TRUST_LAYER_ENV=production achter HTTPS.")
    sys.stdout.flush()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
