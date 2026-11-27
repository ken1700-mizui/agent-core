"""Privileged tests (non-root CI preparation, written first).

A test whose setup needs a privilege only root has (``CAP_MKNOD`` to make a
device node, ``CAP_CHOWN`` to give a file to another user) carries
``@pytest.mark.privileged(reason=...)``. It is never skipped: run without root
it fails as ``PRIVILEGED_NOT_RUN`` (so it is never counted as a success), unless
it is deselected explicitly with ``-m "not privileged"``. Its guarantee is kept
by running ``-m privileged`` as root.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

import conftest
from support import privileged


class _Item:
    def __init__(self, marker: Any) -> None:
        self._marker = marker

    def get_closest_marker(self, name: str) -> Any:
        return self._marker if name == "privileged" else None


def _mark(**kwargs: str) -> Any:
    return pytest.mark.privileged(**kwargs).mark


def test_a_privileged_test_without_root_fails_and_is_not_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1_000)
    with pytest.raises(pytest.fail.Exception, match="PRIVILEGED_NOT_RUN"):
        conftest.pytest_runtest_setup(_Item(_mark(reason="needs CAP_MKNOD")))


def test_a_privileged_test_runs_as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    conftest.pytest_runtest_setup(_Item(_mark(reason="needs CAP_MKNOD")))


def test_a_privileged_mark_states_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(pytest.fail.Exception, match="PRIVILEGED_REASON_MISSING"):
        conftest.pytest_runtest_setup(_Item(_mark()))


def test_an_ordinary_test_is_not_affected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1_000)
    conftest.pytest_runtest_setup(_Item(None))


def test_the_reasons_name_the_privilege() -> None:
    reasons = {m.mark.kwargs["reason"] for m in (privileged.MKNOD, privileged.CHOWN)}
    assert reasons == {
        "creating a character device needs CAP_MKNOD (root)",
        "giving a file to another user needs CAP_CHOWN (root)",
    }
