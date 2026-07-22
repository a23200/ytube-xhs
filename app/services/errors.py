from typing import Any, Dict, Optional


class PipelineError(Exception):
    """Structured error raised by pipeline steps."""

    def __init__(
        self,
        code: str,
        message: str,
        step: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.step = step
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        from app.services.error_diagnostics import diagnose_error

        return diagnose_error({
            "code": self.code,
            "message": self.message,
            "step": self.step,
            "details": self.details,
        })
