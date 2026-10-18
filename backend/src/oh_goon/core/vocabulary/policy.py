from enum import StrEnum


class PolicySource(StrEnum):
    FLEET_POLICY = "FLEET_POLICY"
    UV_ENVIRONMENT = "UV_ENVIRONMENT"
    PROJECT = "PROJECT"
    BUILTIN_DEFAULT = "BUILTIN_DEFAULT"
    DERIVED = "DERIVED"
    AMBIENT_OR_DEFAULT = "AMBIENT_OR_DEFAULT"
    ABSENT = "ABSENT"


class WorkspaceMode(StrEnum):
    ROOT = "root"
    ALL = "all"


class WorkspaceScope(StrEnum):
    ROOT_ONLY = "ROOT_ONLY"
    ALL_PACKAGES = "ALL_PACKAGES"


class ResolutionStrategy(StrEnum):
    HIGHEST = "highest"
    LOWEST = "lowest"
    LOWEST_DIRECT = "lowest-direct"


class PrereleaseStrategy(StrEnum):
    DISALLOW = "disallow"
    ALLOW = "allow"
    IF_NECESSARY = "if-necessary"
    EXPLICIT = "explicit"
    IF_NECESSARY_OR_EXPLICIT = "if-necessary-or-explicit"


class IndexPolicy(StrEnum):
    PROJECT_CONFIGURED = "PROJECT_CONFIGURED"
    DEFAULT_OR_USER_CONFIG = "DEFAULT_OR_USER_CONFIG"
