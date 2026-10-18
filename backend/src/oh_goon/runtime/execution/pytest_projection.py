from __future__ import annotations

from collections.abc import Sequence

from oh_goon.core.models.pytest_selection import StructuredPytestMember


def project_pytest_argv(
    command_prefix: Sequence[str], members: Sequence[StructuredPytestMember]
) -> tuple[str, ...]:
    """Project trusted pytest options + admitted members without option smuggling."""

    prefix = tuple(command_prefix)
    admitted = tuple(members)
    if not prefix:
        raise ValueError("structured pytest projection requires a nonempty trusted command prefix")
    if not admitted:
        raise ValueError("structured pytest projection requires at least one admitted member")
    if "--" in prefix:
        raise ValueError("trusted pytest command prefix must not pre-terminate option parsing")
    selectors = tuple(_project_selector(member) for member in admitted)
    if len(selectors) != len(set(selectors)):
        raise ValueError("structured pytest members have duplicate executable projections")
    if len({member.projection_identity for member in admitted}) != len(admitted):
        raise ValueError("structured pytest members have duplicate projection identities")
    return (*prefix, "--", *selectors)


def _project_selector(member: StructuredPytestMember) -> str:
    """Render one admitted selector as filesystem data, never a pytest option."""

    selector = member.executable_selector
    if member.path.startswith("-"):
        return "./" + selector
    return selector
