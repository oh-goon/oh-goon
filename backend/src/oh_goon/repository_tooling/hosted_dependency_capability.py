"""Validate compatible host dependencies for non-authoritative hosted diagnostics.

This boundary never installs, resolves, or mutates dependency state.  It exists so
constrained environments can distinguish a compatible package already installed in
the selected host interpreter from an artifact that native uv would need in order
to materialize the authoritative project ``.venv``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[4]
PYPROJECT = ROOT / "backend/pyproject.toml"
SCHEMA = "oh-goon.hosted-dependency-capability/v2"
# Only these packages are execution prerequisites for the dependency-light hosted
# diagnostic itself. Every declared Python dependency is nevertheless discovered
# from pyproject.toml and reported below; this tuple is not a package inventory.
REQUIRED_HOST_CAPABILITIES = ("cryptography", "pytest")
_REPOSITORY_IMPORT_SHADOW_ROOTS = (ROOT / "backend" / "src", ROOT / "scripts")
_STREAM_CHUNK_BYTES = 1024 * 1024


def _sha256_file(path: Path) -> str:
    """Diagnostic-bootstrap digest; must not import the product package."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_STREAM_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


_IMPORT_NAME_OVERRIDES = {
    # Distribution/import spelling exceptions. Ordinary names are derived from
    # installed distribution metadata or PEP-style ``-`` -> ``_`` normalization.
    "pyyaml": "yaml",
    "pytest-cov": "pytest_cov",
}
_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
_EXTRAS_RE = re.compile(
    r"\[\s*[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
    r"(?:\s*,\s*[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)*\s*\]"
)
_RELEASE_RE = re.compile(r"^(\d+(?:\.\d+){1,3})$")
VersionReader = Callable[[str], str]
ImportProbe = Callable[[str, str], None]


class HostedCapabilityError(RuntimeError):
    pass


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _requirement_name(requirement: str) -> str:
    match = _NAME_RE.match(requirement.strip())
    if match is None:
        raise HostedCapabilityError(f"cannot normalize dependency requirement: {requirement!r}")
    return _normalized_name(match.group(1))


def _minimum(requirement: str) -> tuple[int, ...]:
    normalized = requirement.strip()
    name_match = _NAME_RE.match(normalized)
    if name_match is None:
        raise HostedCapabilityError(f"cannot parse dependency requirement: {requirement!r}")
    spec = normalized[name_match.end() :].strip()
    extras = _EXTRAS_RE.match(spec)
    if extras is not None:
        spec = spec[extras.end() :].strip()
    match = re.fullmatch(r">=\s*(\d+(?:\.\d+){1,3})", spec)
    if match is None:
        raise HostedCapabilityError(
            "hosted capability requires the repository minimum-compatible >= policy, "
            f"got {requirement!r}"
        )
    return tuple(int(part) for part in match.group(1).split("."))


def _release(version: str) -> tuple[int, ...]:
    match = _RELEASE_RE.fullmatch(version.strip())
    if match is None:
        raise HostedCapabilityError(
            f"hosted capability accepts only final numeric releases; observed {version!r}"
        )
    return tuple(int(part) for part in match.group(1).split("."))


def _python_range(metadata: dict[str, object]) -> str:
    project = metadata.get("project")
    if not isinstance(project, dict):
        raise HostedCapabilityError("backend/pyproject.toml is missing [project]")
    value = project.get("requires-python")
    if not isinstance(value, str):
        raise HostedCapabilityError("backend/pyproject.toml is missing project.requires-python")
    return value


def _python_supported(version: str, spec: str) -> bool:
    match = re.fullmatch(
        r">=\s*(\d+(?:\.\d+){1,2})\s*,\s*<\s*(\d+(?:\.\d+){1,2})",
        spec.strip(),
    )
    if match is None:
        raise HostedCapabilityError(
            f"hosted capability requires project.requires-python in >=MIN,<MAX form, got {spec!r}"
        )
    observed = _release(version)
    minimum = tuple(int(part) for part in match.group(1).split("."))
    maximum = tuple(int(part) for part in match.group(2).split("."))
    return minimum <= observed < maximum


