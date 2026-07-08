export type CaseStatus =
  | "received"
  | "in_progress"
  | "needs_review"
  | "auto_completed"
  | "completed"
  | "rejected";

export interface FixtureTemplate {
  key: "clean" | "payer_delay" | "high_risk";
  label: string;
  description: string;
  sample: Record<string, unknown>;
}

export interface CaseListItem {
  case_id: string;
  patient_id: string;
  status: CaseStatus;
  primary_diagnosis: string;
  discharge_disposition: string;
  payer: string;
  updated_at: string;
}

export interface AgentDecision {
  agent_name: string;
  decision: string;
  confidence: number;
  rationale: string;
  recorded_at: string;
}

export interface EventItem {
  event_type: string;
  occurred_at: string;
  produced_by: string | null;
  duration_ms: number | null;
}

export interface AuditEntry {
  case_id: string;
  agent_id: string;
  input_summary: string;
  confidence: number | null;
  decision: string;
  rationale: string;
  reviewer: string | null;
  recorded_at: string;
}

export interface PendingReview {
  agent_name: string;
  decision: string;
  confidence: number;
  rationale: string;
}

export interface CaseDetail {
  case: Record<string, unknown>;
  agent_decisions: AgentDecision[];
  events: EventItem[];
  audit: AuditEntry[];
  pending_review: PendingReview | null;
}

export interface CaseCreated {
  case_id: string;
  status: CaseStatus;
}

export type ReviewAction = "approved" | "overridden" | "rejected";

export interface ReviewResult {
  case_id: string;
  event_type: string;
  status: CaseStatus;
}

export interface AgentStats {
  agent_name: string;
  agent_id: string;
  decisions: number;
  avg_confidence: number | null;
  avg_duration_ms: number | null;
}

export interface Stats {
  total_cases: number;
  cases_by_status: Record<string, number>;
  auto_complete_rate: number | null;
  avg_composite_confidence: number | null;
  pending_review: number;
  avg_review_wait_ms: number | null;
  reviews: Record<string, number>;
  agents: AgentStats[];
}
