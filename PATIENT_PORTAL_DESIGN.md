# Patient Portal — Design & Implementation Plan

A patient-facing module where a discharged patient signs in and sees the current
state of their own transition-of-care case, with PHI-grade access control.

---

## 1. Why this is not "just another page"

Everything shipped so far is a **clinician-facing internal tool with no
authentication**. A patient portal changes the threat model completely:

| | Today (staff console) | Patient portal |
|---|---|---|
| Users | Trusted, internal | Untrusted, internet-facing |
| Population | One team | Every discharged patient |
| Main risk | — | One patient reading another's record (IDOR) |
| Identity | None needed | Must bind a human to a `patient_id` |
| Regulation | — | HIPAA Security Rule §164.312 |

The single most likely breach is **horizontal authorization failure**: patient A
fetches patient B's case by changing an ID. Every decision below is shaped by
preventing that.

### 1.1 The portal *introduces* direct identifiers

The database today holds **no direct identifiers** — no name, DOB, MRN, address,
phone, or email. `patient_id` is a pseudonym (`patient-b691fe`). What it does
hold is PHI-adjacent clinical data: diagnosis, discharge date, disposition,
payer, facility.

A portal requires knowing *which human* is `patient-b691fe`. That linkage is the
most sensitive data in the system, and it does not exist yet. **Do not add name
/ email / DOB columns to `cases`.** Keep the linkage in a separate, separately
governed table (§4).

---

## 2. Findings in the current codebase (fix before the portal ships)

Verified against the running system, not assumed:

| # | Finding | Evidence | Severity |
|---|---|---|---|
| 1 | **No authentication on any endpoint.** `/api/cases` lists every case with diagnosis + payer to any caller who can reach port 8010. | No auth code anywhere in `backend/src/carebridge/` | Critical |
| 2 | **`/api/logs/tail` and `/api/logs/stream` are public** and stream the internal event log. | `api/main.py` — no dependency, no guard | Critical |
| 3 | **App connects to Postgres as the `postgres` superuser**, which is also the table owner. Superusers and table owners **bypass row-level security by default**. | `DATABASE_URL=postgres:postgres@…`; `rolsuper = t`; `cases.tableowner = postgres` | High — blocks the RLS control in §6 |
| 4 | **PHI sits in `audit_log.input_summary` as free text** — e.g. `case-A · home · Type 2 diabetes, controlled · payer=Medicare`, produced by `TransitionCase.summary()`. | `SELECT input_summary FROM audit_log` | Medium — legitimate location, but must be access-controlled and encrypted, not streamed to `/api/logs` |
| 5 | **CORS is not a security control.** `allow_origins=["http://localhost:3010"]` stops browsers, not `curl`. | `api/main.py:110` | Informational |
| 6 | **Rate limiter is per-IP and in-process.** No per-account lockout; resets on restart; wrong across multiple workers. | `middleware.py` | Medium (login brute-force) |
| 7 | **No CSRF protection.** Becomes exploitable the moment cookie auth is added. | — | High (once §5 lands) |
| 8 | **Diagnosis + payer are sent to the Anthropic API** when `LLM_PROVIDER=anthropic`. | `llm.py` | Blocking — see §10 |
| 9 | **Live API key in `.env`, DB password is `postgres`.** | `.env` | High |

Item 3 is the one that quietly defeats the strongest control in this design.
Fix it first.

---

## 3. Architecture

Keep the portal a **separate trust zone**, not a new route on the staff API.

```
                    ┌───────────────────────────────┐
   Patient  ──TLS──▶│  Portal frontend (/portal)    │
                    │  separate origin + layout     │
                    └──────────────┬────────────────┘
                                   │ httpOnly session cookie
                                   ▼
                    ┌───────────────────────────────┐
                    │  /api/portal/me/*             │  ← never takes an id
                    │  session → patient_id         │     from the client
                    └──────────────┬────────────────┘
                                   │ DB role: carebridge_portal
                                   │ (non-owner, RLS FORCEd)
                    ┌──────────────▼────────────────┐
                    │  portal_case_view             │  ← allowlist projection
                    └──────────────┬────────────────┘
                                   ▼
   ┌──────────────────┐   ┌────────────────┐   ┌──────────────────┐
   │ schema: clinical │   │ schema: portal │   │ phi_access_log   │
   │ cases, events,   │   │ portal_user    │   │ append-only      │
   │ agent_decisions  │   │ enrollment_tok │   │ (who read what)  │
   └──────────────────┘   └────────────────┘   └──────────────────┘
        staff API only      identity linkage        compliance

   Staff console (existing) ──▶ /api/cases, /api/logs  [staff auth required]
```

