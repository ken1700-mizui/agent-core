"""The minimal Agent Core CLI (WP-10, Phase 1: one synthetic Runtime).

This package is the composition root (outside ``agent_core``): it opens the
journal (which holds the Runtime's OS lock), builds the runtime, the execution
gate, the synthetic file broker and the agent loop, and lets the owner start a
synthetic demo task, look at its state, approve exactly the action that waits,
and stop. It adds no authority of its own: every change is an owner-signed
ledger operation or an exact approval through the owner channel, and the
ledger and the Safety and Cost rules decide.

Start it with ``PYTHONPATH=src python -m agent_core_cli --help``.
"""
