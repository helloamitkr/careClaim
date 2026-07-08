"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getCase, reviewCase } from "@/lib/api";
import { StatusPill } from "@/app/components/StatusPill";
import { ConfidenceMeter } from "@/app/components/ConfidenceMeter";
import { PipelineSteps } from "@/app/components/PipelineSteps";
import { TraceWaterfall } from "@/app/components/TraceWaterfall";
import { AGENT_LABELS } from "@/lib/labels";
import type { CaseDetail, ReviewAction } from "@/lib/types";

export default function CaseDetailPage() {
  const params = useParams<{ case_id: string }>();
  const caseId = params.case_id;

  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState("care-manager");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState<ReviewAction | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDetail(await getCase(caseId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load case");
    }
  }, [caseId]);

  useEffect(() => {
    let ignore = false;
    getCase(caseId)
      .then((data) => {
        if (!ignore) {
          setDetail(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!ignore) setError(err instanceof Error ? err.message : "Failed to load case");
      });
    return () => {
      ignore = true;
    };
  }, [caseId]);

  // While the pipeline is still working this case, poll so the step row and
  // agent cards light up live. Stops as soon as the case leaves 'received'/
  // 'in_progress' (review-waiting cases update on the reviewer's action).
  const inFlight =
    detail !== null && ["received", "in_progress"].includes(String(detail.case.status));
  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(refresh, 1500);
    return () => clearInterval(timer);
  }, [inFlight, refresh]);

  async function handleReview(action: ReviewAction) {
    setSubmitting(action);
    setError(null);
    try {
      await reviewCase(caseId, action, reviewer, note || undefined);
      await refresh();
      setNote("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit review");
    } finally {
      setSubmitting(null);
    }
  }

  if (error && !detail) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <Link href="/" className="text-sm text-teal-700 dark:text-teal-400 hover:underline">
          ← Back to cases
        </Link>
        <p className="mt-4 text-sm text-red-600 dark:text-red-400">{error}</p>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <p className="text-sm text-black/50 dark:text-white/50">Loading…</p>
      </div>
    );
  }

  const c = detail.case;
  const composite = detail.agent_decisions.find((d) => d.agent_name === "risk_escalation");

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-8">
      <div>
        <Link href="/" className="text-sm text-teal-700 dark:text-teal-400 hover:underline">
          ← Back to cases
        </Link>
      </div>

      <section className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{caseId}</h1>
          <p className="text-sm text-black/60 dark:text-white/60 mt-1">
            {String(c.primary_diagnosis)} · {String(c.discharge_disposition).replace("_", " ")} · payer:{" "}
            {String(c.payer)}
          </p>
        </div>
        <StatusPill status={c.status as never} />
      </section>

      <section className="card px-5 py-4">
        <h2 className="text-xs uppercase tracking-wider text-black/40 dark:text-white/40 mb-3">
          Pipeline progress
        </h2>
        <PipelineSteps events={detail.events} status={String(c.status)} />
      </section>

      <section className="card">
        <details>
          <summary className="cursor-pointer select-none px-5 py-3 text-xs uppercase tracking-wider text-black/40 dark:text-white/40 hover:text-black/70 dark:hover:text-white/70">
            Input data — what the agents actually saw
          </summary>
          <pre className="overflow-x-auto border-t border-black/5 dark:border-white/5 px-5 py-4 font-mono text-xs leading-relaxed text-black/70 dark:text-white/70">
            {JSON.stringify(c, null, 2)}
          </pre>
        </details>
      </section>

      {composite && (
        <section className="card p-5">
          <h2 className="text-xs uppercase tracking-wider text-black/40 dark:text-white/40 mb-2">
            Composite risk score
          </h2>
          <div className="flex items-center gap-3 mb-2">
            <ConfidenceMeter value={composite.confidence} />
            <span className="text-sm font-medium">{composite.decision.replace(/_/g, " ")}</span>
          </div>
          <p className="text-sm text-black/60 dark:text-white/60">{composite.rationale}</p>
        </section>
      )}

      {detail.pending_review && (
        <section className="rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/10 p-5 space-y-4">
          <h2 className="text-sm font-semibold text-amber-800 dark:text-amber-300">
            Pending your review
          </h2>
          <p className="text-sm text-black/70 dark:text-white/70">
            Proposed: <span className="font-medium">{detail.pending_review.decision}</span>{" "}
            (confidence {Math.round(detail.pending_review.confidence * 100)}%)
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="text-xs text-black/50 dark:text-white/50">
              Reviewer
              <input
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
                className="mt-1 w-full rounded border border-black/10 dark:border-white/10 bg-transparent px-2 py-1.5 text-sm"
              />
            </label>
            <label className="text-xs text-black/50 dark:text-white/50 sm:col-span-2">
              Note (optional)
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Reason for your decision"
                className="mt-1 w-full rounded border border-black/10 dark:border-white/10 bg-transparent px-2 py-1.5 text-sm"
              />
            </label>
          </div>

          <div className="flex gap-2">
            <button
              onClick={() => handleReview("approved")}
              disabled={submitting !== null}
              className="rounded bg-teal-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-teal-700 disabled:opacity-50"
            >
              {submitting === "approved" ? "Approving…" : "Approve"}
            </button>
            <button
              onClick={() => handleReview("overridden")}
              disabled={submitting !== null}
              className="rounded border border-black/20 dark:border-white/20 px-3 py-1.5 text-sm font-medium hover:bg-black/5 dark:hover:bg-white/5 disabled:opacity-50"
            >
              {submitting === "overridden" ? "Overriding…" : "Override"}
            </button>
            <button
              onClick={() => handleReview("rejected")}
              disabled={submitting !== null}
              className="rounded border border-red-300 dark:border-red-700 px-3 py-1.5 text-sm font-medium text-red-700 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/10 disabled:opacity-50"
            >
              {submitting === "rejected" ? "Rejecting…" : "Reject"}
            </button>
          </div>
          {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
        </section>
      )}

      <section>
        <h2 className="text-xs uppercase tracking-wider text-black/40 dark:text-white/40 mb-3">
          Agent decisions
        </h2>
        <div className="space-y-3">
          {detail.agent_decisions
            .filter((d) => d.agent_name !== "risk_escalation")
            .map((d) => (
              <div key={d.agent_name} className="card p-4">
                <div className="flex items-center justify-between gap-3 mb-1">
                  <span className="text-sm font-medium">{AGENT_LABELS[d.agent_name] ?? d.agent_name}</span>
                  <ConfidenceMeter value={d.confidence} />
                </div>
                <p className="text-xs text-black/60 dark:text-white/60">{d.rationale}</p>
              </div>
            ))}
        </div>
      </section>

      <section>
        <h2 className="text-xs uppercase tracking-wider text-black/40 dark:text-white/40 mb-3">
          Request / response flow
        </h2>
        <div className="card p-4">
          <TraceWaterfall events={detail.events} />
        </div>
      </section>

      <section>
        <h2 className="text-xs uppercase tracking-wider text-black/40 dark:text-white/40 mb-3">
          Audit trail
        </h2>
        <div className="overflow-x-auto card">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-black/10 dark:border-white/10 text-left uppercase tracking-wider text-black/40 dark:text-white/40">
                <th className="px-3 py-2 font-medium">Agent</th>
                <th className="px-3 py-2 font-medium">Decision</th>
                <th className="px-3 py-2 font-medium">Confidence</th>
                <th className="px-3 py-2 font-medium">Reviewer</th>
                <th className="px-3 py-2 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {detail.audit.map((row, i) => (
                <tr key={i} className="border-b border-black/5 dark:border-white/5 last:border-0">
                  <td className="px-3 py-2">{AGENT_LABELS[row.agent_id] ?? row.agent_id}</td>
                  <td className="px-3 py-2">{row.decision}</td>
                  <td className="px-3 py-2 tabular-nums">
                    {row.confidence !== null ? `${Math.round(row.confidence * 100)}%` : "—"}
                  </td>
                  <td className="px-3 py-2">{row.reviewer ?? "—"}</td>
                  <td className="px-3 py-2 text-black/50 dark:text-white/50">
                    {new Date(row.recorded_at).toLocaleTimeString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
