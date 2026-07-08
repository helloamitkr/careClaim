#!/usr/bin/env bash
# End-to-end test of the clinical workflow, over real HTTP against a running app:
#
#   doctor uploads JSON -> agents run -> admin reviews the draft
#     -> admin sends it back to the doctor -> doctor edits and resubmits
#     -> admin approves -> the summary becomes visible to the patient
#
# Also asserts the things that must NOT happen: a doctor approving their own
# case, the wrong doctor editing a case, a patient reading someone else's record,
# and a patient seeing anything at all before approval.
#
# Usage:  ./scripts/test_flow.sh            (leaves the demo case in the DB)
#         ./scripts/test_flow.sh --cleanup  (removes it afterwards)
#
# Prerequisites: ./start.sh is running, and LLM_AVAILABLE=true in .env.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="${API:-http://localhost:8010}"
CLEANUP=false
[ "${1:-}" = "--cleanup" ] && CLEANUP=true

# Unique per run, so repeated runs never collide on a duplicate case_id.
RUN_ID="$(date +%s)-$$"
CASE_ID="flow-${RUN_ID}"
PATIENT_ID="pt-flow-${RUN_ID}"
OTHER_CASE_ID="flow-other-${RUN_ID}"
OTHER_PATIENT_ID="pt-other-${RUN_ID}"
EMAIL="flow-${RUN_ID}@example.com"
DOCTOR="dr.smith"
ADMIN="admin.jones"

pass=0; fail=0
ok()   { echo "  ✓ $1"; pass=$((pass+1)); }
bad()  { echo "  ✗ $1"; fail=$((fail+1)); }
step() { echo; echo "── $1"; }

# Reads .env without sourcing it (values may contain characters bash would eat).
env_get() { grep -E "^$1=" "$ROOT_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- ; }

json() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

# --- preflight -------------------------------------------------------------
step "Preflight"
HEALTH="$(curl -sf "$API/api/health" 2>/dev/null)" || {
  echo "  ✗ API is not answering at $API — run ./start.sh first"; exit 1; }
ok "API is up"

if [ "$(echo "$HEALTH" | json 'd["llm_available"]')" != "True" ]; then
  echo "  ✗ LLM_AVAILABLE is false — the agents will not run and this flow cannot"
  echo "    complete. Set LLM_AVAILABLE=true in .env and restart the backend."
  exit 1
fi
ok "agents are enabled"

if [ "$(env_get PORTAL_DEV_MODE)" != "true" ]; then
  echo "  ✗ PORTAL_DEV_MODE is not true — patient sign-in needs an enrollment code."
  echo "    Set PORTAL_DEV_MODE=true in .env and restart the backend."
  exit 1
fi
ok "dev mode on (patients sign in with a username)"

# --- 1. doctor uploads -----------------------------------------------------
step "1. Doctor '$DOCTOR' uploads a case"
upload() {  # $1 = case_id, $2 = patient_id
  curl -sf -X POST "$API/api/cases/ingest?uploaded_by=$DOCTOR" \
    -H 'Content-Type: application/json' -d @- <<EOF
{"case_id":"$1","patient_id":"$2","discharge_date":"2026-08-01",
 "discharge_disposition":"home","primary_diagnosis":"Pneumonia, resolved",
 "has_pcp_on_file":true,"payer":"Aetna","referral_specialty":"pulmonology","risk_flags":[]}
EOF
}
upload "$CASE_ID" "$PATIENT_ID" >/dev/null && ok "case $CASE_ID accepted" || bad "upload failed"
# A second patient, purely so we can try to read their record as the first one.
upload "$OTHER_CASE_ID" "$OTHER_PATIENT_ID" >/dev/null && ok "second patient's case created (for the IDOR check)"

# --- 2. agents -------------------------------------------------------------
step "2. Waiting for the agents"
READY=false
for _ in $(seq 1 40); do
  if [ "$(curl -sf "$API/api/cases/$CASE_ID/draft" | json 'd["ready"]')" = "True" ]; then
    READY=true; break
  fi
  sleep 2
done
$READY && ok "all five agents reported; draft is ready" || { bad "agents did not finish in 80s"; exit 1; }

SECTIONS="$(curl -sf "$API/api/cases/$CASE_ID/draft" | json '", ".join(s["heading"] for s in d["sections"])')"
echo "     sections: $SECTIONS"

# --- 3. admin queue --------------------------------------------------------
step "3. Admin queue"
STAGE="$(curl -sf "$API/api/workflow/queue?role=admin" \
  | json "next(c['stage'] for c in d if c['case_id']=='$CASE_ID')")"
[ "$STAGE" = "awaiting_admin" ] && ok "case is awaiting admin approval" || bad "stage=$STAGE, expected awaiting_admin"

# --- 4. patient sees nothing yet -------------------------------------------
step "4. Before approval, the patient must see no summary"
# Username + role. That's the whole sign-in.
patient_signin() {  # $1 = patient_id (the username), $2 = cookie jar
  curl -sf -c "$2" -X POST "$API/api/portal/auth/dev-session" \
      -H 'Content-Type: application/json' -d "{\"username\":\"$1\"}" >/dev/null
}

JAR="$(mktemp)"; JAR2="$(mktemp)"
patient_signin "$PATIENT_ID" "$JAR" && ok "patient signed in with username '$PATIENT_ID'" || bad "patient sign-in failed"

SUMMARY="$(curl -sf -b "$JAR" "$API/api/portal/me/cases" | json 'd[0].get("summary")')"
[ "$SUMMARY" = "None" ] && ok "patient sees NO summary before approval" || bad "summary leaked before approval!"

# --- 5. permission guards --------------------------------------------------
step "5. Actions that must be refused"
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

C="$(code -X POST "$API/api/workflow/$CASE_ID/approve" -H 'Content-Type: application/json' \
     -d "{\"username\":\"$DOCTOR\",\"role\":\"doctor\",\"summary_text\":\"self-approved\"}")"
[ "$C" = "403" ] && ok "doctor cannot approve their own case (403)" || bad "doctor self-approval returned $C"

# Patient A tries to read patient B's case by id.
C="$(code -b "$JAR" "$API/api/portal/me/cases/$OTHER_CASE_ID")"
[ "$C" = "404" ] && ok "patient cannot read another patient's case (404, not 403)" || bad "IDOR returned $C"

# --- 6. admin sends it back ------------------------------------------------
step "6. Admin sends the case back to $DOCTOR"
ASSIGNED="$(curl -sf -X POST "$API/api/workflow/$CASE_ID/request-review" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$ADMIN\",\"role\":\"admin\",\"note\":\"Please confirm the PCP follow-up.\"}" \
  | json 'd["assigned_reviewer"]')"
