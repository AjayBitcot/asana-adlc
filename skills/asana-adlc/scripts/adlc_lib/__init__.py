"""Deterministic core of the asana-adlc skill.

Everything safety-critical lives here rather than in prose: the run record,
approval gates, the command guard, isolated workspaces, verified cleanup, and
the Asana / Git provider clients with their duplicate guards. The agent decides
*what* to do; these modules refuse to do it unsafely.
"""


class AdlcError(Exception):
    """A failure the agent must report to the user."""

    exit_code = 1


class ConfigError(AdlcError):
    """Missing or invalid configuration or credentials."""

    exit_code = 2


class GateError(AdlcError):
    """The action needs an approval that is missing, or was rejected."""

    exit_code = 3


class BlockedError(AdlcError):
    """The workflow cannot continue without a human decision."""

    exit_code = 4


class ExistingRunError(AdlcError):
    """A run for this task already exists; the user must pick a recovery option."""

    exit_code = 5


class OutcomeUnknown(AdlcError):
    """A remote write may or may not have happened; re-read before retrying."""

    exit_code = 75
