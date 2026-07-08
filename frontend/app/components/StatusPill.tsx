import type { CaseStatus } from "@/lib/types";

const STYLES: Record<CaseStatus, string> = {
  received: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  in_progress: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  needs_review: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  auto_completed: "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300",
  completed: "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300",
  rejected: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
};

const LABELS: Record<CaseStatus, string> = {
  received: "Received",
  in_progress: "In progress",
  needs_review: "Needs review",
  auto_completed: "Auto-completed",
  completed: "Completed",
  rejected: "Rejected",
};

export function StatusPill({ status }: { status: CaseStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STYLES[status]}`}
    >
      {LABELS[status]}
    </span>
  );
}
