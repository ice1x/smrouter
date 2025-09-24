"""Custom exception types for the Genti platform."""


class FatalPipelineError(RuntimeError):
    """Raised when the pipeline hits a non-recoverable error.

    The orchestrator should stop scheduling further iterations as there is
    no reasonable expectation that automatic retries will succeed without
    external intervention (for example, misconfiguration or revoked access).
    """


__all__ = ["FatalPipelineError"]

