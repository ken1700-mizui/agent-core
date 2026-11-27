"""Marks of the tests whose setup needs a privilege only root has.

Never a skip: without root such a test fails as ``PRIVILEGED_NOT_RUN``
(``tests/conftest.py``), unless deselected with ``-m "not privileged"``. Run
them as root with ``-m privileged`` (WP-01: a separate CI step).
"""

from __future__ import annotations

import pytest

MKNOD = pytest.mark.privileged(reason="creating a character device needs CAP_MKNOD (root)")
CHOWN = pytest.mark.privileged(reason="giving a file to another user needs CAP_CHOWN (root)")