Three properties fall out of this shape:

1. The portal API has **no route that accepts a case id or patient id** as
   input. Identity comes from the session, server-side, always.
2. The portal DB role can read **only** `portal_case_view`, and RLS restricts
   even that to the session's own patient. Two independent controls.
3. The identity table lives in its own schema so the clinical database never
   gains direct identifiers.

---

## 4. Data model

```sql
CREATE SCHEMA portal;

-- The identity linkage. The most sensitive table in the system.
CREATE TABLE portal.portal_user (
    portal_user_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id       text NOT NULL UNIQUE,     -- FK in spirit to clinical.cases
    email_encrypted  bytea NOT NULL,           -- pgcrypto; key in KMS, not .env
    email_hmac       bytea NOT NULL UNIQUE,    -- deterministic, for lookup on login
    mfa_secret_enc   bytea,
    status           text NOT NULL DEFAULT 'invited',  -- invited|active|locked|revoked
    failed_logins    int  NOT NULL DEFAULT 0,
    last_login_at    timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- Out-of-band enrollment. Never let a user self-assert a patient_id.
CREATE TABLE portal.enrollment_token (
    token_hash   bytea PRIMARY KEY,            -- store the hash, never the token
    patient_id   text        NOT NULL,
    expires_at   timestamptz NOT NULL,         -- 72h
    used_at      timestamptz,                  -- single use
    issued_by    text        NOT NULL          -- staff user who discharged them
);

-- HIPAA §164.312(b) audit controls: every PHI *read*, not just writes.
CREATE TABLE portal.phi_access_log (
    id             bigserial PRIMARY KEY,
    occurred_at    timestamptz NOT NULL DEFAULT now(),
    actor_type     text NOT NULL,              -- patient|staff|system
    actor_id       text NOT NULL,
    patient_id     text,                       -- subject of the record
    case_id        text,
    action         text NOT NULL,              -- read_case|list_cases|login|...
    outcome        text NOT NULL,              -- allow|deny
    source_ip      inet,
    user_agent     text
);
```

Note what is **not** in `portal_user`: no password hash. See §5.

Email is stored twice — encrypted (to read back) and HMAC'd with a server-side
key (to look up on login without decrypting every row). A plain hash would be
vulnerable to enumeration; a bare index on ciphertext wouldn't be searchable.

---

## 5. Authentication

**Do not build password auth.** Passwords bring reset flows, breach reuse, and
hashing decisions you don't want to own for PHI. Two acceptable options:

- **Recommended for production:** an OIDC identity provider (Keycloak self-hosted,
  or Auth0/Cognito with a BAA). Delegate credentials, MFA, and lockout entirely.
- **Acceptable for a demo/MVP:** passwordless magic link — patient enters email,
  receives a single-use, 10-minute, hashed-at-rest token; clicking it mints a
  session. No credential to steal, no password store to breach.

If you are ever forced into passwords: argon2id, and MFA is mandatory anyway.

**Session handling** — the details here matter more than the mechanism:

