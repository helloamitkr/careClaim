"use client";

import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";

/**
 * Doctor uploads JSON -> agents run -> admin reviews the draft
 *   -> approve            : summary is released to the patient
 *   -> request review     : routed back to a doctor (defaults to the uploader)
 *
 * There is no login. The username and role below are self-asserted and only
 * used for attribution and routing — see workflow.py.
 */

type Role = "doctor" | "admin";

interface WorkflowCase {
  case_id: string;
  patient_id: string;
  primary_diagnosis: string;
  stage: "processing" | "awaiting_admin" | "awaiting_doctor" | "approved";
  case_status: string;
  uploaded_by: string;
  assigned_reviewer: string | null;
  review_note: string | null;
  approved_by: string | null;
  approved_at: string | null;
  // Only ever present once an admin has signed the case off.
  summary_text: string | null;
  agents_ready: boolean;
  updated_at: string;
}

interface Draft {
  case_id: string;
  ready: boolean;
  draft: string;
  sections: { agent_name: string; heading: string; body: string; confidence: number }[];
}

const STAGE_LABEL: Record<WorkflowCase["stage"], string> = {
  processing: "Agents working",
  awaiting_admin: "Awaiting admin approval",
  awaiting_doctor: "Sent back for doctor review",
  approved: "Approved & released",
};

const STAGE_STYLE: Record<WorkflowCase["stage"], string> = {
  processing: "bg-amber-500/10 text-amber-700 dark:text-amber-300 ring-amber-500/20",
  awaiting_admin: "bg-sky-500/10 text-sky-700 dark:text-sky-300 ring-sky-500/20",
  awaiting_doctor: "bg-violet-500/10 text-violet-700 dark:text-violet-300 ring-violet-500/20",
  approved: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 ring-emerald-500/20",
};

const SAMPLE_JSON = JSON.stringify(
  {
    patient_id: "pt-0001",
    discharge_date: "2026-07-20",
    discharge_disposition: "home",
    primary_diagnosis: "Heart Failure Exacerbation",
    has_pcp_on_file: true,
    payer: "Medicare",
    referral_specialty: "cardiology",
    risk_flags: [],
  },
  null,
  2,
);

