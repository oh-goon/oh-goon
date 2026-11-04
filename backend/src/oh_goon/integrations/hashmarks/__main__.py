"""Run the existing supervised repository-evidence tool through its public API."""

from oh_goon.integrations.hashmarks import agent_evidence_main


def main() -> int:
    return agent_evidence_main()


if __name__ == "__main__":
    raise SystemExit(main())
