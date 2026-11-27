"""MACs of the execution gate's reports (WP09-R04).

In Phase 1 the gate runs inside P1 and signs with the P1-only permit key. The
ledger verifies the MAC and every binding in the transaction that uses a
report. A report without a valid MAC, or bound to another receipt, attempt,
owner epoch or gate instance, is refused.
"""

from __future__ import annotations

import hashlib
import hmac

from agent_core.types.safety import (
    EXECUTION_REPORT_DOMAIN,
    NOT_DISPATCHED_DOMAIN,
    ExecutionReport,
    NotDispatchedReport,
    SecretKey,
)
from agent_core.types.serialization import dump_json

_PLACEHOLDER = "0" * 64


def _body(report: NotDispatchedReport | ExecutionReport) -> bytes:
    return dump_json(report.model_copy(update={"mac": _PLACEHOLDER}))


def _domain(report: NotDispatchedReport | ExecutionReport) -> bytes:
    if isinstance(report, NotDispatchedReport):
        return NOT_DISPATCHED_DOMAIN
    return EXECUTION_REPORT_DOMAIN


def report_mac(key: SecretKey, report: NotDispatchedReport | ExecutionReport) -> str:
    return hmac.new(key.reveal(), _domain(report) + _body(report), hashlib.sha256).hexdigest()


def sign(
    key: SecretKey, report: NotDispatchedReport | ExecutionReport
) -> NotDispatchedReport | ExecutionReport:
    return report.model_copy(update={"mac": report_mac(key, report)})


def verify(key: SecretKey, report: NotDispatchedReport | ExecutionReport) -> bool:
    if type(report) not in (NotDispatchedReport, ExecutionReport):
        return False
    return hmac.compare_digest(report_mac(key, report), report.mac)


def report_ref(report: NotDispatchedReport | ExecutionReport) -> str:
    """The digest a runtime transition's evidence names for this report."""
    return "sha256:" + hashlib.sha256(_domain(report) + dump_json(report)).hexdigest()
