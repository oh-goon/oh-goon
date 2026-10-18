from __future__ import annotations

from oh_goon.core.models.attention import AttentionGroup, AttentionRepository, FleetAttention
from oh_goon.core.models.explanation import ReasonCode, reason_semantics
from oh_goon.core.projections.fleet_context import FleetProjectionContext
from oh_goon.core.vocabulary import RiskLevel


def build_fleet_attention(context: FleetProjectionContext) -> FleetAttention:
    """Group current unresolved repository guidance from one indexed fleet cut."""
    groups: dict[ReasonCode, list[AttentionRepository]] = {}
    healthy = 0
    healthy_codes = {
        ReasonCode.READY,
        ReasonCode.DEPENDENCIES_UPDATED,
        ReasonCode.NO_UPDATES,
        ReasonCode.REVIEW_PREPARED,
    }
    for repo in context.included:
        explanation = repo.guidance
        if explanation.reason_code in healthy_codes:
            healthy += 1
            continue
        item = AttentionRepository(
            repository=repo.repository,
            reason_code=explanation.reason_code,
            phase=explanation.phase,
            risk=explanation.risk,
            can_auto_fix=explanation.can_auto_fix,
            auto_fix_action=explanation.auto_fix_action,
        )
        groups.setdefault(explanation.reason_code, []).append(item)
    risk_order = {RiskLevel.HIGH: 0, RiskLevel.MEDIUM: 1, RiskLevel.LOW: 2, RiskLevel.NONE: 3}
    projected: list[AttentionGroup] = []
    for reason_code, items in groups.items():
        semantics = reason_semantics(reason_code)
        projected.append(
            AttentionGroup(
                reason_code=reason_code,
                phase=semantics.phase,
                risk=semantics.risk,
                repositories=sorted(items, key=lambda item: item.repository.name.lower()),
            )
        )
    projected.sort(key=lambda group: (risk_order[group.risk], group.reason_code.value))
    return FleetAttention(healthy=healthy, groups=projected)
