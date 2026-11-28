"""P2: the sandbox broker (WP2-02; C1 2.1; sandbox design 7.6, 7.6.1).

A separate process, meant to run as its own OS user (P2), that alone may talk
to the sandbox backend. In WP2-02 the backend is a deterministic fake: nothing
is executed, no Docker is contacted, no process is spawned.

- It accepts only typed, MAC'd requests from the P1 user (``SO_PEERCRED``).
- It keeps its own append-only, synced execution record (never the Journal or
  the ledger, which it neither reads nor writes): at most one start per Receipt,
  and the highest owner epoch seen.
- It answers with MAC'd responses; P1 records what it verified.

Kept small on purpose. Imports only ``agent_core.types`` from Agent Core
(import contract).
"""