export default function ClinicianPage() {
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("doctor");
  const [identified, setIdentified] = useState(false);

  // Survive a refresh — this is convenience, not a session.
  useEffect(() => {
    const saved = localStorage.getItem("carebridge_actor");
    if (saved) {
      const a = JSON.parse(saved);
      setUsername(a.username);
      setRole(a.role);
      setIdentified(true);
    }
  }, []);

  function signIn(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim()) return;
    localStorage.setItem("carebridge_actor", JSON.stringify({ username: username.trim(), role }));
    setIdentified(true);
  }

  /** Same person, different hat. The admin queue is otherwise reachable only by
   *  signing out, which reads like "leave" — so nobody ever finds it. */
  function switchRole(next: Role) {
    localStorage.setItem("carebridge_actor", JSON.stringify({ username, role: next }));
    setRole(next);
  }

  function signOut() {
    localStorage.removeItem("carebridge_actor");
    setIdentified(false);
  }

  if (!identified) {
    return (
      <Page>
        <div className="mx-auto max-w-sm">
          <h1 className="text-2xl font-semibold tracking-tight">Who are you?</h1>
          <p className="mt-1.5 text-sm text-black/50 dark:text-white/50">
            No password — your name and role are recorded on every action for attribution.
          </p>
          <form onSubmit={signIn} className="mt-6 space-y-3">
            <input
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="dr.smith"
              className="w-full rounded-lg border border-black/15 dark:border-white/15 bg-transparent px-3 py-2 text-sm"
            />
            <div className="flex gap-2">
              {(["doctor", "admin"] as Role[]).map((r) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => setRole(r)}
                  className={`flex-1 rounded-lg border px-3 py-2 text-sm capitalize ${
                    role === r
                      ? "border-teal-600 bg-teal-600/10 text-teal-700 dark:text-teal-300"
                      : "border-black/15 dark:border-white/15 text-black/60 dark:text-white/60"
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
            <button className="w-full rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-white hover:bg-teal-700">
              Continue
            </button>
          </form>
        </div>
      </Page>
    );
  }

  return (
    <Page>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {role === "doctor" ? "Doctor workspace" : "Admin approvals"}
          </h1>
          <p className="mt-1 text-sm text-black/50 dark:text-white/50">
            Signed in as <span className="font-medium">{username}</span> ({role})
          </p>
        </div>
        <div className="flex items-center gap-3">
          <RoleSwitch role={role} onChange={switchRole} />
          <button onClick={signOut} className="text-sm text-black/50 hover:text-black dark:text-white/50 dark:hover:text-white">
            Switch user
          </button>
        </div>
      </div>

      {role === "doctor" && <DoctorView username={username} />}
      {role === "admin" && <AdminView username={username} />}
    </Page>
  );
}

/** Not authorization — there is none. It labels every action for attribution
 *  and decides which queue you are looking at. The backend still refuses a
 *  doctor's approve with a 403 regardless of what this says. */
function RoleSwitch({ role, onChange }: { role: Role; onChange: (r: Role) => void }) {
  return (
    <div className="flex rounded-lg border border-black/15 dark:border-white/15 p-0.5">
      {(["doctor", "admin"] as Role[]).map((r) => (
        <button
          key={r}
          onClick={() => onChange(r)}
          aria-pressed={role === r}
          className={`rounded-md px-3 py-1 text-sm capitalize transition ${
            role === r
              ? "bg-teal-600 text-white"
              : "text-black/55 dark:text-white/55 hover:text-black dark:hover:text-white"
          }`}
        >
          {r}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------

function DoctorView({ username }: { username: string }) {
  const [json, setJson] = useState(SAMPLE_JSON);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { cases, reload } = useQueue("doctor", username);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      let parsed: unknown;
      try {
        parsed = JSON.parse(json);
      } catch {
        setErr("That isn't valid JSON.");
        return;
      }
      const res = await fetch(`${API}/api/cases/ingest?uploaded_by=${encodeURIComponent(username)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(parsed),
      });
      const body = await res.json();
      if (!res.ok) {
        setErr(typeof body.detail === "string" ? body.detail : "Upload rejected.");
        return;
      }
      setMsg(
        Array.isArray(parsed)
          ? `${body.accepted}/${body.total} cases accepted. The agents are working.`
          : `Case ${body.case_id} accepted. The agents are working.`,
      );
      setTimeout(() => void reload(), 1500);
    } finally {
      setBusy(false);
    }
  }

  const needsMyReview = cases.filter((c) => c.stage === "awaiting_doctor" && c.assigned_reviewer === username);

  return (
    <div className="mt-8 space-y-10">
      <section>
        <h2 className="text-sm font-medium uppercase tracking-wide text-black/40 dark:text-white/40">
          Upload a case
        </h2>
        <form onSubmit={upload} className="mt-3">
          <textarea
            value={json}
            onChange={(e) => setJson(e.target.value)}
            rows={12}
            spellCheck={false}
            className="w-full rounded-xl border border-black/15 dark:border-white/15 bg-transparent p-3 font-mono text-xs"
          />
          <div className="mt-3 flex items-center gap-3">
            <button
              disabled={busy}
              className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-700 disabled:opacity-50"
            >
              {busy ? "Uploading…" : "Upload JSON"}
            </button>
            <span className="text-xs text-black/40 dark:text-white/40">
              A single object, or an array of up to 100.
            </span>
          </div>
        </form>
        {msg && <p className="mt-3 text-sm text-emerald-700 dark:text-emerald-400">{msg}</p>}
        {err && <p className="mt-3 text-sm text-rose-600">{err}</p>}
      </section>

      {needsMyReview.length > 0 && (
        <section>
          <h2 className="text-sm font-medium uppercase tracking-wide text-violet-700 dark:text-violet-300">
            Sent back to you ({needsMyReview.length})
          </h2>
          <div className="mt-3 space-y-4">
            {needsMyReview.map((c) => (
              <DoctorReviewCard key={c.case_id} c={c} username={username} onDone={reload} />
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="text-sm font-medium uppercase tracking-wide text-black/40 dark:text-white/40">
          Your cases
        </h2>
        <CaseTable cases={cases} empty="You haven't uploaded any cases yet." />
      </section>
    </div>
  );
}

function DoctorReviewCard({ c, username, onDone }: { c: WorkflowCase; username: string; onDone: () => void }) {
  const draft = useDraft(c.case_id);
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const value = text ?? draft?.draft ?? "";

  async function resubmit() {
    setBusy(true);
    await fetch(`${API}/api/workflow/${c.case_id}/submit-review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, role: "doctor", summary_text: value }),
    });
    setBusy(false);
    onDone();
  }

  return (
    <article className="rounded-xl border border-violet-500/30 p-4">
      <Header c={c} />
      {c.review_note && (
        <p className="mt-2 rounded-lg bg-violet-500/5 p-2 text-sm">
          <span className="font-medium">Admin note:</span> {c.review_note}
        </p>
      )}
      <textarea
        value={value}
        onChange={(e) => setText(e.target.value)}
        rows={10}
        className="mt-3 w-full rounded-lg border border-black/15 dark:border-white/15 bg-transparent p-3 text-sm"
      />
      <button
        onClick={() => void resubmit()}
        disabled={busy || !value}
        className="mt-3 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
      >
        {busy ? "Sending…" : "Return to admin"}
      </button>
    </article>
  );
}

// ---------------------------------------------------------------------------

function AdminView({ username }: { username: string }) {
  const { cases, reload } = useQueue("admin", "");
  const pending = cases.filter((c) => c.stage === "awaiting_admin");
  const rest = cases.filter((c) => c.stage !== "awaiting_admin");

  return (
    <div className="mt-8 space-y-10">
      <section>
        <h2 className="text-sm font-medium uppercase tracking-wide text-black/40 dark:text-white/40">
          Awaiting your approval ({pending.length})
        </h2>
        {pending.length === 0 && (
          <p className="mt-3 text-sm text-black/50 dark:text-white/50">Nothing to approve right now.</p>
        )}
        <div className="mt-3 space-y-4">
          {pending.map((c) => (
            <AdminApprovalCard key={c.case_id} c={c} username={username} onDone={reload} />
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-medium uppercase tracking-wide text-black/40 dark:text-white/40">
          Everything else
        </h2>
        <CaseTable cases={rest} empty="No other cases." />
      </section>
    </div>
  );
}

function AdminApprovalCard({ c, username, onDone }: { c: WorkflowCase; username: string; onDone: () => void }) {
  const draft = useDraft(c.case_id);
  const [text, setText] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const value = text ?? draft?.draft ?? "";

  async function act(path: string, body: object) {
    setBusy(true);
    setErr(null);
    const res = await fetch(`${API}/api/workflow/${c.case_id}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, role: "admin", ...body }),
    });
    setBusy(false);
    if (!res.ok) {
      setErr((await res.json()).detail ?? "Action failed.");
      return;
    }
    onDone();
  }

  return (
    <article className="rounded-xl border border-black/10 dark:border-white/10 p-4">
      <Header c={c} />

      {draft && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {draft.sections.map((s) => (
            <span
              key={s.agent_name}
              title={`${s.heading}: ${Math.round(s.confidence * 100)}% confidence`}
              className="rounded-full bg-black/5 dark:bg-white/10 px-2 py-0.5 text-[11px]"
            >
              {s.heading} · {Math.round(s.confidence * 100)}%
            </span>
          ))}
        </div>
      )}

      <textarea
        value={value}
        onChange={(e) => setText(e.target.value)}
        rows={12}
        className="mt-3 w-full rounded-lg border border-black/15 dark:border-white/15 bg-transparent p-3 text-sm"
      />
      <p className="mt-1.5 text-xs text-black/40 dark:text-white/40">
        This exact text is what the patient will see once approved.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          onClick={() => void act("approve", { summary_text: value })}
          disabled={busy || !value}
          className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          Approve & release to patient
        </button>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={`Note for ${c.uploaded_by}`}
          className="flex-1 min-w-[200px] rounded-lg border border-black/15 dark:border-white/15 bg-transparent px-3 py-2 text-sm"
        />
        <button
          onClick={() => void act("request-review", { reviewer: c.uploaded_by, note })}
          disabled={busy}
          className="rounded-lg border border-violet-600 px-4 py-2 text-sm font-medium text-violet-700 dark:text-violet-300 hover:bg-violet-600/10 disabled:opacity-50"
        >
          Send back to {c.uploaded_by}
        </button>
      </div>
      {err && <p className="mt-2 text-sm text-rose-600">{err}</p>}
    </article>
  );
}

// ---------------------------------------------------------------------------

function Header({ c }: { c: WorkflowCase }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div>
        <p className="font-medium">{c.primary_diagnosis}</p>
        <p className="text-xs text-black/45 dark:text-white/45">
          {c.patient_id} · {c.case_id} · uploaded by {c.uploaded_by}
        </p>
      </div>
      <span className={`rounded-full px-2.5 py-1 text-xs font-medium ring-1 ${STAGE_STYLE[c.stage]}`}>
        {STAGE_LABEL[c.stage]}
      </span>
    </div>
  );
}

