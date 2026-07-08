"use client";

import { useCallback, useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";

/** Mirrors PortalCaseOut on the backend — an allowlist, not the internal case. */
interface PortalCase {
  case_id: string;
  status: string;
  status_message: string;
  primary_diagnosis: string | null;
  discharge_date: string | null;
  discharge_disposition: string | null;
  /** Null until a clinician approves the case — never a raw agent draft. */
  summary: string | null;
  approved_at: string | null;
  last_updated: string;
}

const STATUS_STYLE: Record<string, string> = {
  preparing: "bg-amber-500/10 text-amber-700 dark:text-amber-300 ring-amber-500/20",
  in_review: "bg-sky-500/10 text-sky-700 dark:text-sky-300 ring-sky-500/20",
  ready: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 ring-emerald-500/20",
  contact_us: "bg-rose-500/10 text-rose-700 dark:text-rose-300 ring-rose-500/20",
};

/** Every call carries the session cookie; none of them carries a patient id. */
async function portalFetch(path: string, init: RequestInit = {}) {
  return fetch(`${API_URL}/api/portal${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
}

function titleCase(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function PatientPortal() {
  const [cases, setCases] = useState<PortalCase[] | null>(null);
  const [signedIn, setSignedIn] = useState<boolean | null>(null); // null = still checking
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadCases = useCallback(async () => {
    const res = await portalFetch("/me/cases");
    if (res.status === 401) {
      setSignedIn(false);
      setCases(null);
      return;
    }
    if (!res.ok) {
      setError("Could not load your care plan. Please try again.");
      return;
    }
    setCases(await res.json());
    setSignedIn(true);
    setError(null);
  }, []);

  useEffect(() => {
    void loadCases();
  }, [loadCases]);

  // Keep the page current. Polling, not the staff log stream — that firehose
  // carries other patients' case ids.
  useEffect(() => {
    if (!signedIn) return;
    const id = setInterval(() => void loadCases(), 30_000);
    return () => clearInterval(id);
  }, [signedIn, loadCases]);

  async function signOut() {
    await portalFetch("/auth/logout", { method: "POST" });
    setSignedIn(false);
    setCases(null);
  }

  if (signedIn === null) {
    return <Shell><p className="text-black/50 dark:text-white/50">Loading…</p></Shell>;
  }

  if (!signedIn) {
    return (
      <Shell>
        <SignIn onSignedIn={loadCases} busy={busy} setBusy={setBusy} />
      </Shell>
    );
  }

  return (
    <Shell>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Your care plan</h1>
          <p className="mt-1 text-sm text-black/50 dark:text-white/50">
            Updated automatically. Last checked just now.
          </p>
        </div>
        <button
          onClick={() => void signOut()}
          className="text-sm text-black/50 dark:text-white/50 hover:text-black dark:hover:text-white"
        >
          Sign out
        </button>
      </div>

      {error && <p className="mt-4 text-sm text-rose-600">{error}</p>}

      <div className="mt-6 space-y-4">
        {cases?.length === 0 && (
          <p className="text-sm text-black/50 dark:text-white/50">
            You don&apos;t have any care plans yet.
          </p>
        )}
        {cases?.map((c) => (
          <article
            key={c.case_id}
            className="rounded-2xl border border-black/10 dark:border-white/10 p-5"
          >
            <div className="flex items-center justify-between gap-4">
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-medium ring-1 ${
                  STATUS_STYLE[c.status] ?? STATUS_STYLE.preparing
                }`}
              >
                {titleCase(c.status)}
              </span>
              <time className="text-xs text-black/40 dark:text-white/40">
                Updated {new Date(c.last_updated).toLocaleString()}
              </time>
            </div>

            <p className="mt-3 text-[15px]">{c.status_message}</p>

            {/* Only ever present once a clinician has approved the case. */}
            {c.summary && (
              <div className="mt-4 rounded-xl bg-black/[0.03] dark:bg-white/[0.04] p-4">
                <h2 className="text-xs uppercase tracking-wide text-black/40 dark:text-white/40">
                  Your discharge summary
                </h2>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">{c.summary}</p>
                {c.approved_at && (
                  <p className="mt-3 text-xs text-black/40 dark:text-white/40">
                    Approved by your care team on {new Date(c.approved_at).toLocaleDateString()}
                  </p>
                )}
              </div>
            )}

            <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3 text-sm">
              <Field label="Reason for care" value={c.primary_diagnosis} />
              <Field
                label="Discharge date"
                value={c.discharge_date ? new Date(c.discharge_date).toLocaleDateString() : null}
              />
              <Field
                label="Discharged to"
                value={c.discharge_disposition ? titleCase(c.discharge_disposition) : null}
              />
            </dl>
          </article>
        ))}
      </div>

      <p className="mt-8 text-xs text-black/40 dark:text-white/40">
        Questions about your plan? Contact your care team.
      </p>
    </Shell>
  );
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-black/40 dark:text-white/40">{label}</dt>
      <dd className="mt-0.5">{value ?? "—"}</dd>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-full">
      <header className="border-b border-black/10 dark:border-white/10">
        <div className="mx-auto max-w-2xl px-6 py-4 flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-teal-500 to-teal-700 text-white">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M3 12h4l2.5-6 4 12L16 12h5"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span className="text-lg font-semibold tracking-tight">
            CareBridge <span className="text-teal-600 dark:text-teal-400">Patient Portal</span>
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-2xl px-6 py-10">{children}</main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sign in — username only, matching the doctor/admin pages. The username *is*
// the patient_id. Backed by /auth/dev-session, which only exists when
// PORTAL_DEV_MODE=true; the enrollment + magic-link flow is still there for
// production (see portal/auth.py).
// ---------------------------------------------------------------------------

function SignIn({
  onSignedIn,
  busy,
  setBusy,
}: {
  onSignedIn: () => Promise<void>;
  busy: boolean;
  setBusy: (b: boolean) => void;
}) {
  const [username, setUsername] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleSignIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await portalFetch("/auth/dev-session", {
        method: "POST",
        body: JSON.stringify({ username: username.trim() }),
      });
      if (!res.ok) {
        setError(
          res.status === 404
            ? "Patient sign-in is disabled. Set PORTAL_DEV_MODE=true and restart."
            : "Could not sign in.",
        );
        return;
      }
      await onSignedIn();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm">
      <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
      <p className="mt-1.5 text-sm text-black/50 dark:text-white/50">
        Enter your patient ID. Role: <span className="font-medium">patient</span>.
      </p>

      <form onSubmit={handleSignIn} className="mt-6 space-y-3">
        <input
          autoFocus
          required
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="pt-0001"
          className="w-full rounded-lg border border-black/15 dark:border-white/15 bg-transparent px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-white hover:bg-teal-700 disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Continue"}
        </button>
      </form>

      {error && <p className="mt-4 text-sm text-rose-600">{error}</p>}
    </div>
  );
}
