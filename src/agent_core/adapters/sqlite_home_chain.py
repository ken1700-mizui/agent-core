"""The home chain store of one experiment (WP2-08; contract 7.3-7.8, D19).

A SQLite file, private to the owner (0600), holding the owner-MAC'd opening,
closing and resolution records. What the MAC gives and what it does not:

- the MAC detects a changed or forged record (``ChainIntegrityError`` on every
  read; nothing is trusted that fails it);
- uniqueness is NOT from the MAC: it is the database's UNIQUE constraints (one
  opening per home, per index and per closing; one closing per home and per
  successor; one resolution per (origin home, reservation)), inside one
  transaction per record;
- exclusion is an OS lock (``flock``) held while the store is open: one runner
  per experiment (``ChainBusyError`` for a second one);
- a deleted or rolled-back file is NOT detected here (no independent witness
  until Phase 3A); the ledgers then disagree with the chain and new trials are
  refused by the admission check, but a fresh chain on a fresh home is not
  prevented (``docs/design/phase-2/WP2-08_COST_ENGINE.md``).

Recording a record that is already there with the same content is a no-op;
with different content it is a ``ChainConflictError``.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import os
import sqlite3
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ValidationError

from agent_core.types.errors import AgentCoreError
from agent_core.types.experiment_budget import (
    CarriedHoldResolved,
    ClosingStatement,
    OpeningStatement,
)
from agent_core.types.ids import ExperimentId
from agent_core.types.serialization import dump_json

_MAC_DOMAIN: Final = b"agent-core/experiment-chain-mac/v1\x00"
_SCHEMA: Final = (
    """CREATE TABLE IF NOT EXISTS openings (
    home_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    prev_closing_hash TEXT UNIQUE,
    body TEXT NOT NULL,
    mac TEXT NOT NULL,
    UNIQUE (experiment_id, idx)
) STRICT""",
    """CREATE TABLE IF NOT EXISTS closings (
    home_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    closing_hash TEXT NOT NULL UNIQUE,
    successor_home_id TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL,
    mac TEXT NOT NULL
) STRICT""",
    """CREATE TABLE IF NOT EXISTS resolutions (
    experiment_id TEXT NOT NULL,
    origin_home_id TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    body TEXT NOT NULL,
    mac TEXT NOT NULL,
    PRIMARY KEY (experiment_id, origin_home_id, reservation_id)
) STRICT""",
)


class ChainError(AgentCoreError):
    code = "EXPERIMENT_CHAIN"
    fatal = True


class ChainIntegrityError(ChainError):
    """A record fails its MAC or does not parse: nothing in the chain is trusted."""

    code = "EXPERIMENT_CHAIN_INTEGRITY"


class ChainConflictError(ChainError):
    """A second, different record where only one is allowed."""

    code = "EXPERIMENT_CHAIN_CONFLICT"
    fatal = False


class ChainBusyError(ChainError):
    """Another runner holds this experiment's chain."""

    code = "EXPERIMENT_CHAIN_BUSY"
    fatal = False


