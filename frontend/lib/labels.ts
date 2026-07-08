export const AGENT_LABELS: Record<string, string> = {
  referral_routing: "Referral Routing",
  followup_scheduling: "Follow-Up Scheduling",
  medication_instruction: "Medication Instruction",
  patient_outreach: "Patient Outreach",
  discharge_readiness: "Discharge Readiness",
  risk_escalation: "Risk & Escalation (composite)",
  confidence_router: "Confidence Router",
  human_review_gate: "Human Review Gate",
};

export function labelFor(name: string | null): string {
  if (!name) return "Intake";
  return AGENT_LABELS[name] ?? name;
}
