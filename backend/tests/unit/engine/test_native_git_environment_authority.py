from __future__ import annotations

from oh_goon.engine.native_git_environment import capture_native_git_authority


def test_native_git_transport_authority_preserves_operator_configuration_opaquely() -> None:
    native = capture_native_git_authority(
        {
            "PATH": "/native/bin",
            "HOME": "/home/operator",
            "XDG_CONFIG_HOME": "/home/operator/.config",
            "SSH_AUTH_SOCK": "/run/user/1000/ssh-agent.socket",
            "GIT_CONFIG_GLOBAL": "/home/operator/.gitconfig.custom",
            "GIT_SSH_COMMAND": "ssh -F /home/operator/.ssh/config",
            "HTTPS_PROXY": "https://proxy.example.invalid",
            "SSL_CERT_FILE": "/etc/ssl/operator.pem",
            "FUTURE_GIT_CREDENTIAL_TOKEN": "credential-secret",
        }
    )

    environment = native.transport_environment()

    assert environment["HOME"] == "/home/operator"
    assert environment["XDG_CONFIG_HOME"] == "/home/operator/.config"
    assert environment["SSH_AUTH_SOCK"] == "/run/user/1000/ssh-agent.socket"
    assert environment["GIT_CONFIG_GLOBAL"] == "/home/operator/.gitconfig.custom"
    assert environment["GIT_SSH_COMMAND"] == "ssh -F /home/operator/.ssh/config"
    assert environment["HTTPS_PROXY"] == "https://proxy.example.invalid"
    assert environment["SSL_CERT_FILE"] == "/etc/ssl/operator.pem"
    assert environment["FUTURE_GIT_CREDENTIAL_TOKEN"] == "credential-secret"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert "credential-secret" not in repr(native)


def test_native_git_transport_drops_repository_state_and_template_redirection() -> None:
    native = capture_native_git_authority(
        {
            "HOME": "/home/operator",
            "GIT_DIR": "/tmp/foreign/.git",
            "GIT_WORK_TREE": "/tmp/foreign",
            "GIT_INDEX_FILE": "/tmp/foreign-index",
            "GIT_OBJECT_DIRECTORY": "/tmp/foreign-objects",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/alternate-objects",
            "GIT_COMMON_DIR": "/tmp/foreign-common",
            "GIT_NAMESPACE": "foreign",
            "GIT_TEMPLATE_DIR": "/tmp/native-template",
        }
    )

    environment = native.transport_environment()

    assert environment["HOME"] == "/home/operator"
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_TEMPLATE_DIR",
    ):
        assert name not in environment


def test_operator_git_noninteractive_choice_is_overridden_by_fleet_acquisition_policy() -> None:
    native = capture_native_git_authority({"GIT_TERMINAL_PROMPT": "1", "HOME": "/native"})

    assert native.transport_environment()["GIT_TERMINAL_PROMPT"] == "0"