class HomeChainStore:
    def __init__(self, db: sqlite3.Connection, lock_fd: int, key: bytes) -> None:
        self._db = db
        self._lock_fd = lock_fd
        self._key = key

    @classmethod
    def open(cls, path: Path, *, key: bytes) -> HomeChainStore:
        if len(key) < 16:
            raise ValueError("the chain key is at least 16 bytes")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(lock_fd)
            raise ChainBusyError("another runner holds this experiment") from exc
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            if path.stat().st_mode & 0o077:
                raise ChainIntegrityError("the chain file is readable by group or others")
            db = sqlite3.connect(path, isolation_level=None)
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            for statement in _SCHEMA:
                db.execute(statement)
        except BaseException:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            raise
        return cls(db, lock_fd, key)

    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self._db.close()
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        os.close(self._lock_fd)

    # ------------------------------------------------------------ MAC

    def _mac(self, kind: str, body: str) -> str:
        message = _MAC_DOMAIN + kind.encode() + b"\x00" + body.encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def _checked[M: BaseModel](self, kind: str, body: str, mac: str, model: type[M]) -> M:
        if not hmac.compare_digest(self._mac(kind, body), mac):
            raise ChainIntegrityError(f"a {kind} record fails its MAC")
        try:
            return model.model_validate_json(body)
        except ValidationError as exc:
            raise ChainIntegrityError(f"a {kind} record does not parse") from exc

    def _insert(self, kind: str, sql: str, values: tuple[object, ...], body: str) -> None:
        mac = self._mac(kind, body)
        try:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(sql, (*values, body, mac))
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            raise ChainConflictError(f"a different {kind} record is already there") from exc

    # ------------------------------------------------------------ records

    def record_opening(self, statement: OpeningStatement) -> None:
        body = dump_json(statement).decode()
        found = self._db.execute(
            "SELECT body, mac FROM openings WHERE home_id = ?", (str(statement.home_id),)
        ).fetchone()
        if found is not None:
            if self._checked("opening", found[0], found[1], OpeningStatement) != statement:
                raise ChainConflictError("a different opening is already there for this home")
            return
        self._insert(
            "opening",
            "INSERT INTO openings (home_id, experiment_id, idx, prev_closing_hash, body, mac) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(statement.home_id),
                str(statement.experiment_id),
                statement.index,
                statement.prev_closing_hash,
            ),
            body,
        )

    def record_closing(self, statement: ClosingStatement) -> None:
        body = dump_json(statement).decode()
        found = self._db.execute(
            "SELECT body, mac FROM closings WHERE home_id = ?", (str(statement.home_id),)
        ).fetchone()
        if found is not None:
            if self._checked("closing", found[0], found[1], ClosingStatement) != statement:
                raise ChainConflictError("a different closing is already there for this home")
            return
        self._insert(
            "closing",
            "INSERT INTO closings (home_id, experiment_id, closing_hash, successor_home_id, "
            "body, mac) VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(statement.home_id),
                str(statement.experiment_id),
                statement.closing_hash(),
                str(statement.successor_home_id),
            ),
            body,
        )

    def record_resolution(self, record: CarriedHoldResolved) -> None:
        body = dump_json(record).decode()
        found = self._db.execute(
            "SELECT body, mac FROM resolutions WHERE experiment_id = ? AND origin_home_id = ? "
            "AND reservation_id = ?",
            (str(record.experiment_id), str(record.origin_home_id), str(record.reservation_id)),
        ).fetchone()
        if found is not None:
            if self._checked("resolution", found[0], found[1], CarriedHoldResolved) != record:
                raise ChainConflictError("this carried hold is already resolved differently")
            return
        seq = self._db.execute(
            "SELECT COUNT(*) FROM resolutions WHERE experiment_id = ?", (str(record.experiment_id),)
        ).fetchone()[0]
        self._insert(
            "resolution",
            "INSERT INTO resolutions "
            "(experiment_id, origin_home_id, reservation_id, seq, body, mac) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(record.experiment_id),
                str(record.origin_home_id),
                str(record.reservation_id),
                seq,
            ),
            body,
        )

    def openings(self, experiment_id: ExperimentId) -> tuple[OpeningStatement, ...]:
        rows = self._db.execute(
            "SELECT body, mac FROM openings WHERE experiment_id = ? ORDER BY idx",
            (str(experiment_id),),
        ).fetchall()
        return tuple(self._checked("opening", b, m, OpeningStatement) for b, m in rows)

    def closings(self, experiment_id: ExperimentId) -> tuple[ClosingStatement, ...]:
        rows = self._db.execute(
            "SELECT body, mac FROM closings WHERE experiment_id = ? ORDER BY rowid",
            (str(experiment_id),),
        ).fetchall()
        return tuple(self._checked("closing", b, m, ClosingStatement) for b, m in rows)

    def resolutions(self, experiment_id: ExperimentId) -> tuple[CarriedHoldResolved, ...]:
        rows = self._db.execute(
            "SELECT body, mac FROM resolutions WHERE experiment_id = ? ORDER BY seq",
            (str(experiment_id),),
        ).fetchall()
        return tuple(self._checked("resolution", b, m, CarriedHoldResolved) for b, m in rows)
