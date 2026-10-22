"""Synthetic native Codex credentials and ephemeral TLS material for local probes."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class CodexAuthFixture:
    token: str
    account_id: str | None
    certificate: Path
    key: Path
    ca: Path


def prepare_codex_auth(home: Path, *, authentication: str) -> CodexAuthFixture:
    """Write only synthetic login data, retaining native account and TLS validation."""
    if authentication not in {"api-key", "chatgpt"}:
        raise ValueError(f"unsupported synthetic authentication: {authentication}")
    home.mkdir(exist_ok=True)
    account_id: str | None = None
    if authentication == "api-key":
        token = "synthetic-codex-context-key"
        credentials: dict[str, object] = {"OPENAI_API_KEY": token}
    else:
        account_id = "synthetic-account"
        now = datetime.now(UTC)
        claims = {
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "email": "synthetic@example.invalid",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": account_id,
                "chatgpt_user_id": "synthetic-user",
                "chatgpt_plan_type": "pro",
            },
        }

        def encode(value: object) -> str:
            return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

        token = f"{encode({'alg': 'none', 'typ': 'JWT'})}.{encode(claims)}.synthetic"
        credentials = {
            "auth_mode": "chatgpt",
            "tokens": {
                "id_token": token,
                "access_token": token,
                "refresh_token": "synthetic-refresh-token",
                "account_id": account_id,
            },
            "last_refresh": now.isoformat(),
        }
    (home / "auth.json").write_text(json.dumps(credentials), encoding="utf-8")

    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("selected native Codex qualification requires OpenSSL")
    tls = home.parent / "synthetic-tls"
    tls.mkdir()
    ca = tls / "ca.pem"
    ca_key = tls / "ca-key.pem"
    key = tls / "server-key.pem"
    certificate = tls / "server.pem"
    request = tls / "server.csr"
    extensions = tls / "server.ext"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n",
        encoding="utf-8",
    )

    def run(*arguments: str) -> None:
        subprocess.run(
            [openssl, *arguments],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=15,
        )

    run(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-keyout",
        str(ca_key),
        "-out",
        str(ca),
        "-subj",
        "/CN=OH-Goon Synthetic Test CA",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    run(
        "req",
        "-new",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(request),
        "-subj",
        "/CN=localhost",
    )
    run(
        "x509",
        "-req",
        "-in",
        str(request),
        "-CA",
        str(ca),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(certificate),
        "-days",
        "1",
        "-extfile",
        str(extensions),
    )
    return CodexAuthFixture(token, account_id, certificate, key, ca)
