"use client";

import type { EventItem } from "@/lib/types";

/** The pipeline as a step row, derived purely from the case's event log —
 * each step lights up when its event exists, pulses while the pipeline is
 * still running, and the review step waits amber until a human acts. */

interface StepDef {
  label: string;
  match: string[]; // event types that complete this step
}

const AGENT_STEPS: StepDef[] = [
  { label: "Intake", match: ["case.created"] },
  { label: "Referral", match: ["referral.routed"] },
  { label: "Follow-up", match: ["followup.scheduled"] },
  { label: "Medication", match: ["medication.instructions_ready"] },
  { label: "Outreach", match: ["outreach.attempted"] },
  { label: "Readiness", match: ["discharge.assessed"] },
  { label: "Composite", match: ["case.risk_assessed"] },
  { label: "Routing", match: ["case.auto_completed", "case.needs_review"] },
];

const REVIEW_EVENTS = ["case.review_approved", "case.review_overridden", "case.review_rejected"];
const TERMINAL_STATUSES = new Set(["auto_completed", "completed", "rejected"]);

function formatMs(ms: number | null): string {
  if (ms === null) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

type StepState = "done" | "running" | "waiting" | "pending";

function Step({
  label,
  state,
  detail,
}: {
  label: string;
  state: StepState;
  detail?: string;
}) {
  const icon = {
    done: <span className="text-teal-600 dark:text-teal-500">✓</span>,
    running: (
      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-teal-500" />
    ),
    waiting: <span className="text-amber-600 dark:text-amber-400">⏳</span>,
    pending: <span className="text-black/20 dark:text-white/20">○</span>,
  }[state];

  const labelColor = {
    done: "text-black/80 dark:text-white/80",
    running: "text-teal-700 dark:text-teal-400",
    waiting: "text-amber-700 dark:text-amber-400",
    pending: "text-black/30 dark:text-white/30",
  }[state];

  return (
    <div className="flex items-center gap-1.5">
      <span className="flex w-4 justify-center text-xs">{icon}</span>
      <div className="leading-tight">
        <div className={`text-xs font-medium ${labelColor}`}>{label}</div>
        {detail && (
          <div className="text-[10px] tabular-nums text-black/40 dark:text-white/40">{detail}</div>
        )}
      </div>
    </div>
  );
}

export function PipelineSteps({
  events,
  status,
}: {
  events: EventItem[];
  status: string;
}) {
  const byType = new Map<string, EventItem>();
  for (const event of events) {
    if (!byType.has(event.event_type)) byType.set(event.event_type, event);
  }
  const inFlight = !TERMINAL_STATUSES.has(status) && status !== "needs_review";

  const steps = AGENT_STEPS.map((step) => {
    const hit = step.match.map((t) => byType.get(t)).find(Boolean);
    if (hit) {
      const outcome =
        step.label === "Routing"
          ? hit.event_type === "case.auto_completed"
            ? "auto-complete"
            : "→ review"
          : formatMs(hit.duration_ms);
      return { label: step.label, state: "done" as StepState, detail: outcome };
    }
    return {
      label: step.label,
      state: (inFlight ? "running" : "pending") as StepState,
      detail: undefined,
    };
  });

  // Human review step only exists on the review path.
  const routedToReview = byType.has("case.needs_review");
  if (routedToReview) {
    const reviewEvent = REVIEW_EVENTS.map((t) => byType.get(t)).find(Boolean);
    if (reviewEvent) {
      steps.push({
        label: "Human review",
        state: "done",
        detail: reviewEvent.event_type.replace("case.review_", ""),
      });
    } else {
      steps.push({ label: "Human review", state: "waiting", detail: "awaiting decision" });
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-3">
      {steps.map((step, i) => (
        <div key={step.label} className="flex items-center gap-2">
          {i > 0 && <span className="text-black/15 dark:text-white/15">—</span>}
          <Step label={step.label} state={step.state} detail={step.detail} />
        </div>
      ))}
    </div>
  );
}
