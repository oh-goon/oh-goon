import os
import shutil
import tempfile
from pathlib import Path

from oh_goon.core.fleet_plan_tool_requirements import (
    SHELL_RUNTIME_SUPPORT_TOOLS,
    required_runtime_tool_names,
)
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.repository_tooling.goon_static_admission import canonical_step_required_tools

_ROOT = Path(__file__).resolve().parents[3]
_P0_AUTHORITY_TOOLS = canonical_step_required_tools(
    _ROOT,
    goon="oh-goon/backend-p0",
    step_id="make",
)
_P0_RUNTIME_TOOLS = tuple(sorted(set(_P0_AUTHORITY_TOOLS) | set(SHELL_RUNTIME_SUPPORT_TOOLS)))
_TOOLCHAIN_TEMPS: list[tempfile.TemporaryDirectory[str]] = []
_HOST_TOOL_PATH = os.environ.get("PATH", os.defpath)


def _host_tool(name: str) -> Path | None:
    resolved = shutil.which(name, path=_HOST_TOOL_PATH)
    return None if resolved is None else Path(resolved).resolve()


def _required_tool(name: str, *, owner: str) -> Path:
    resolved = _host_tool(name)
    if resolved is None:
        raise RuntimeError(f"oh-goon strict test fixtures require {owner} tool {name}")
    return resolved


def _test_path_authority(
    tool_names: tuple[str, ...], *, owner: str, require_all: bool
) -> StrictHostPathAuthority:
    runtime_inspector = _required_tool("ldd", owner=owner)
    temporary = tempfile.TemporaryDirectory(prefix="oh-goon-test-toolchain-")
    _TOOLCHAIN_TEMPS.append(temporary)
    toolchain_root = Path(temporary.name)
    for name in tuple(sorted(set(tool_names))):
        resolved = _host_tool(name)
        if resolved is None:
            if require_all:
                raise RuntimeError(f"oh-goon strict test fixtures require {owner} tool {name}")
            continue
        (toolchain_root / name).symlink_to(resolved)
    return StrictHostPathAuthority.create(
        trusted_roots=(toolchain_root, runtime_inspector.parent),
        tool_search_paths=(toolchain_root,),
        runtime_inspector=runtime_inspector,
    )


def test_path_authority_for_plan(plan: ResolvedFleetPlan) -> StrictHostPathAuthority:
    """Create exact test deployment authority for one already-resolved strict Plan."""

    return _test_path_authority(
        required_runtime_tool_names(plan),
        owner=f"resolved test Plan {plan.plan_id}",
        require_all=False,
    )


# Release-critical P0 tests that directly compose stores/fixtures consume the same exact
# host-tool authority admitted by the APP backend-p0 Goon. Plan-execution harnesses do
# not reuse this broad fixture: they derive their exact toolchain from the resolved Plan.
TEST_PATH_AUTHORITY = _test_path_authority(
    _P0_RUNTIME_TOOLS,
    owner="canonical backend-p0",
    require_all=True,
)

# Do not point test path authority at broad host directories such as /usr/bin. Doing so
# makes undeclared sibling executables accidentally visible in developer tests while the
# real strict certification toolchain correctly contains only frozen declared tools.
# Private symlink directories model Fleet's name-level tool admission without copying
# executable bytes or changing the runtime inspector owner.