[ "$ASSIGNED" = "$DOCTOR" ] && ok "routed back to $DOCTOR" || bad "assigned_reviewer=$ASSIGNED"

C="$(code -X POST "$API/api/workflow/$CASE_ID/submit-review" -H 'Content-Type: application/json' \
     -d "{\"username\":\"dr.wrong\",\"role\":\"doctor\",\"summary_text\":\"hijacked\"}")"
[ "$C" = "403" ] && ok "a different doctor cannot submit the review (403)" || bad "wrong-doctor submit returned $C"

# --- 7. doctor resubmits ---------------------------------------------------
step "7. $DOCTOR edits and returns it"
EDITED="Discharge summary

Pneumonia, resolved. Discharged home on 2026-08-01.
Follow-up: pulmonology on 2026-08-15.
Your PCP has been notified.
Medications: finish the full antibiotic course."

STAGE="$(curl -sf -X POST "$API/api/workflow/$CASE_ID/submit-review" -H 'Content-Type: application/json' \
  -d "$(EDITED="$EDITED" DOCTOR="$DOCTOR" python3 -c 'import json,os;print(json.dumps({"username":os.environ["DOCTOR"],"role":"doctor","summary_text":os.environ["EDITED"]}))')" \
  | json 'd["stage"]')"
[ "$STAGE" = "awaiting_admin" ] && ok "back with the admin" || bad "stage=$STAGE"

# --- 8. admin approves -----------------------------------------------------
step "8. Admin approves"
STAGE="$(curl -sf -X POST "$API/api/workflow/$CASE_ID/approve" -H 'Content-Type: application/json' \
  -d "$(EDITED="$EDITED" ADMIN="$ADMIN" python3 -c 'import json,os;print(json.dumps({"username":os.environ["ADMIN"],"role":"admin","summary_text":os.environ["EDITED"]}))')" \
  | json 'd["stage"]')"
[ "$STAGE" = "approved" ] && ok "approved and released" || bad "stage=$STAGE"

C="$(code -X POST "$API/api/workflow/$CASE_ID/approve" -H 'Content-Type: application/json' \
     -d "{\"username\":\"$ADMIN\",\"role\":\"admin\",\"summary_text\":\"again\"}")"
[ "$C" = "409" ] && ok "double approval refused (409)" || bad "double approval returned $C"

# --- 9. patient sees it ----------------------------------------------------
step "9. After approval, the patient sees the summary"
BODY="$(curl -sf -b "$JAR" "$API/api/portal/me/cases")"
SUMMARY="$(echo "$BODY" | json 'd[0].get("summary") or ""')"
[ -n "$SUMMARY" ] && [ "$SUMMARY" != "None" ] && ok "summary is visible" || bad "patient still sees no summary"
echo "$BODY" | json 'd[0]["status"]' | grep -q ready && ok "status shown to patient is 'ready'" || bad "unexpected patient status"

# Internal fields must never reach the patient.
for field in rationale confidence payer internal_status; do
  echo "$BODY" | grep -qi "\"$field\"" && bad "leaked field: $field" || ok "no '$field' in patient response"
done

echo
echo "── Summary the patient now sees ──"
echo "$SUMMARY" | sed 's/^/   /'

# --- cleanup ---------------------------------------------------------------
rm -f "$JAR" "$JAR2"
if $CLEANUP; then
  step "Cleanup"
  DB_URL="$(env_get DATABASE_URL)"
  python3 - "$DB_URL" "$CASE_ID" "$OTHER_CASE_ID" "$PATIENT_ID" "$OTHER_PATIENT_ID" <<'PY'
import sys, re
url, *ids = sys.argv[1:]
cases, patients = ids[:2], ids[2:]
try:
    import psycopg2
except ImportError:
    sys.exit("  (psycopg2 not on PATH — skipping cleanup)")
m = re.match(r".*://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", url)
conn = psycopg2.connect(user=m[1], password=m[2], host=m[3], port=m[4], dbname=m[5])
cur = conn.cursor()
for c in cases:
    for t in ("events", "agent_decisions", "case_workflow", "cases"):
        cur.execute(f"DELETE FROM {t} WHERE case_id = %s", (c,))
    cur.execute("DELETE FROM audit_log WHERE case_id = %s", (c,))
for p in patients:
    for t in ("portal_session", "login_token", "enrollment_token", "portal_user"):
        cur.execute(f"DELETE FROM portal.{t} WHERE patient_id = %s", (p,))
conn.commit()
print("  ✓ demo cases removed")
PY
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "All $pass checks passed."
else
  echo "$pass passed, $fail FAILED."
fi
exit $((fail > 0))
