"""The application layer: what the system *does* to a case.

    services/
      router.py       fans a case.created event out to the agents, then routes on
                      aggregate confidence — auto-complete or escalate
      review_gate.py  parks a low-confidence case until a human calls act()
      drafting.py     composes the draft discharge summary from agent decisions,
                      deterministically — no second LLM call
      workflow.py     who owns a case right now, and what text a human approved
      rag.py          the knowledge base the agents retrieve grounded content from

What is deliberately *not* here: `bus.py`, `persistence.py`, `audit.py`,
`middleware.py`, `staff_auth.py`, `fixtures.py`. Those are infrastructure — they
carry, store, or guard the work rather than perform it. A service may use them;
none of them may import a service.

Imported by path (`from carebridge.services.workflow import ...`) rather than
re-exported here, so a reader can see which service a call belongs to without
opening this file.
"""