| Control | Value | Why |
|---|---|---|
| Transport | `httpOnly; Secure; SameSite=Strict` cookie | A JWT in `localStorage` is exfiltratable by any XSS. Non-negotiable. |
| Idle timeout | 15 minutes | Shared/family devices |
| Absolute timeout | 12 hours | Bounds a stolen cookie |
| Rotation | New session id on login, on MFA, on privilege change | Session fixation |
| Revocation | Server-side session table | Stateless JWTs can't be revoked on logout/lockout |
| CSRF | Double-submit token **and** `SameSite=Strict` | Cookie auth reintroduces CSRF (finding #7) |

**Enrollment / identity proofing** is where portals actually get breached. The
only safe flow: at discharge, staff issue a one-time `enrollment_token` bound to
that `patient_id`, delivered **out of band** (printed in discharge paperwork, or
sent to an address already on file in the EHR). The patient exchanges token +
email for an account. There is **no** path where a user types a `patient_id` and
gets access to it.

---

## 6. Authorization — three independent layers

Any one of these prevents patient A from reading patient B. Build all three;
they fail in different ways.

### Layer 1 — the route shape makes IDOR unrepresentable

```
GET  /api/portal/me/cases          ← list my cases
GET  /api/portal/me/cases/{id}     ← id is validated to be MINE, never trusted
POST /api/portal/me/acknowledge    ← the only write
```

There is no `/api/portal/cases/{id}`. `patient_id` is read from the session and
never accepted as a parameter, header, or body field.

### Layer 2 — a repository chokepoint, not per-route checks

Per-route `if` statements get forgotten on the tenth route. Force the filter into
the only function that can reach the table:

```python
def fetch_my_cases(session_patient_id: str) -> list[PortalCase]:
    # The WHERE clause is not optional and not caller-supplied.
    return db.query(PortalCaseView).filter_by(patient_id=session_patient_id).all()
```

Portal routes are forbidden (by code review and a lint rule) from importing
`CaseRecord` directly.

### Layer 3 — Postgres row-level security (defense in depth)

Even a buggy query cannot cross patients:

```sql
ALTER TABLE clinical.cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.cases FORCE  ROW LEVEL SECURITY;   -- see below

CREATE POLICY portal_own_rows ON clinical.cases
    FOR SELECT TO carebridge_portal
    USING (patient_id = current_setting('app.patient_id', true));
```

Per transaction, the app sets `SET LOCAL app.patient_id = :session_patient_id`.

> **This is finding #3.** RLS is silently skipped for superusers and for the
> table owner. Today the app is `postgres`, which is both. Without a dedicated
> non-owner `carebridge_portal` role **and** `FORCE ROW LEVEL SECURITY`, the
> policy above is decorative. Verify with a test that asserts a cross-patient
> query returns zero rows *while connected as the app role*.

**Deny by default:** `REVOKE ALL ON ALL TABLES IN SCHEMA clinical FROM
carebridge_portal;` then grant `SELECT` on `portal_case_view` only.

---

## 7. What the patient may see (minimum necessary)

HIPAA's minimum-necessary standard, and plain product sense: a patient gets
their care plan, not the machinery that produced it.

| Internal field | In portal? | Rationale |
|---|---|---|
| `primary_diagnosis`, `discharge_date`, `disposition` | Yes | It's their record |
| Follow-up appointment, medication instructions, referral | Yes | The point of the portal |
| `status` | Mapped | See below |
| `agent_decisions.confidence` | **Never verbatim** | Meaningless and alarming to a patient |
| `agent_decisions.rationale` | **Never verbatim** | Internal model reasoning — but see §13 |
| `audit_log`, `events`, trace waterfall | **No** | Operational internals |
| `payer`, `source_message_id`, `admitting_facility` | **No** | Not needed for their task |
| Any other patient's anything | **No** | — |

The two "never verbatim" rows changed meaning when the chat assistant shipped.
Minimum-necessary **does not apply to disclosures to the individual themselves**
(§164.502(b)(2)(i)) — a patient has a right of access to their own record. So the
constraint on `rationale` was never "the patient may not know this"; it was "this
text is written for clinicians and is useless to them". §13 translates it. No
route serializes either field.

Internal status leaks operational detail; map it:

| Internal | Patient sees |
|---|---|
| `received`, `in_progress` | "We're preparing your plan" |
| `needs_review`, `auto_completed`, `completed` | "A care coordinator is reviewing your plan" |
| any status, once `case_workflow.approved_at IS NOT NULL` | "Your plan is ready" |
| `rejected` | "Please contact your care team" |

**Approval, not the agent pipeline, is what makes a plan "ready."** The agents mark
a case `completed` the moment they agree, which is long before a clinician has
signed anything, and `approved_summary` is NULL until they do. Driving the label
off `completed` told patients their plan was ready above an empty summary panel.
`rejected` wins over approval: it means "contact your care team" whether or not
something was previously signed.

Implement as an **allowlist projection** — a `PortalCaseOut` Pydantic model
built field-by-field. **Never** `return case_row.snapshot`, which is how the
staff route works today and would leak every column the moment one is added.

Add a test that asserts the serialized portal response contains none of
`confidence`, `rationale`, `payer`, `source_message_id`.

---

## 8. Audit logging

Write to `phi_access_log` on **every read**, before returning the response —
HIPAA §164.312(b) is about access, not just mutation.

- Log **denials** too. A spike of `outcome='deny'` from one account is your
  IDOR-attempt detector; alert on it.
- Make it genuinely append-only: `REVOKE UPDATE, DELETE ON portal.phi_access_log
  FROM carebridge_portal;`
- Retain **6 years** (HIPAA §164.316(b)(2)).
- Never log PHI *into* the log — record `case_id`, not the diagnosis. Note
  `TransitionCase.summary()` embeds the diagnosis; do not call it from any
  logging path (this is finding #4).

---

## 9. Frontend

- Separate Next.js route group `app/portal/` with its **own layout** — no shared
  `Nav`, no `TraceWaterfall`, no `ConfidenceMeter`. Shared components are how
  internal fields end up on a patient's screen.
- Ideally a separate deployment/origin (`portal.example.com`) so an XSS in the
  staff console can't reach a patient session cookie.
- Strict CSP; no `dangerouslySetInnerHTML`.
- Auto-logout on idle with a visible countdown.
- "Updated details" — do **not** reuse `/api/logs/stream` (public firehose).
  Poll `/api/portal/me/cases` every 30s, or add a per-patient SSE channel filtered
  server-side by session `patient_id`.
- No PHI or identifiers in URLs or query strings — they land in access logs,
  browser history, and `Referer` headers.

---

## 10. Before real patient data touches this system

These are organizational, not code, and they gate go-live:

1. **BAA with every processor of PHI.** Cloud host, and — because diagnosis and
   payer are sent to the Claude API today (finding #8) — the LLM provider.
   Confirm scope with the vendor before sending real PHI. Until a BAA is signed,
   run `LLM_PROVIDER=local`; Ollama keeps the data on your hardware, which is a
   genuine compliance advantage of the setup you already have.
2. Encryption at rest (disk + `pgcrypto` for identifiers) and in transit (TLS 1.2+,
   HSTS). Keys in a KMS, never `.env`.
3. Rotate the API key and the `postgres` password (finding #9). Move secrets out
   of `.env` into a secrets manager.
4. Backup encryption; test restores.
5. Documented breach-notification procedure; workforce training; periodic access
   reviews.
6. Independent penetration test focused on authorization.

---

## 11. Implementation steps, in order

Each phase is independently shippable and testable. **Phase 0 is not optional** —
several later controls are inert without it.

**Phase 0 — Close the existing holes** *(do this even if the portal never ships)*
1. Add staff authentication to every existing `/api/*` route.
2. Put `/api/logs/tail` and `/api/logs/stream` behind staff auth (finding #2).
3. Create a non-superuser, non-owner `carebridge_app` role; repoint
   `DATABASE_URL`; rotate the password (findings #3, #9).
4. Rotate the exposed Anthropic key.
5. Terminate TLS in front of the API; set HSTS.

**Phase 1 — Data model**
6. `CREATE SCHEMA portal`; add the three tables from §4.
7. Wire `pgcrypto` + KMS-held key for `email_encrypted`.

**Phase 2 — Enrollment & authentication**
8. Staff endpoint to issue an `enrollment_token` at discharge (out-of-band delivery).
9. Magic-link or OIDC login; server-side session store.
10. Cookie flags, idle/absolute timeouts, rotation, CSRF tokens (§5).
11. Per-account lockout + Redis-backed rate limiting on the login route (finding #6).

**Phase 3 — Authorization**
12. `carebridge_portal` DB role; `REVOKE ALL`, then grant `SELECT` on the view only.
13. `ENABLE` + **`FORCE`** row-level security; the `portal_own_rows` policy.
14. `SET LOCAL app.patient_id` per transaction.
15. The repository chokepoint (§6, layer 2).

**Phase 4 — Read model**
16. `portal_case_view` + the `PortalCaseOut` allowlist schema.
17. Internal→patient status mapping.
18. `/api/portal/me/cases` and `/api/portal/me/cases/{id}`.

**Phase 5 — Audit**
19. `phi_access_log` writes on every read and every denial.
20. `REVOKE UPDATE, DELETE`; 6-year retention job.
21. Alert on `outcome='deny'` spikes.

**Phase 6 — Frontend**
22. `app/portal/` route group, isolated layout, CSP, idle logout.
23. Polling or per-patient SSE for live updates.

**Phase 7 — Verification** *(the phase that actually proves the design)*
24. **IDOR test suite:** authenticated as patient A, every portal route with
    patient B's `case_id` must return 404 (not 403 — don't confirm existence).
25. **RLS test:** connected as `carebridge_portal` with `app.patient_id` set to A,
    a raw `SELECT * FROM clinical.cases` returns only A's rows.
26. **Leak test:** serialized portal responses contain no `confidence`,
    `rationale`, `payer`, or `source_message_id`.
27. **Audit test:** every 200 on a portal read produces exactly one
    `phi_access_log` row.
28. Independent penetration test (§10.6).

---

## 12. Recommended first slice

If you want something running this week, the smallest slice that is *actually*
safe rather than demo-safe:

Phase 0 items 1–3 → Phase 1 → magic-link login → `/api/portal/me/cases` behind
the repository chokepoint **and** forced RLS → `PortalCaseOut` projection →
`phi_access_log` → the IDOR and RLS tests (24, 25).

That is roughly 400–600 lines. Everything else — MFA, OIDC, SSE, alerting — layers
on top without redesign. Skipping the RLS role work (Phase 0 item 3) to "save
time" is the one shortcut that invalidates the rest.

---

## 13. The status assistant (patient-facing chat)

A patient whose plan is held up sees "Please contact your care team." and nothing
else. The *reason* — an unconfirmed prior authorization, an out-of-network payer —
lives in `agent_decisions.rationale`, which §7 forbids serializing.

That forbiddance is about **form, not entitlement**. It is the patient's own
record, and minimum-necessary does not bind disclosures to the individual. What
binds us is that `"composite confidence set by weakest signal(s) —
discharge_readiness (0.30)"` is meaningless and frightening. So the assistant
translates it. The rationale is never returned; only a paraphrase of it is.

### The threat model

An LLM sitting between a discharged cardiac patient and their clinical record has
four distinct failure modes, and each gets its own control. **None of the four
trusts the model.**

| # | Failure | Control | Where |
|---|---|---|---|
| 1 | Reads another patient's case | No `patient_id` in the route; session supplies it | `routes.py` |
| 2 | Gives medical advice | Deterministic refusal **before** the model runs | `bot/intent.py` |
| 3 | Prompt injection reaches other data | Context is RLS-bounded before the model sees it | migration 006 |
| 4 | Echoes internal vocabulary | Reply discarded — never repaired — if it does | `bot/redact.py` |

### Why the refusal is not a system prompt

`"You are not a doctor, decline clinical questions"` is a **request**, not a
control. `intent.is_clinical_question()` is a regex filter in Python, the refusal
text is a module constant, and no model authors it. It runs before the database
read, so a clinical question causes no PHI access at all — only a
`chat_refused_clinical` denial row.

Deliberately over-broad. A false positive costs one redirect to the care team,
which is where a clinical question belonged anyway. A false negative costs
something we cannot take back.

### The reason view (migration 006)

```
portal role --SELECT--> portal.portal_case_reason_view   (owner: carebridge_owner)
                                   |
                                   | executes as owner ⇒ owner's RLS applies
                                   v
                  public.agent_decisions JOIN public.cases  (RLS on cases)
```

`agent_decisions` has **no RLS of its own and needs none**: the join to `cases` is
what bounds it. With `app.patient_id` unset, `current_setting(..., true)` is NULL,
`patient_id = NULL` matches nothing, and the view returns zero rows. Fails closed,
exactly like `portal_case_view`. The portal role still has no privilege on either
base table.

Verify at the DB level, not through the app:

```sql
-- as carebridge_portal
SELECT count(*) FROM public.agent_decisions;             -- permission denied
SELECT count(*) FROM portal.portal_case_reason_view;     -- 0  (fails closed)
BEGIN; SELECT set_config('app.patient_id','pt-0001',true);
SELECT count(DISTINCT patient_id) FROM portal.portal_case_reason_view;  -- 1
COMMIT;
```

### Prompt injection

`rationale` is LLM-generated text derived from doctor-uploaded JSON — untrusted,
twice over. It is fenced in a delimited block the system prompt declares to be
data. **That is a mitigation, not a guarantee**, which is exactly why control 4
exists: the reply is checked afterwards regardless of how well the fencing held.

`redact.sanitize()` discards rather than repairs. A partially-scrubbed sentence
about a patient's own care is worse than a clean fallback. It also refuses to log
the matched text — logging a leak is another copy of the leak.

### Statelessness

No history, no conversation id. Nothing for a multi-turn jailbreak to accumulate
in, and every message meets the clinical filter on its own. The transcript in the
UI is display state.

### Audit

Every message is a PHI read: `action='chat'`, `outcome='allow'|'deny'`. Refusals
write `action='chat_refused_clinical'`. Both IDOR shapes — another patient's case
and a nonexistent one — return a byte-identical 404 and log a `deny`.

### Before real patients

**Every chat prompt contains the patient's diagnosis and case notes.** With
`LLM_PROVIDER=anthropic` (or `gemini`) that leaves the machine. A signed BAA with
the vendor is a prerequisite, not a formality. `LLM_PROVIDER=local` runs the same
code path against Ollama with no egress.

Add to §10's checklist:

- [ ] BAA signed with the LLM vendor, **or** `LLM_PROVIDER=local` enforced for the portal
- [ ] `intent.py` patterns reviewed by a clinician, not an engineer
- [ ] Alert on `action='chat_refused_clinical'` spikes — a patient repeatedly asking
      a machine for medical advice is a care-team signal, not just a filter hit
