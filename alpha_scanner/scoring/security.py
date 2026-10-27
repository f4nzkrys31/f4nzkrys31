"""Security assessment: turns an audit report into flags + a risk contribution.

Rule: a token without a usable audit is UNVERIFIED, never "safe". Unverified
tokens carry a fixed risk penalty and an explicit flag that the dashboard and
alerts display.
"""

from __future__ import annotations

from ..models import Flag, SecurityReport, SecurityStatus

UNVERIFIED_PENALTY = 30.0


def security_flags(report: SecurityReport | None) -> tuple[list[Flag], float]:
    """Return (flags, risk_points)."""
    if report is None or report.status != SecurityStatus.CHECKED:
        status = report.status.value if report else SecurityStatus.NOT_CHECKED.value
        why = {
            "unsupported": "audit provider has no data for this token/chain",
            "unavailable": "audit provider unreachable or errored",
            "not_checked": "not audited yet",
        }.get(status, status)
        return [Flag(code="SECURITY_UNVERIFIED", severity="warn",
                     message=f"Contract security UNVERIFIED ({why}) — treat as unknown, not safe")], UNVERIFIED_PENALTY

    flags: list[Flag] = []
    points = 0.0
    lvl = report.risk_level or 0
    points += lvl * 10
    if lvl >= 4:
        flags.append(Flag(code="AUDIT_HIGH_RISK", severity="critical",
                          message=f"Audit risk level {lvl}/5 ({report.risk_enum}) — avoid / block"))
    elif lvl >= 2:
        flags.append(Flag(code="AUDIT_MEDIUM_RISK", severity="warn", message=f"Audit risk level {lvl}/5 ({report.risk_enum})"))
    for h in report.hits:
        honeypot = "honeypot" in h.title.lower() or "cannot sell" in (h.description or "").lower()
        if honeypot:
            flags.append(Flag(code="AUDIT_HONEYPOT", severity="critical", message=f"{h.category}: {h.title}"))
            points += 50
        elif h.severity == "RISK":
            flags.append(Flag(code="AUDIT_RISK_ITEM", severity="warn", message=f"{h.category}: {h.title}"))
            points += 15
        else:
            flags.append(Flag(code="AUDIT_CAUTION_ITEM", severity="info", message=f"{h.category}: {h.title}"))
            points += 4
    if report.verified is False:
        flags.append(Flag(code="UNVERIFIED_SOURCE", severity="info", message="Contract source code not verified"))
        points += 5
    return flags, points
