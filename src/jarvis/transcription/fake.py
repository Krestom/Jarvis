"""Тестовый провайдер транскрибации."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeTranscription:
    name: str = "fake"
    transcripts: list[str] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    async def transcribe(
        self, audio: bytes, *, filename: str, mime_type: str | None = None
    ) -> str:
        self.calls.append(filename)
        if self.errors:
            raise self.errors.pop(0)
        if self.transcripts:
            return self.transcripts.pop(0)
        return "тестовая транскрибация"