/** The exact text the patient is reading. Collapsed by default — a doctor with
 *  twenty approved cases wants to scan the list, not scroll past twenty summaries. */
function ReleasedSummary({ c }: { c: WorkflowCase }) {
  const [open, setOpen] = useState(false);
  const approved = c.approved_at ? new Date(c.approved_at).toLocaleString() : "";

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="text-xs font-medium text-emerald-700 dark:text-emerald-300 hover:underline"
      >
        {open ? "Hide" : "View"} summary released to patient
      </button>
      {open && (
        <>
          <p className="mt-1.5 text-xs text-black/40 dark:text-white/40">
            Approved by {c.approved_by} on {approved}. This is what {c.patient_id} sees.
          </p>
          <pre className="mt-1.5 whitespace-pre-wrap rounded-lg bg-emerald-500/5 p-3 text-sm font-sans">
            {c.summary_text}
          </pre>
        </>
      )}
    </div>
  );
}

function CaseTable({ cases, empty }: { cases: WorkflowCase[]; empty: string }) {
  if (cases.length === 0) {
    return <p className="mt-3 text-sm text-black/50 dark:text-white/50">{empty}</p>;
  }
  return (
    <div className="mt-3 divide-y divide-black/5 dark:divide-white/5 rounded-xl border border-black/10 dark:border-white/10">
      {cases.map((c) => (
        <div key={c.case_id} className="p-3">
          <Header c={c} />
          {c.summary_text && <ReleasedSummary c={c} />}
        </div>
      ))}
    </div>
  );
}

function Page({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-3xl px-6 py-10">{children}</div>;
}

// ---------------------------------------------------------------------------

function useQueue(role: Role, username: string) {
  const [cases, setCases] = useState<WorkflowCase[]>([]);

  const reload = useCallback(async () => {
    const qs = new URLSearchParams({ role, username });
    const res = await fetch(`${API}/api/workflow/queue?${qs}`);
    if (res.ok) setCases(await res.json());
  }, [role, username]);

  useEffect(() => {
    void reload();
    // Agents take seconds; poll so "Agents working" flips on its own.
    const id = setInterval(() => void reload(), 5000);
    return () => clearInterval(id);
  }, [reload]);

  return { cases, reload };
}

function useDraft(caseId: string) {
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => {
    let cancelled = false;
    void fetch(`${API}/api/cases/${caseId}/draft`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => !cancelled && setDraft(d));
    return () => {
      cancelled = true;
    };
  }, [caseId]);
  return draft;
}
