"""Steps 7-8 — same 3 fixtures as demo.py, but run against a real Postgres
database, then queries the DB back to prove every agent run and state
transition actually landed in a table — including the audit trail, which is
its own append-only store even though it's the same Postgres instance.

Requires: docker compose up -d   (see docker-compose.yml)
Run with: source venv/bin/activate && python demo_persistence.py
"""

import asyncio

from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.audit import AuditLogRecord, AuditTrail
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK
from carebridge.persistence import AgentDecisionRecord, CaseRecord, Database, EventRecord
from carebridge.review_gate import HumanReviewGate, fake_human_reviewer
from carebridge.router import ConfidenceRouter


async def main() -> None:
    db = Database()
    db.reset_schema()
    audit = AuditTrail(db)

    bus = EventBus(db=db, audit=audit)
    ReferralRoutingAgent(bus)
    ConfidenceRouter(bus, listens_to="referral.routed", threshold=0.75)
    gate = HumanReviewGate(bus)

    for case in (CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK):
        await bus.publish(Event(event_type="case.created", case=case))

    for case_id in list(gate.pending.keys()):
        review = gate.pending[case_id]
        status, note = fake_human_reviewer(review)
        await gate.act(case_id, status, reviewer="demo-reviewer", note=note)

    print("=" * 78)
    print("Postgres — cases table")
    print("=" * 78)
    with db.Session() as session:
        for row in session.query(CaseRecord).order_by(CaseRecord.case_id).all():
            print(f"  {row.case_id:8} status={row.status:16} updated_at={row.updated_at}")

        print("\n" + "=" * 78)
        print("Postgres — agent_decisions table")
        print("=" * 78)
        for row in session.query(AgentDecisionRecord).order_by(AgentDecisionRecord.case_id).all():
            print(f"  {row.case_id:8} agent={row.agent_name:18} confidence={row.confidence:<5} decision={row.decision}")

        print("\n" + "=" * 78)
        print("Postgres — events table (case-C's full history)")
        print("=" * 78)
        rows = session.query(EventRecord).filter_by(case_id="case-C").order_by(EventRecord.id).all()
        for row in rows:
            print(f"  #{row.id:<3} {row.event_type:24} at {row.occurred_at}")

        print("\n" + "=" * 78)
        print("Postgres — audit_log table (append-only, separate from the event log)")
        print("=" * 78)
        for row in session.query(AuditLogRecord).order_by(AuditLogRecord.id).all():
            conf = f"{row.confidence:.2f}" if row.confidence is not None else "  -  "
            reviewer = row.reviewer or "-"
            print(
                f"  #{row.id:<3} {row.case_id:8} agent={row.agent_id:18} "
                f"confidence={conf} decision={row.decision:12} reviewer={reviewer}"
            )


if __name__ == "__main__":
    asyncio.run(main())
