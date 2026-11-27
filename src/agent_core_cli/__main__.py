"""``python -m agent_core_cli``."""

from __future__ import annotations

import sys

from agent_core_cli.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], stdout=sys.stdout, stderr=sys.stderr))
