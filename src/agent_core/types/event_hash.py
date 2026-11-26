"""Event hash, version 1 (WP-03).

``event_hash(e) = "sha256:" + hex(SHA-256(EVENT_HASH_DOMAIN || dump_json(e)))``

- Input: the whole stored Event, including ``tenant_seq``, ``run_seq`` and
  ``prev_hash``. Because ``prev_hash`` is the previous event's hash, each hash
  covers the tenant's entire history up to that event.
- Chain scope: one chain per tenant, in ``tenant_seq`` order. The first event
  (``tenant_seq`` 1) has no ``prev_hash``.
- Normalization: ``dump_json`` (keys sorted, compact, UTF-8; the Phase 1
  internal JSON format, not verified against RFC 8785).
- The domain tag separates this hash from any other use of SHA-256 and carries
  the version. A different algorithm or input needs a new version.

Limits: a hash chain alone cannot detect an attacker who rewrites the whole
chain consistently, or a rollback of the whole database to an older, valid
state. That needs an independent witness or signed checkpoints (E 21), which
are not part of WP-03.
"""

from __future__ import annotations

import hashlib

from agent_core.types.records import Event
from agent_core.types.serialization import dump_json

EVENT_HASH_VERSION = 1
EVENT_HASH_DOMAIN = b"agent-core/event-hash/v1\x00"


def event_hash(event: Event) -> str:
    return "sha256:" + hashlib.sha256(EVENT_HASH_DOMAIN + dump_json(event)).hexdigest()
