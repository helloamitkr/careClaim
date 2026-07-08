"use client";

import { useState } from "react";
import { ingestBulk, ingestCase } from "@/lib/api";
import type { BulkIngestResult } from "@/lib/types";

const EXAMPLE_JSON = `{
  "patient_id": "patient-042",
  "discharge_date": "2026-07-10",
  "discharge_disposition": "home",
  "primary_diagnosis": "Pneumonia, resolved",
  "has_pcp_on_file": true,
  "payer": "Aetna",
  "referral_specialty": "pulmonology",
  "risk_flags": []
}`;

export function IngestModal({
  onClose,
  onCreated,
  initialJson,
  title,
}: {
  onClose: () => void;
  onCreated: () => void;
  initialJson?: string;
  title?: string;
}) {
  const [text, setText] = useState(initialJson ?? EXAMPLE_JSON);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [bulkResult, setBulkResult] = useState<BulkIngestResult | null>(null);

  async function handleSubmit() {
    setError(null);

    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (err) {
      setError(`Invalid JSON: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }

    setSubmitting(true);
    try {
      if (Array.isArray(parsed)) {
        // Bulk mode: per-item results come back immediately; the pipeline
        // works through accepted cases in the background.
        const result = await ingestBulk(parsed);
        setBulkResult(result);
        onCreated();
      } else {
        await ingestCase(parsed as Record<string, unknown>);
        onCreated();
        onClose();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to ingest case");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-lg border border-black/10 dark:border-white/10 bg-[var(--background)] p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 mb-1">
          <h2 className="text-lg font-semibold tracking-tight">{title ?? "Ingest a case"}</h2>
          <button
            onClick={onClose}
            className="text-black/40 dark:text-white/40 hover:text-black dark:hover:text-white text-sm"
          >
            ✕
          </button>
        </div>
        {bulkResult === null ? (
          <>
            <p className="text-sm text-black/50 dark:text-white/50 mb-4">
              Paste case JSON — this is the same manual front door the EHR normalization layer
              would use for a real feed. Only <code className="text-xs">discharge_date</code>,{" "}
              <code className="text-xs">discharge_disposition</code>,{" "}
              <code className="text-xs">primary_diagnosis</code>,{" "}
              <code className="text-xs">has_pcp_on_file</code>, and <code className="text-xs">payer</code>{" "}
              are required — everything else (case_id, patient_id, timestamps) is auto-generated if
              omitted. Extra fields are ignored, so a full case dump works too.{" "}
              <span className="text-teal-700 dark:text-teal-400">
                Bulk: paste an array <code className="text-xs">[{"{…}"}, {"{…}"}]</code> of up to
                100 cases — each is checked individually, then processed in the background.
              </span>
            </p>

            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              spellCheck={false}
              rows={14}
              className="w-full rounded border border-black/10 dark:border-white/10 bg-black/[0.02] dark:bg-white/[0.03] px-3 py-2 font-mono text-xs leading-relaxed focus:outline-none focus:ring-1 focus:ring-teal-500"
            />

            {error && (
              <pre className="mt-3 whitespace-pre-wrap rounded border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/10 px-3 py-2 text-xs text-red-700 dark:text-red-400">
                {error}
              </pre>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={onClose}
                disabled={submitting}
                className="rounded border border-black/20 dark:border-white/20 px-3 py-1.5 text-sm font-medium hover:bg-black/5 dark:hover:bg-white/5 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={submitting}
                className="rounded bg-teal-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-teal-700 disabled:opacity-50"
              >
                {submitting ? "Running pipeline…" : "Ingest"}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm text-black/60 dark:text-white/60 mb-3">
              <span className="font-medium text-teal-700 dark:text-teal-400">
                {bulkResult.accepted} accepted
              </span>
              {" · "}
              <span className={bulkResult.rejected ? "font-medium text-red-600 dark:text-red-400" : ""}>
                {bulkResult.rejected} rejected
              </span>
              {" of "}
              {bulkResult.total}. Accepted cases are running in the background — watch them
              on the case list.
            </p>
            <div className="max-h-72 overflow-y-auto rounded border border-black/10 dark:border-white/10 divide-y divide-black/5 dark:divide-white/5">
              {bulkResult.results.map((item) => (
                <div key={item.index} className="flex items-start gap-2 px-3 py-2 text-xs">
                  <span className="mt-px">
                    {item.accepted ? (
                      <span className="text-teal-600 dark:text-teal-400">✓</span>
                    ) : (
                      <span className="text-red-600 dark:text-red-400">✕</span>
                    )}
                  </span>
                  <span className="font-mono text-black/70 dark:text-white/70">
                    #{item.index}
                    {item.case_id ? ` · ${item.case_id}` : ""}
                  </span>
                  {item.error && (
                    <span className="text-red-600 dark:text-red-400">{item.error}</span>
                  )}
                </div>
              ))}
            </div>
            <div className="mt-4 flex justify-end">
              <button
                onClick={() => {
                  onCreated(); // background rows exist by now — refresh again
                  onClose();
                }}
                className="rounded bg-teal-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-teal-700"
              >
                Done
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
