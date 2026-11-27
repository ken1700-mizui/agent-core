"""Event hash specification (WP-03): sha256 over a domain tag and canonical JSON."""

from __future__ import annotations

import hashlib

from agent_core.types.event_hash import EVENT_HASH_DOMAIN, EVENT_HASH_VERSION, event_hash
from agent_core.types.serialization import dump_json
from support import builders as b


def test_hash_is_sha256_of_domain_tag_and_canonical_json() -> None:
    event = b.event()
    expected = hashlib.sha256(EVENT_HASH_DOMAIN + dump_json(event)).hexdigest()
    assert event_hash(event) == "sha256:" + expected
    assert EVENT_HASH_VERSION == 1
    assert EVENT_HASH_DOMAIN == b"agent-core/event-hash/v1\x00"


def test_hash_is_deterministic_and_sensitive_to_every_position_field() -> None:
    event = b.event(tenant_seq=2, prev_hash=b.digest("p"))
    assert event_hash(event) == event_hash(type(event).model_validate_json(dump_json(event)))
    changed = [
        b.event(event_id=event.event_id, tenant_seq=3, prev_hash=b.digest("p")),
        b.event(event_id=event.event_id, tenant_seq=2, prev_hash=b.digest("q")),
        b.event(event_id=event.event_id, tenant_seq=2, prev_hash=b.digest("p"), labels=("other",)),
    ]
    assert len({event_hash(e) for e in [event, *changed]}) == 4
