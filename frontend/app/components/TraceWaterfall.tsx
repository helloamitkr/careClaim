import { labelFor } from "@/lib/labels";
import type { EventItem } from "@/lib/types";

const REVIEW_EVENT_TYPES = new Set([
  "case.review_approved",
  "case.review_overridden",
  "case.review_rejected",
]);

const BAR_COLOR: Record<string, string> = {
  risk_escalation: "bg-purple-500",
  confidence_router: "bg-amber-500",
};

// Shorter than the full AGENT_LABELS text — this column is narrow.
const SHORT_LABELS: Record<string, string> = {
  risk_escalation: "Risk & Escalation",
};

function formatDuration(ms: number): string {
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatWait(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60000)}m`;
}

export function TraceWaterfall({ events }: { events: EventItem[] }) {
  const reviewEvent = events.find((e) => REVIEW_EVENT_TYPES.has(e.event_type));
  const pipelineEvents = events.filter((e) => !REVIEW_EVENT_TYPES.has(e.event_type));

  const spans = pipelineEvents.map((e) => {
    const end = new Date(e.occurred_at).getTime();
    const duration = e.duration_ms ?? 0;
    return { event: e, start: end - duration, end, duration };
  });

  const t0 = Math.min(...spans.map((s) => s.start));
  const totalSpan = Math.max(...spans.map((s) => s.end)) - t0 || 1;

  return (
    <div className="space-y-2">
      {spans.map((s, i) => {
        const leftPct = ((s.start - t0) / totalSpan) * 100;
        const widthPct = Math.max((s.duration / totalSpan) * 100, 1.5);
        const producedBy = s.event.produced_by;
        const label = producedBy ? (SHORT_LABELS[producedBy] ?? labelFor(producedBy)) : "Case created";
        const color = producedBy ? BAR_COLOR[producedBy] ?? "bg-teal-500" : "bg-black/30 dark:bg-white/30";

        return (
          <div key={i} className="flex items-center gap-3 text-xs">
            <div className="w-36 shrink-0 text-black/70 dark:text-white/70 truncate" title={label}>
              {label}
            </div>
            <div className="relative h-5 flex-1 rounded bg-black/[0.03] dark:bg-white/[0.05]">
              <div
                className={`absolute h-full rounded ${color}`}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                title={`${label}: ${formatDuration(s.duration)}`}
              />
            </div>
            <div className="w-16 shrink-0 text-right tabular-nums text-black/50 dark:text-white/50">
              {formatDuration(s.duration)}
            </div>
          </div>
        );
      })}

      {reviewEvent && (
        <div className="mt-3 pt-3 border-t border-dashed border-black/10 dark:border-white/10 flex items-center gap-2 text-xs text-black/60 dark:text-white/60">
          <span>⏸</span>
          <span>
            Held for human review — waited{" "}
            <span className="font-medium tabular-nums">
              {formatWait(reviewEvent.duration_ms ?? 0)}
            </span>
            , then <span className="font-medium">{reviewEvent.event_type.replace("case.review_", "")}</span>
          </span>
        </div>
      )}
    </div>
  );
}
