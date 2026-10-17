"""Голосовые и аудио: транскрибация → диалоговый движок."""

from __future__ import annotations

import logging
from io import BytesIO

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.agent.queue import UserSessionGate
from jarvis.ai import AIProvider
from jarvis.bot.handlers.render import send_outcome
from jarvis.config.settings import Settings
from jarvis.conversation.engine import ConversationEngine
from jarvis.domain.enums import KnowledgeSource
from jarvis.transcription import TranscriptionError, TranscriptionProvider

log = logging.getLogger(__name__)


NO_TRANSCRIPTION_TEXT = (
    "🎤 Транскрибация не настроена, поэтому голосовые я пока не понимаю. "
    "Пришлите текст или настройте TRANSCRIPTION_PROVIDER."
)


async def voice_message(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    ai: AIProvider,
    transcription: TranscriptionProvider | None,
    agent_gate: UserSessionGate,
    search_provider=None,
) -> None:
    voice = message.voice
    if voice is None:
        return
    if (voice.file_size or 0) > settings.max_voice_bytes:
        await message.answer("Голосовое сообщение слишком большое для обработки.")
        return
    await _transcribe_and_handle(
        message=message,
        session=session,
        settings=settings,
        ai=ai,
        transcription=transcription,
        downloadable=voice,
        filename="voice.ogg",
        mime_type=voice.mime_type,
        agent_gate=agent_gate,
        search_provider=search_provider,
    )


async def audio_message(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    ai: AIProvider,
    transcription: TranscriptionProvider | None,
    agent_gate: UserSessionGate,
    search_provider=None,
) -> None:
    audio = message.audio
    if audio is None:
        return
    if (audio.file_size or 0) > settings.max_voice_bytes:
        await message.answer("Аудиофайл слишком большой для обработки.")
        return
    await _transcribe_and_handle(
        message=message,
        session=session,
        settings=settings,
        ai=ai,
        transcription=transcription,
        downloadable=audio,
        filename=audio.file_name or "audio.mp3",
        mime_type=audio.mime_type,
        agent_gate=agent_gate,
        search_provider=search_provider,
    )


async def _transcribe_and_handle(
    *,
    message: Message,
    session: AsyncSession,
    settings: Settings,
    ai: AIProvider,
    transcription: TranscriptionProvider | None,
    downloadable,
    filename: str,
    mime_type: str | None,
    agent_gate: UserSessionGate,
    search_provider=None,
) -> None:
    if transcription is None:
        await message.answer(NO_TRANSCRIPTION_TEXT)
        return

    buffer = BytesIO()
    try:
        await message.bot.download(downloadable, destination=buffer)
    except Exception:  # noqa: BLE001 - скачивание из Telegram может упасть
        log.exception("failed to download audio from telegram")
        await message.answer("Не удалось скачать аудио из Telegram. Попробуйте ещё раз.")
        return

    try:
        transcript = await transcription.transcribe(
            buffer.getvalue(), filename=filename, mime_type=mime_type
        )
    except TranscriptionError as error:
        log.warning("transcription failed: %s", error)
        await message.answer(f"🎤 Не получилось распознать: {error}. Пришлите текст.")
        return

    await message.answer(f"🎤 <i>Распознано:</i>\n{transcript}")
    from jarvis.logging_setup import get_correlation_id
    from jarvis.services.chatlog import ChatLogService

    await ChatLogService(session, settings).record(
        message.from_user.id,
        direction="in",
        text=transcript,
        source="voice",
        correlation_id=get_correlation_id(),
    )
    engine = ConversationEngine(
        session, settings, ai, gate=agent_gate, search_provider=search_provider
    )
    outcome = await engine.handle_text(
        message.from_user.id, transcript, source=KnowledgeSource.VOICE
    )
    await send_outcome(message, outcome)


def build_router() -> Router:
    router = Router(name="voice")
    router.message.register(voice_message, F.voice)
    router.message.register(audio_message, F.audio)
    return router
