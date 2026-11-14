"""Stable application errors shared by every entry point."""

from __future__ import annotations

from pathlib import Path


class AppError(Exception):
    """An expected, auditable failure.

    ``code`` is part of the public contract.  ``path`` and ``line`` are best
    effort location hints; both are optional for failures that are not tied to
    one input row.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | Path | None = None,
        line: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = str(path) if path is not None else None
        self.line = line

    def __str__(self) -> str:
        location = ""
        if self.path:
            location = self.path
            if self.line is not None:
                location += f":{self.line}"
        if location:
            return f"{self.code}: {self.message} ({location})"
        if self.line is not None:
            return f"{self.code}: {self.message} (line {self.line})"
        return f"{self.code}: {self.message}"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation used by the HTTP layer."""

        data: dict[str, object] = {"code": self.code, "message": self.message}
        if self.path is not None:
            data["path"] = self.path
        if self.line is not None:
            data["line"] = self.line
        return data