def _native_only_dependencies(payload: dict[str, object]) -> frozenset[str]:
    tool = payload.get("tool")
    oh_goon = tool.get("oh-goon") if isinstance(tool, dict) else None
    hosted = oh_goon.get("hosted-diagnostic") if isinstance(oh_goon, dict) else None
    values = hosted.get("native-only-dependencies") if isinstance(hosted, dict) else None
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value.strip() for value in values
    ):
        raise HostedCapabilityError(
            "backend/pyproject.toml must define [tool.oh-goon.hosted-diagnostic] "
            "native-only-dependencies as a non-empty string list"
        )
    normalized = tuple(_normalized_name(value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise HostedCapabilityError("hosted diagnostic native-only dependencies contain duplicates")
    return frozenset(normalized)


def _project_contract(
    pyproject: Path,
) -> tuple[dict[str, str], dict[str, str], str, frozenset[str]]:
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    native_only = _native_only_dependencies(payload)
    project = payload.get("project")
    rows: list[str] = [
        str(value)
        for value in (project.get("dependencies", []) if isinstance(project, dict) else [])
    ]
    for values in payload.get("dependency-groups", {}).values():
        rows.extend(str(value) for value in values)
    declared: dict[str, str] = {}
    for requirement in rows:
        name = _requirement_name(requirement)
        if name in declared:
            raise HostedCapabilityError(f"duplicate hosted dependency requirement for {name}")
        declared[name] = requirement
    undeclared_native_only = sorted(native_only - set(declared))
    if undeclared_native_only:
        raise HostedCapabilityError(
            "hosted diagnostic native-only dependencies are not declared dependencies: "
            + ", ".join(undeclared_native_only)
        )
    evaluated: dict[str, str] = {}
    for name, requirement in declared.items():
        if name in native_only:
            continue
        _minimum(requirement)
        evaluated[name] = requirement
    missing = sorted(set(REQUIRED_HOST_CAPABILITIES) - set(evaluated))
    if missing:
        raise HostedCapabilityError(
            "backend/pyproject.toml is missing hosted capability requirements: "
            + ", ".join(missing)
        )
    return declared, evaluated, _python_range(payload), native_only


def _import_name(distribution: str) -> str:
    # Import spelling is deterministic repository policy, not an observation of
    # whichever ambient environment happens to execute the diagnostic. Most
    # distributions follow PEP normalization; only true spelling exceptions live
    # in the small mapping above.
    return _IMPORT_NAME_OVERRIDES.get(distribution, distribution.replace("-", "_"))


def _default_import_probe(distribution: str, import_name: str) -> None:
    if distribution == "types-pyyaml":
        # This PEP 561 distribution contains type data rather than an importable
        # runtime module. Native package metadata owns its installed location.
        stubs = importlib.metadata.distribution(distribution).locate_file("yaml-stubs/__init__.pyi")
        if not stubs.is_file():
            raise HostedCapabilityError("PyYAML type stubs are not materialized")
        return
    module = importlib.import_module(import_name)
    origin = getattr(module, "__file__", None)
    if origin is not None:
        resolved_origin = Path(origin).resolve()
        for source_root in _REPOSITORY_IMPORT_SHADOW_ROOTS:
            try:
                resolved_origin.relative_to(source_root.resolve())
            except ValueError:
                continue
            raise HostedCapabilityError(
                f"host dependency {distribution} is shadowed by repository source: {origin}"
            )
    if distribution == "cryptography":
        module_version = getattr(module, "__version__", None)
        metadata_version = importlib.metadata.version("cryptography")
        if module_version != metadata_version:
            raise HostedCapabilityError(
                "cryptography import metadata mismatch: "
                f"module={module_version!r} distribution={metadata_version!r}"
            )
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.x509.oid import NameOID

        key = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)
        message = b"oh-goon-hosted-cryptography-capability"
        signature = key.sign(message)
        public_key = key.public_key()
        public_key.verify(signature, message)

        public_pem = public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        loaded_public_key = serialization.load_pem_public_key(public_pem)
        loaded_public_key.verify(signature, message)

        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "oh-goon-host-capability")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(public_key)
            .serial_number(1)
            .not_valid_before(datetime(2020, 1, 1, tzinfo=UTC))
            .not_valid_after(datetime(2030, 1, 1, tzinfo=UTC))
            .sign(key, None)
        )
        certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
        loaded_certificates = x509.load_pem_x509_certificates(certificate_pem)
        if len(loaded_certificates) != 1 or loaded_certificates[0].serial_number != 1:
            raise HostedCapabilityError(
                "cryptography X.509 capability probe returned unexpected data"
            )


