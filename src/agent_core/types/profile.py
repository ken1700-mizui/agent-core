"""The Runtime's start profile (P-6, PHASE_1_IMPLEMENTATION_PLAN section 2, F07-1).

Phase 1 has exactly one profile, ``phase1_synthetic``: synthetic data only, the
Fake and Replay providers only, no LiveProvider and no Gateway (those code paths
do not exist; the import contracts keep it so). The Runtime's start path
(``RuntimeStateMachine.load``) and the CLI home's configuration use this one
check. There is no default: a missing profile is refused.
"""

from __future__ import annotations

from typing import Final

from agent_core.types.errors import RuntimeProfileError

PHASE1_SYNTHETIC: Final = "phase1_synthetic"


def require_phase1_profile(profile: object) -> None:
    """Refuse anything but exactly the ``phase1_synthetic`` string."""
    if type(profile) is not str or profile != PHASE1_SYNTHETIC:
        raise RuntimeProfileError("RUNTIME_PROFILE_REFUSED")
