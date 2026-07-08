"""One module per vendor. Each exports a client satisfying `llm.base.LLMClient`.

Vendor SDKs are imported inside each client's `__init__`, never at module import
time, so selecting one provider does not require installing the other two.
"""
