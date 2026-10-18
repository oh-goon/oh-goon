from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture import (
    frontend_base_contract as frontend_base,
)
from oh_goon.repository_tooling.architecture._structural_authority_frontend import (
    _check_browser_persistence_boundary,
    _check_frontend_transport_ownership,
)
from oh_goon.repository_tooling.architecture.external_development_tooling_contract import (
    _runtime_file_leaks,
)


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _local_only(root: Path, monkeypatch) -> list[str]:
    source = root / "frontend/src"
    monkeypatch.setattr(frontend_base, "FRONTEND_SOURCE_ROOT", source)
    return [item.message for item in frontend_base.check_frontend_local_only()]


def test_frontend_comments_and_strings_do_not_create_transport_or_storage_authority(
    tmp_path: Path, monkeypatch
) -> None:
    _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "// import { request } from '@/app/api/httpTransport'\n"
        "// localStorage; fetch('/api'); https://example.test\n"
        "const documentation = \"localStorage fetch('/api')\";\n",
    )

    assert _check_frontend_transport_ownership(tmp_path) == []
    assert _check_browser_persistence_boundary(tmp_path) == []
    assert _local_only(tmp_path, monkeypatch) == []


def test_transport_import_alias_is_still_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "import { request as send } from '@/app/api/httpTransport';\n"
        "export const load = () => send('/api/runs');\n",
    )

    violations = _check_frontend_transport_ownership(tmp_path)
    assert any(
        item.path == path and "HTTP transport is infrastructure-only" in item.message
        for item in violations
    )


def test_transport_reexport_is_still_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "export { request as send } from '@/app/api/httpTransport';\n",
    )

    violations = _check_frontend_transport_ownership(tmp_path)
    assert any(
        item.path == path and "HTTP transport is infrastructure-only" in item.message
        for item in violations
    )


def test_mutation_transport_import_alias_is_still_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "import { writeOperation as mutate } from '@/app/api/mutationTransport';\n",
    )

    violations = _check_frontend_transport_ownership(tmp_path)
    assert any(
        item.path == path and "writeOperation is owned only" in item.message
        for item in violations
    )


def test_create_api_client_import_alias_is_still_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "import { createApiClient as buildClient } from '@/app/createApiClient';\n",
    )

    violations = _check_frontend_transport_ownership(tmp_path)
    assert any(
        item.path == path and "createApiClient is owned only" in item.message
        for item in violations
    )


def test_computed_browser_storage_access_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    path = _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "const browser = globalThis;\n"
        "const storageName = 'local' + 'Storage';\n"
        "const store = browser[storageName];\n"
        "store.setItem('run', 'x');\n",
    )

    structural = _check_browser_persistence_boundary(tmp_path)
    local_only = _local_only(tmp_path, monkeypatch)
    assert any(
        item.path == path and "must not gain browser persistence" in item.message
        for item in structural
    )
    assert "localStorage is owned only by app/preferences.ts" in local_only


def test_computed_session_storage_access_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "const browser = window;\n"
        "const store = browser['session' + 'Storage'];\n"
        "store.getItem('run');\n",
    )

    assert any(
        "must not gain browser persistence" in item.message
        for item in _check_browser_persistence_boundary(tmp_path)
    )
    assert "unsupported browser persistence/telemetry: sessionStorage" in _local_only(
        tmp_path, monkeypatch
    )


def test_computed_document_cookie_access_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "const documentAlias = document;\n"
        "const cookieName = 'coo' + 'kie';\n"
        "documentAlias[cookieName] = 'a=b';\n",
    )

    assert "unsupported browser persistence/telemetry: document.cookie" in _local_only(
        tmp_path, monkeypatch
    )


def test_fetch_alias_through_computed_global_access_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "const browser = globalThis;\n"
        "const networkName = 'fe' + 'tch';\n"
        "const request = browser[networkName];\n"
        "export async function load() { return request('/api/runs'); }\n",
    )

    assert "raw fetch is owned only by app/api/httpTransport.ts" in _local_only(
        tmp_path, monkeypatch
    )


def test_split_external_url_literal_is_rejected(tmp_path: Path, monkeypatch) -> None:
    _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "const remote = 'https:' + '//example.test/api';\n",
    )

    assert "frontend source must not contain an external URL" in _local_only(
        tmp_path, monkeypatch
    )


def test_allowed_storage_owner_accepts_computed_local_storage(
    tmp_path: Path, monkeypatch
) -> None:
    _write(
        tmp_path,
        "frontend/src/app/preferences.ts",
        "const browser = globalThis;\n"
        "const key = 'local' + 'Storage';\n"
        "const store = browser[key];\n"
        "store.getItem('theme');\n",
    )

    assert _check_browser_persistence_boundary(tmp_path) == []
    assert _local_only(tmp_path, monkeypatch) == []


def test_allowed_http_transport_accepts_computed_fetch_alias(
    tmp_path: Path, monkeypatch
) -> None:
    _write(
        tmp_path,
        "frontend/src/app/api/httpTransport.ts",
        "const browser = globalThis;\n"
        "const key = 'fe' + 'tch';\n"
        "const request = browser[key];\n"
        "export const send = (...args: Parameters<typeof fetch>) => request(...args);\n",
    )

    assert _local_only(tmp_path, monkeypatch) == []


def test_type_only_fetch_reference_does_not_create_network_authority(
    tmp_path: Path, monkeypatch
) -> None:
    _write(
        tmp_path,
        "frontend/src/features/status.ts",
        "export type RequestArguments = Parameters<typeof fetch>;\n",
    )

    assert _local_only(tmp_path, monkeypatch) == []


def test_external_developer_tool_comment_and_string_are_representation_only() -> None:
    text = (
        "// import { inspect } from 'agentsCookbook';\n"
        "const example = \"require('agent-economics')\";\n"
    )
    assert _runtime_file_leaks(Path("feature.ts"), "frontend", text) is False


def test_external_developer_tool_reexport_remains_rejected() -> None:
    text = "export { inspect as review } from 'agentsCookbook/runtime';\n"
    assert _runtime_file_leaks(Path("feature.ts"), "frontend", text) is True