def _canonical_identity(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _package_capability(
    *,
    distribution: str,
    import_name: str,
    requirement: str,
    version_reader: VersionReader,
    import_probe: ImportProbe,
) -> tuple[dict[str, object], str | None]:
    row: dict[str, object] = {
        "distribution": distribution,
        "import": import_name,
        "requirement": requirement,
    }
    try:
        observed = version_reader(distribution)
    except importlib.metadata.PackageNotFoundError:
        row.update(status="missing", observed_version=None)
        blocker = f"{distribution}: missing" if distribution in REQUIRED_HOST_CAPABILITIES else None
        return row, blocker
    row["observed_version"] = observed
    try:
        compatible = _release(observed) >= _minimum(requirement)
    except HostedCapabilityError as exc:
        row.update(status="incompatible", detail=str(exc))
        return row, f"{distribution}: {exc}"
    if not compatible:
        row.update(status="incompatible")
        return row, f"{distribution}: {observed} does not satisfy {requirement}"
    try:
        import_probe(distribution, import_name)
    except Exception as exc:  # diagnostic boundary must classify import/ABI failures
        detail = f"{type(exc).__name__}: {exc}"
        row.update(status="import-broken", detail=detail)
        return row, f"{distribution}: import probe failed: {detail}"
    row["status"] = "compatible"
    return row, None


def build_capability(
    *,
    pyproject: Path = PYPROJECT,
    version_reader: VersionReader = importlib.metadata.version,
    import_probe: ImportProbe = _default_import_probe,
    interpreter: str | None = None,
    python_version: str | None = None,
) -> dict[str, object]:
    declared_requirements, requirements, requires_python, native_only = _project_contract(pyproject)
    packages: list[dict[str, object]] = []
    blockers: list[str] = []
    observed_python = python_version or platform.python_version()
    try:
        python_ready = _python_supported(observed_python, requires_python)
    except HostedCapabilityError as exc:
        python_ready = False
        blockers.append(f"python: {exc}")
    else:
        if not python_ready:
            blockers.append(f"python: {observed_python} does not satisfy {requires_python}")
    for distribution, requirement in requirements.items():
        import_name = _import_name(distribution)
        row, blocker = _package_capability(
            distribution=distribution,
            import_name=import_name,
            requirement=requirement,
            version_reader=version_reader,
            import_probe=import_probe,
        )
        packages.append(row)
        if blocker is not None:
            blockers.append(blocker)

    base: dict[str, object] = {
        "schema": SCHEMA,
        "authority": "diagnostic-only",
        "native_authority": False,
        "dependency_complete_authority": False,
        "uv_artifact_cache_evaluated": False,
        "result": "HOST-CAPABILITY-READY" if not blockers else "HOST-CAPABILITY-BLOCKED",
        "interpreter": interpreter or str(Path(sys.executable).resolve()),
        "python_version": observed_python,
        "requires_python": requires_python,
        "python_status": "compatible" if python_ready else "incompatible",
        "dependency_intent_sha256": "sha256:" + _sha256_file(pyproject),
        "required_host_capabilities": list(REQUIRED_HOST_CAPABILITIES),
        "declared_python_dependencies": list(declared_requirements),
        "evaluated_python_dependencies": list(requirements),
        "excluded_python_dependencies": sorted(native_only),
        "packages": packages,
        "blockers": blockers,
    }
    base["identity"] = _canonical_identity(base)
    return base


def required_host_capability_blockers(
    *,
    pyproject: Path = PYPROJECT,
    version_reader: VersionReader = importlib.metadata.version,
    import_probe: ImportProbe = _default_import_probe,
) -> tuple[str, ...]:
    """Return blockers for the minimal dependency-light host interpreter boundary.

    ``REQUIRED_HOST_CAPABILITIES`` is the single semantic owner of what the
    interpreter selector needs.  The selector delegates here instead of spelling
    package imports in shell.  Full hosted diagnostics still use
    :func:`build_capability` to evaluate the wider dependency-light environment.
    """

    _declared, requirements, _requires_python, _native_only = _project_contract(pyproject)
    blockers: list[str] = []
    for distribution in REQUIRED_HOST_CAPABILITIES:
        requirement = requirements[distribution]
        _row, blocker = _package_capability(
            distribution=distribution,
            import_name=_import_name(distribution),
            requirement=requirement,
            version_reader=version_reader,
            import_probe=import_probe,
        )
        if blocker is not None:
            blockers.append(blocker)
    return tuple(blockers)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--probe-required-host-capabilities", action="store_true")
    return parser.parse_args()


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    args = _parse_args()
    if getattr(args, "probe_required_host_capabilities", False):
        blockers = required_host_capability_blockers()
        if blockers:
            for blocker in blockers:
                print(f"- {blocker}", file=sys.stderr)
            return 3
        return 0

    output = getattr(args, "output", None)
    if not isinstance(output, Path):
        raise HostedCapabilityError("hosted capability output path is required")
    payload = build_capability()
    _write_atomic(output, payload)
    print("Host dependency capability:")
    for row in payload["packages"]:
        assert isinstance(row, dict)
        observed = row.get("observed_version") or "MISSING"
        print(
            f"  {row['distribution']}: {observed} -> {row['status']} "
            f"(requires {row['requirement']})"
        )
    print(f"HOST_DEPENDENCY_CAPABILITY_IDENTITY={payload['identity']}")
    print("Native uv artifact cache: NOT EVALUATED (separate native authority)")
    if payload["result"] != "HOST-CAPABILITY-READY":
        print(
            "HOSTED DEVELOPMENT DIAGNOSTIC BLOCKED: incompatible host dependency capability",
            file=sys.stderr,
        )
        for blocker in payload["blockers"]:
            print(f"- {blocker}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
