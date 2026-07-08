"use client";

import { useCallback, useEffect, useState } from "react";
import { getStats } from "@/lib/api";
import { AGENT_LABELS } from "@/lib/labels";
import { StatusPill } from "@/app/components/StatusPill";
import type { AgentStats, CaseStatus, Stats } from "@/lib/types";

const REFRESH_MS = 15000;
// Single-series bar hue, palette-validated for light and dark surfaces.
const BAR_COLOR = "#0d9488";
const CONFIDENCE_THRESHOLD = 0.75;

function formatMs(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function StatTile({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="card px-4 py-3">
      <div className="text-xs uppercase tracking-wider text-black/40 dark:text-white/40">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-black/40 dark:text-white/40">{hint}</div>}
    </div>
  );
}

function AgentBars({
  title,
  agents,
  value,
  format,
  max,
  threshold,
}: {
  title: string;
  agents: AgentStats[];
  value: (a: AgentStats) => number | null;
  format: (v: number) => string;
  max: number;
  threshold?: number;
}) {
  return (
    <div className="card p-5">
      <h2 className="text-sm font-medium mb-4">{title}</h2>
      <div className="space-y-2">
        {agents.map((agent) => {
          const v = value(agent);
          const width = v === null ? 0 : Math.max((v / max) * 100, 1.5);
          return (
            <div key={agent.agent_id} className="group flex items-center gap-3">
              <div className="w-44 shrink-0 text-xs">
                <span className="text-black/70 dark:text-white/70">
                  {AGENT_LABELS[agent.agent_name] ?? agent.agent_name}
                </span>
              </div>
              <div className="relative h-4 flex-1">
                {threshold !== undefined && (
                  <div
                    className="absolute inset-y-0 border-l border-dashed border-black/25 dark:border-white/25"
                    style={{ left: `${(threshold / max) * 100}%` }}
                  />
                )}
                {v !== null && (
                  <div
                    className="absolute inset-y-0.5 left-0 rounded-r"
                    style={{ width: `${width}%`, backgroundColor: BAR_COLOR }}
                  />
                )}
                {/* hover detail — decisions behind this average */}
                <div className="pointer-events-none absolute -top-7 left-0 hidden rounded bg-zinc-800 px-2 py-1 text-[10px] text-zinc-100 shadow group-hover:block">
                  {agent.agent_id} · {agent.decisions} decision
                  {agent.decisions === 1 ? "" : "s"}
                </div>
              </div>
              <div className="w-14 shrink-0 text-right text-xs tabular-nums text-black/70 dark:text-white/70">
                {v === null ? "—" : format(v)}
              </div>
            </div>
          );
        })}
      </div>
      {threshold !== undefined && (
        <p className="mt-3 text-xs text-black/40 dark:text-white/40">
          Dashed line: {threshold} auto-complete threshold — a composite below it goes to
          human review.
        </p>
      )}
    </div>
  );
}

export default function StatsPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStats(await getStats());
      setError(null);
      setUpdatedAt(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load stats");
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  if (error) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
      </div>
    );
  }
  if (stats === null) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-sm text-black/50 dark:text-white/50">Loading…</p>
      </div>
    );
  }

  const closed =
    (stats.cases_by_status.auto_completed ?? 0) +
    (stats.cases_by_status.completed ?? 0) +
    (stats.cases_by_status.rejected ?? 0);
  const maxDuration = Math.max(
    ...stats.agents.map((a) => a.avg_duration_ms ?? 0),
    1,
  );

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 space-y-8">
      <section className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight mb-1">Pipeline stats</h1>
          <p className="text-sm text-black/50 dark:text-white/50">
            Live aggregates from every case this pipeline has processed.
          </p>
        </div>
        <p className="text-xs text-black/40 dark:text-white/40">
          {updatedAt ? `updated ${updatedAt}` : ""} · refreshes every {REFRESH_MS / 1000}s
        </p>
      </section>

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile label="Cases processed" value={String(stats.total_cases)} />
        <StatTile
          label="Auto-completed"
          value={
            stats.auto_complete_rate === null
              ? "—"
              : `${Math.round(stats.auto_complete_rate * 100)}%`
          }
          hint={closed ? `of ${closed} closed case${closed === 1 ? "" : "s"}, zero human touch` : "no closed cases yet"}
        />
        <StatTile
          label="Avg composite confidence"
          value={
            stats.avg_composite_confidence === null
              ? "—"
              : stats.avg_composite_confidence.toFixed(2)
          }
          hint={`threshold ${CONFIDENCE_THRESHOLD}`}
        />
        <StatTile
          label="Awaiting review"
          value={String(stats.pending_review)}
          hint={`${stats.reviews.approved ?? 0} approved · ${stats.reviews.rejected ?? 0} rejected`}
        />
        <StatTile
          label="Avg human wait"
          value={formatMs(stats.avg_review_wait_ms)}
          hint="needs-review → decision"
        />
      </section>

      <section className="card p-5">
        <h2 className="text-sm font-medium mb-3">Cases by status</h2>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {Object.entries(stats.cases_by_status).map(([status, count]) => (
            <div key={status} className="flex items-center gap-2">
              <StatusPill status={status as CaseStatus} />
              <span className="text-sm tabular-nums text-black/70 dark:text-white/70">
                {count}
              </span>
            </div>
          ))}
          {stats.total_cases === 0 && (
            <p className="text-sm text-black/50 dark:text-white/50">
              No cases yet — create one from the Cases page.
            </p>
          )}
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <AgentBars
          title="Average confidence by agent"
          agents={stats.agents}
          value={(a) => a.avg_confidence}
          format={(v) => v.toFixed(2)}
          max={1}
          threshold={CONFIDENCE_THRESHOLD}
        />
        <AgentBars
          title="Average decision time by agent"
          agents={stats.agents}
          value={(a) => a.avg_duration_ms}
          format={formatMs}
          max={maxDuration}
        />
      </section>
      <p className="text-xs text-black/40 dark:text-white/40">
        Decision time is the agent&apos;s own work per case — LLM-backed agents (medication,
        outreach, readiness) carry the model round-trip; rule-based agents decide in
        milliseconds.
      </p>
    </div>
  );
}
