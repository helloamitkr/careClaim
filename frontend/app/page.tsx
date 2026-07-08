"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { createCase, listCases, listFixtures } from "@/lib/api";
import { StatusPill } from "@/app/components/StatusPill";
import { IngestModal } from "@/app/components/IngestModal";
import type { CaseListItem, FixtureTemplate } from "@/lib/types";

export default function CaseListPage() {
  const [cases, setCases] = useState<CaseListItem[] | null>(null);
  const [fixtures, setFixtures] = useState<FixtureTemplate[]>([]);
  const [creatingTemplate, setCreatingTemplate] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ingestOpen, setIngestOpen] = useState(false);
  const [previewFixture, setPreviewFixture] = useState<FixtureTemplate | null>(null);

  const refresh = useCallback(async () => {
    try {
      setCases(await listCases());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load cases");
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    listFixtures().then((data) => {
      if (!ignore) setFixtures(data);
    }).catch(() => {});
    listCases()
      .then((data) => {
        if (!ignore) {
          setCases(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!ignore) setError(err instanceof Error ? err.message : "Failed to load cases");
      });
    return () => {
      ignore = true;
    };
  }, []);

  // Bulk-ingested cases process in the background — poll while any case is
  // mid-pipeline, plus a ~20s burst after an ingest so rows that appear a
  // beat after the response are never missed. Self-terminates: each refresh
  // re-evaluates the condition.
  const [pollUntil, setPollUntil] = useState(0);
  const handleCreated = useCallback(() => {
    refresh();
    setPollUntil(Date.now() + 20000);
  }, [refresh]);

  useEffect(() => {
    const active = cases?.some((c) => c.status === "received" || c.status === "in_progress");
    if (!active && Date.now() >= pollUntil) return;
    const timer = setInterval(refresh, 2500);
    return () => clearInterval(timer);
  }, [cases, pollUntil, refresh]);

  async function handleNewDischarge(templateKey: string) {
    setCreatingTemplate(templateKey);
    setError(null);
    try {
      await createCase(templateKey);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create case");
    } finally {
      setCreatingTemplate(null);
    }
  }

  const FIXTURE_ICONS: Record<string, string> = {
    clean: "🩺",
    payer_delay: "🦴",
    high_risk: "🫀",
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 space-y-10">
      <section>
        <h1 className="text-3xl font-semibold tracking-tight mb-1">Transition cases</h1>
        <p className="text-sm text-black/50 dark:text-white/50 mb-6">
          Decisions to make, not tasks to grind through —{" "}
          <span className="text-teal-700 dark:text-teal-400">clean cases close themselves.</span>
        </p>

        <p className="text-xs text-black/40 dark:text-white/40 mb-3">
          Simulated EHR feed — in production these discharges arrive automatically via
          HL7/FHIR. Each card runs a fixed, inspectable payload: click{" "}
          <span className="font-mono">{"{ }"}</span> view data to see (or edit) exactly
          what it sends.
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {fixtures.map((fixture) => (
            <div
              key={fixture.key}
              className="card flex flex-col overflow-hidden transition-all hover:-translate-y-0.5 hover:shadow-lg hover:border-teal-500/60"
            >
              <button
                onClick={() => handleNewDischarge(fixture.key)}
                disabled={creatingTemplate !== null}
                className="flex-1 text-left px-4 pt-4 pb-3 hover:bg-teal-50/60 dark:hover:bg-teal-900/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-lg bg-teal-600/10 dark:bg-teal-400/10 text-lg">
                  {FIXTURE_ICONS[fixture.key] ?? "＋"}
                </div>
                <div className="text-sm font-medium mb-1">
                  {creatingTemplate === fixture.key ? "Running pipeline…" : fixture.label}
                </div>
                <div className="text-xs leading-relaxed text-black/50 dark:text-white/50">
                  {fixture.description}
                </div>
              </button>
              <button
                onClick={() => setPreviewFixture(fixture)}
                disabled={creatingTemplate !== null}
                className="text-left px-4 py-2 border-t border-black/5 dark:border-white/5 font-mono text-xs text-teal-700 dark:text-teal-400 hover:bg-teal-50/60 dark:hover:bg-teal-900/10 transition-colors disabled:opacity-50"
              >
                {"{ }"} view data
              </button>
            </div>
          ))}
          <button
            onClick={() => setIngestOpen(true)}
            disabled={creatingTemplate !== null}
            className="flex flex-col items-start text-left rounded-xl border border-dashed border-black/20 dark:border-white/20 px-4 py-4 hover:border-teal-500 hover:bg-teal-50/40 dark:hover:bg-teal-900/10 transition-all hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-lg bg-black/5 dark:bg-white/10 font-mono text-sm text-teal-700 dark:text-teal-400">
              {"{ }"}
            </div>
            <div className="text-sm font-medium mb-1">Ingest JSON</div>
            <div className="text-xs leading-relaxed text-black/50 dark:text-white/50">
              Paste your own case data instead of a canned template.
            </div>
          </button>
        </div>
        {error && <p className="mt-4 text-sm text-red-600 dark:text-red-400">{error}</p>}

        {ingestOpen && (
          <IngestModal onClose={() => setIngestOpen(false)} onCreated={handleCreated} />
        )}
        {previewFixture && (
          <IngestModal
            title={`Data behind “${previewFixture.label}”`}
            initialJson={JSON.stringify(previewFixture.sample, null, 2)}
            onClose={() => setPreviewFixture(null)}
            onCreated={handleCreated}
          />
        )}
      </section>

      <section>
        {cases === null ? (
          <p className="text-sm text-black/50 dark:text-white/50">Loading…</p>
        ) : cases.length === 0 ? (
          <p className="text-sm text-black/50 dark:text-white/50">
            No cases yet — drop in a new discharge above.
          </p>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-black/10 dark:border-white/10 bg-black/[0.02] dark:bg-white/[0.03] text-left text-xs uppercase tracking-wider text-black/40 dark:text-white/40">
                  <th className="px-4 py-3 font-medium">Case</th>
                  <th className="px-4 py-3 font-medium">Diagnosis</th>
                  <th className="px-4 py-3 font-medium">Disposition</th>
                  <th className="px-4 py-3 font-medium">Payer</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Updated</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((c) => (
                  <tr
                    key={c.case_id}
                    className="border-b border-black/5 dark:border-white/5 last:border-0 hover:bg-teal-50/40 dark:hover:bg-teal-900/[0.07] transition-colors"
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/cases/${c.case_id}`}
                        className="font-mono text-xs text-teal-700 dark:text-teal-400 hover:underline"
                      >
                        {c.case_id}
                      </Link>
                    </td>
                    <td className="px-4 py-3">{c.primary_diagnosis}</td>
                    <td className="px-4 py-3 capitalize">{c.discharge_disposition.replace("_", " ")}</td>
                    <td className="px-4 py-3">{c.payer}</td>
                    <td className="px-4 py-3">
                      <StatusPill status={c.status} />
                    </td>
                    <td className="px-4 py-3 text-black/50 dark:text-white/50">
                      {new Date(c.updated_at).toLocaleTimeString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
