"""Public, lazy composition API for Hashmarks repository evidence.

The export table is the supported capability census. Implementation modules stay
private to this boundary; resolving an export returns its original object and
does not load the producer or perform repository work.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_tools import (
        PreparedAgentEvidence as PreparedAgentEvidence,
    )
    from .agent_tools import (
        agent_evidence_main as agent_evidence_main,
    )
    from .agent_tools import (
        prepare_agent_evidence as prepare_agent_evidence,
    )
    from .agent_tools import (
        prompt_observation as prompt_observation,
    )
    from .agent_tools import (
        repository_observation as repository_observation,
    )
    from .agent_tools import (
        task_evidence_prompt as task_evidence_prompt,
    )
    from .dependencies import (
        LockedInventoryAssessment as LockedInventoryAssessment,
    )
    from .dependencies import (
        assess_locked_dependencies as assess_locked_dependencies,
    )
    from .dependencies import (
        observe_locked_dependencies as observe_locked_dependencies,
    )
    from .dependencies import (
        require_current_dependency_environment as require_current_dependency_environment,
    )
    from .development_verification import (
        nominate_changed_verification as nominate_changed_verification,
    )
    from .development_verification import (
        observe_repository_assets as observe_repository_assets,
    )
    from .identity import (
        HashmarksIdentityResult as HashmarksIdentityResult,
    )
    from .identity import (
        HashmarksTreeIdentity as HashmarksTreeIdentity,
    )
    from .identity import (
        identify_path as identify_path,
    )
    from .identity import (
        identify_tree_and_children as identify_tree_and_children,
    )
    from .repository_intelligence import (
        HashmarksEvidenceDisclosure as HashmarksEvidenceDisclosure,
    )
    from .repository_intelligence import (
        HashmarksEvidenceKind as HashmarksEvidenceKind,
    )
    from .repository_intelligence import (
        HashmarksRepositoryIntelligenceInputError as HashmarksRepositoryIntelligenceInputError,
    )
    from .repository_intelligence import (
        VerifiedHashmarksRepositoryEvidence as VerifiedHashmarksRepositoryEvidence,
    )
    from .repository_intelligence import (
        bind_hashmarks_repository_evidence as bind_hashmarks_repository_evidence,
    )
    from .repository_intelligence import (
        load_hashmarks_repository_evidence as load_hashmarks_repository_evidence,
    )
    from .runtime_contract import (
        HashmarksRuntimeContractError as HashmarksRuntimeContractError,
    )
    from .runtime_contract import (
        loaded_hashmarks_producer_identity as loaded_hashmarks_producer_identity,
    )
    from .runtime_contract import (
        loaded_hashmarks_producer_provenance as loaded_hashmarks_producer_provenance,
    )
    from .runtime_contract import (
        loaded_hashmarks_version as loaded_hashmarks_version,
    )
    from .runtime_contract import (
        native_producer_implementation_identity as native_producer_implementation_identity,
    )
    from .runtime_contract import (
        require_loaded_hashmarks_runtime as require_loaded_hashmarks_runtime,
    )
    from .verification_selection import (
        HashmarksVerificationSelectionInputError as HashmarksVerificationSelectionInputError,
    )
    from .verification_selection import (
        VerifiedHashmarksVerificationSelection as VerifiedHashmarksVerificationSelection,
    )
    from .verification_selection import (
        load_hashmarks_verification_selection as load_hashmarks_verification_selection,
    )

_EXPORTS = {
    "PreparedAgentEvidence": "agent_tools",
    "agent_evidence_main": "agent_tools",
    "prepare_agent_evidence": "agent_tools",
    "prompt_observation": "agent_tools",
    "repository_observation": "agent_tools",
    "task_evidence_prompt": "agent_tools",
    "LockedInventoryAssessment": "dependencies",
    "assess_locked_dependencies": "dependencies",
    "observe_locked_dependencies": "dependencies",
    "require_current_dependency_environment": "dependencies",
    "nominate_changed_verification": "development_verification",
    "observe_repository_assets": "development_verification",
    "HashmarksIdentityResult": "identity",
    "HashmarksTreeIdentity": "identity",
    "identify_path": "identity",
    "identify_tree_and_children": "identity",
    "HashmarksEvidenceDisclosure": "repository_intelligence",
    "HashmarksEvidenceKind": "repository_intelligence",
    "HashmarksRepositoryIntelligenceInputError": "repository_intelligence",
    "VerifiedHashmarksRepositoryEvidence": "repository_intelligence",
    "bind_hashmarks_repository_evidence": "repository_intelligence",
    "load_hashmarks_repository_evidence": "repository_intelligence",
    "HashmarksRuntimeContractError": "runtime_contract",
    "loaded_hashmarks_producer_identity": "runtime_contract",
    "loaded_hashmarks_producer_provenance": "runtime_contract",
    "loaded_hashmarks_version": "runtime_contract",
    "native_producer_implementation_identity": "runtime_contract",
    "require_loaded_hashmarks_runtime": "runtime_contract",
    "HashmarksVerificationSelectionInputError": "verification_selection",
    "VerifiedHashmarksVerificationSelection": "verification_selection",
    "load_hashmarks_verification_selection": "verification_selection",
}

__all__ = tuple(_EXPORTS)


def __getattr__(name: str) -> object:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"{__name__!r} has no public capability {name!r}")
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
