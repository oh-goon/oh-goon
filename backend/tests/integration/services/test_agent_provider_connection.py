"""Real HTTP requests through the CLI need no provider executable or provider SDK."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from oh_goon.cli import main


@pytest.mark.parametrize(
    ("api", "suffix", "reply"),
    [
        ("ollama", "/api/chat", {"done": True, "message": {"content": "hello"}}),
        (
            "openai-chat-completions",
            "/chat/completions",
            {"choices": [{"finish_reason": "stop", "message": {"content": "hello"}}]},
        ),
        (
            "openai-responses",
            "/responses",
            {
                "status": "completed",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "hello"}]}
                ],
            },
        ),
        (
            "anthropic-messages",
            "/messages",
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": "hello"}]},
        ),
    ],
)
def test_cli_connects_to_explicit_http_endpoint_without_provider_installation(
    tmp_path, monkeypatch, capsys, api, suffix, reply
):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            observed.append((self.path, dict(self.headers), json.loads(body)))
            payload = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever)
    worker.start()
    try:
        monkeypatch.setenv("PATH", "")
        monkeypatch.setenv("HTTP_PROXY", "http://unselected-proxy.invalid:9999")
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("PROVIDER_TEST_KEY", "private-test-key")
        config = tmp_path / "providers.toml"
        config.write_text(f'''version = 1
default_model = "review"
[providers.custom]
api = "{api}"
base_url = "http://127.0.0.1:{server.server_port}/prefix"
api_key_env = "PROVIDER_TEST_KEY"
[models.review]
provider = "custom"
model = "server/model:tag"
''')
        assert main(["agent", "providers", "validate", "--config", str(config)]) == 0
        capsys.readouterr()
        assert observed == []
        assert (
            main(
                [
                    "agent",
                    "providers",
                    "request",
                    "--config",
                    str(config),
                    "--prompt",
                    "explicit text",
                    "--json",
                ]
            )
            == 0
        )
        output = capsys.readouterr().out
        assert json.loads(output)["text"] == "hello"
        assert "private-test-key" not in output
        assert len(observed) == 1
        path, headers, body = observed[0]
        assert path == "/prefix" + suffix
        assert body["model"] == "server/model:tag"
        assert (body["input"] if api == "openai-responses" else body["messages"]) == [
            {"role": "user", "content": "explicit text"}
        ]
        credential = "x-api-key" if api == "anthropic-messages" else "Authorization"
        assert headers[credential] == (
            "private-test-key" if api == "anthropic-messages" else "Bearer private-test-key"
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
    assert not worker.is_alive()
