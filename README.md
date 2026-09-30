# Vouch

**Trusted HR & payroll knowledge, with evidence.**
*Find it → Understand it → Trust it.*

Built for the Tectonic Hackathon (SD Worx challenge).

HR and payroll knowledge is spread over contracts, policies, handbooks, emails and Teams chats.
The same question often has several "truths". Vouch extracts **claims** from every source and
scores each one on **trust**, **relevance** and **human feedback** (swipes). A popular old rule
**never** wins from the rule that is valid today. A **Test Agent** proves this in code.

---

## Quick start

Requirements: Python 3.10 or newer. No other packages are needed.

```bash
cd trust-layer
python server.py
```

Open <http://localhost:8000>.

On the first start Vouch creates the demo accounts and prints a one-time random password in the console.
To choose the password yourself, set `TRUST_LAYER_DEMO_PASSWORD` before the first start. To reset the
accounts, delete `trust-layer/data/state/users.json`.

| Account | Role | Access |
| --- | --- | --- |
| `medewerker@brouwer.demo` | Employee | Brouwer Logistics |
| `hr@brouwer.demo` | HR | Brouwer Logistics, including confidential documents |
| `medewerker@verhoeven.demo` | Employee | Verhoeven Retail |
| `admin@trustlayer.demo` | Supervisor | All companies, including confidential documents and the Test Agent |

Optional extras:

```bash
pip install pypdf
```

```bash
pip install anthropic
```

`pypdf` enables PDF upload. `anthropic` enables Claude: set `ANTHROPIC_API_KEY` to get smoother answers,
LLM claim extraction and the LLM judge.

---

## What you can do

| Tab | What it does | Who |
| --- | --- | --- |
| **Dashboard** | Your companies, each split into categories (Compensation & benefits, Payroll processing, Working time), with a green, orange or red status per topic. Click a category to swipe through its sources. | Everyone |
| **Search** | Ask a question ("How much is the meal voucher?") or search documents. You get the answer, a trust score, a trust receipt and a swipeable deck of every source. | Everyone |
| **Upload** | Drag in a PDF, Word or text file. Vouch extracts the rules, shows the impact (confirms, contradicts or replaces) and keeps the original file. | Everyone (only HR and Supervisor can validate documents or publish a new version) |
| **Health** | Conflicts, popular-but-replaced documents, "high trust but nobody understands it", archive proposals and missing owners. | HR, Supervisor |
| **Test Agent** | Runs the automated quality check (see below). | Supervisor |

Every source card shows:
- a **verdict**: Current answer, Conflicting, Outdated, Not yet valid or Other scope;
- **flags**: which document it contradicts, what replaced it, its age, whether it has an owner, whether it was validated;
- **"Why X% trust?"**: every trust factor with its weight.

Swipe right means useful. Swipe left means not ok, with a reason: outdated, unclear or wrong. The ‹ › buttons browse without voting.

---

## How it works

```
Document / Teams message
   → Specialist Agent: extracts claims + topic (rules; Claude optional)
   → Knowledge base, strictly per company
Question
   → gates: your company → relevant (topic + country) → valid on the reference date
   → ranking: 0.5 × trust + 0.3 × relevance + 0.2 × human score
     (if valid sources contradict each other: trust only)
   → answer + trust receipt
Test Agent → golden questions + invariants + stress test → report
```

**Trust** is computed on the server from five factors: authority of the source type (contract or policy 1.0 … Teams 0.3),
recency, validation, active owner, and consistency with other valid sources.
**Relevance** comes from topic match, country scope and word overlap.
**Human score** comes from swipes. HR and supervisor votes count double, and votes lose half their weight each year.

### The three hard rules

1. **The rule that is valid on the reference date wins.** A future version shows as a badge ("changes on 2027-01-01"). Older versions go to history.
2. **Replacement is not the same as conflict.** Only a newer version in the same document chain, with equal or higher authority, replaces an older one. Different sources that disagree are shown as a conflict and flagged for HR.
3. **Swipes never settle a conflict.** Popularity can only reorder sources that say the same thing.

---

## The Test Agent

An automated audit of the Specialist Agent. It checks:

- **11 golden questions** with known correct behaviour, for example "meal voucher = €7 today, €8 from 2027, the handbook conflict is shown".
- **Invariants over every company × topic × country × date**:
  - the valid version wins;
  - there are no leaks between companies;
  - the answer is valid on the reference date;
  - conflicts are always visible.
- **A stress test**: about 19,000 fake "useful" votes on outdated and weaker sources must change no answer.
- **Teams noise**: chat such as "lunch anyone?" must produce no claims.

The result is a score (currently 372/372 checks) with PASS or FAIL per rule. The report is saved in
`data/state/reports/`. If `TRUST_LAYER_REPORT_URL` is set, the report is also posted as JSON to that URL.

From the command line:

```bash
cd trust-layer
python -m trust.test_agent
```

```bash
python -m unittest discover tests -v
```

---

## Security

- **Authentication**: sign-in is required for every API call. Passwords are hashed with PBKDF2-SHA256. Sessions use an `HttpOnly`, `SameSite=Strict` cookie; it is also `Secure` and HSTS is added when `TRUST_LAYER_ENV=production`.
- **Authorization**: role, user and company access come from the session only. Requests that contain fields such as `role` or `user_id` are rejected.
- **Tenant isolation / IDOR**: every document, file and claim is checked against the user's companies. Confidential documents are only visible to HR and supervisors.
- **Votes**: one vote per user per claim; voting again replaces your earlier vote. Vote weights and trust scores are always computed on the server.
- **State transitions**: only HR and supervisors can validate or replace documents, and never with a weaker source.
- **Input validation**: strict schemas per endpoint (unknown fields → 400), length limits, enums, date checks, file type and size checks.
- **API hardening**: rate limiting (login, search, votes, uploads, Test Agent), CSRF protection, centralized error handling without stack traces, `Cache-Control: no-store`.
- **Security headers**: CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`.
- **Audit log** (`data/state/audit.log`): logins, votes (previous and new), publications, replacements and Test Agent runs, each with who and when. Passwords and tokens are never logged.
- **Secrets** live only in environment variables. `data/state/` and `.env` are git-ignored.

---

## Project structure

```
tectonichackathondepatrons/
├── README.md                 ← this file
└── trust-layer/              ← Vouch app
    ├── server.py             HTTP server + JSON API (auth, validation, headers)
    ├── trust/
    │   ├── engine.py         gates, trust, relevance, human score, ranking, dashboard, health
    │   ├── specialist.py     Specialist Agent: claim extraction, noise filter, answer text
    │   ├── test_agent.py     Test Agent + report
    │   ├── kb.py             knowledge base, votes, uploads, audit log
    │   ├── auth.py           accounts, sessions, roles, rate limiting
    │   ├── validate.py       input validation
    │   ├── files.py          PDF / Word / text extraction
    │   └── llm.py            optional Claude integration
    ├── data/corpus.json      demo dataset (fictional): 2 companies, 23 documents, 10 Teams messages
    ├── web/                  frontend (vanilla JS) + logo
    └── tests/                unit tests
```

---

## Honest scope

This is a proof of concept on a fictional demo dataset. The trust score is an explainable composite score,
not a calibrated probability. Relevance uses keywords instead of embeddings. The source documents are in
Dutch; English questions work for common HR terms. Storage is JSON files instead of a database, and
sessions and rate limits live in memory. A production version would need SharePoint and Teams connectors,
PII redaction at ingest, a real database and SSO.
