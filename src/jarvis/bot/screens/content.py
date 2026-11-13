"""Rich-экраны знаний, документов и секретов: карточки с кнопками в пузыре."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jarvis.bot.callbacks import (
    DocCB,
    DocListCB,
    KbCB,
    KbListCB,
    MenuCB,
    SecretCB,
    SecretListCB,
    uuid_hex,
)
from jarvis.bot.formatting import document_card_text, esc, knowledge_card_text, secret_card_text
from jarvis.bot.keyboards import (
    document_card_keyboard,
    documents_list_keyboard,
    knowledge_card_keyboard,
    knowledge_list_keyboard,
    secret_card_keyboard,
    secrets_list_keyboard,
)
from jarvis.bot.rich import RichScreen, button, row
from jarvis.conversation.preview import (
    DOCUMENT_TYPE_LABELS,
    KNOWLEDGE_TYPE_LABELS,
    SENSITIVITY_LABELS,
)
from jarvis.domain.enums import Sensitivity


def _label(value: object, limit: int = 64) -> str:
    """Однострочная подпись кнопки: без переносов, с многоточием на длинном."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _nav_row(prev_data: str | None, next_data: str | None, menu_label: str) -> str:
    nav = []
    if prev_data is not None:
        nav.append(button("⬅️", prev_data))
    nav.append(button(menu_label, MenuCB(action="main").pack(), style="link"))
    if next_data is not None:
        nav.append(button("➡️", next_data))
    return row(*nav)


def _sensitivity_label(value) -> str:
    return SENSITIVITY_LABELS.get(value, str(value))


# --- Знания ------------------------------------------------------------------


def knowledge_list_screen(items, page: int, has_next: bool) -> RichScreen:
    parts = ["<h3>📚 Знания</h3>"]
    if page > 0 or has_next:
        parts.append(f"<p><i>Страница {page + 1}</i></p>")
    if not items:
        parts.append("<p>Пусто. Напишите заметку — я предложу её сохранить.</p>")
    else:
        parts.append("<p>Последние записи. Выберите запись:</p>")
        for index, item in enumerate(items, start=1):
            parts.append(
                row(
                    button(
                        f"{index}. {_label(item.title)}",
                        KbCB(action="view", item_id=uuid_hex(item.id)).pack(),
                    )
                )
            )
    parts.append(
        _nav_row(
            KbListCB(page=page - 1).pack() if page > 0 else None,
            KbListCB(page=page + 1).pack() if has_next else None,
            "🏠 В меню",
        )
    )

    if items:
        text = "📚 <b>Знания</b> (последние записи)\n\n"
        text += "\n".join(
            f"{index}. {esc(item.title)}" for index, item in enumerate(items, start=1)
        )
        text += "\n\nВыберите запись:"
    else:
        text = "📚 Пока пусто. Напишите заметку — я предложу её сохранить."
    return RichScreen(
        html="".join(parts),
        text=text,
        markup=knowledge_list_keyboard(items, page, has_next),
    )


def knowledge_card_screen(item, *, full: bool = False) -> RichScreen:
    item_id = uuid_hex(item.id)
    type_label = KNOWLEDGE_TYPE_LABELS.get(item.type, str(item.type))
    parts = [
        "<h3>📚 Знание</h3>",
        f"<p><b>{esc(item.title)}</b></p>",
        f"<p><b>Тип:</b> {esc(type_label)}</p>",
    ]
    if item.sensitivity != Sensitivity.NORMAL:
        parts.append(
            f"<p><b>Чувствительность:</b> {esc(_sensitivity_label(item.sensitivity))}</p>"
        )
    if item.summary:
        parts.append(f"<blockquote>{esc(item.summary)}</blockquote>")
    if full:
        parts.append(f"<blockquote>{esc(item.content)}</blockquote>")
    else:
        preview = item.content if len(item.content) <= 400 else item.content[:399] + "…"
        if preview:
            parts.append(f"<blockquote>{esc(preview)}</blockquote>")

    toggle = (
        button("📕 Свернуть", KbCB(action="view", item_id=item_id).pack())
        if full
        else button(
            "📖 Полный текст",
            KbCB(action="full", item_id=item_id).pack(),
            style="primary",
        )
    )
    actions = row(toggle, button("🔎 Похожие", KbCB(action="similar", item_id=item_id).pack()))
    actions += row(
        button("🗑 Удалить", KbCB(action="delete", item_id=item_id).pack(), style="danger")
    )
    actions += row(button("📚 К списку", MenuCB(action="knowledge").pack(), style="link"))

    return RichScreen(
        html="".join(parts) + actions,
        text=knowledge_card_text(item, full=full) + "\n\nДействия:",
        markup=knowledge_card_keyboard(item_id, full=full),
    )


# --- Документы ---------------------------------------------------------------


def document_list_screen(documents, page: int, has_next: bool) -> RichScreen:
    parts = ["<h3>📄 Документы</h3>"]
    if page > 0 or has_next:
        parts.append(f"<p><i>Страница {page + 1}</i></p>")
    if not documents:
        parts.append(
            "<p>Пусто. Отправьте файл одним сообщением — я зашифрую его и предложу сохранить.</p>"
        )
    else:
        parts.append("<p>Файлы хранятся в зашифрованном виде. Выберите документ:</p>")
        for index, document in enumerate(documents, start=1):
            parts.append(
                row(
                    button(
                        f"{index}. {_label(document.title)}",
                        DocCB(action="view", doc_id=uuid_hex(document.id)).pack(),
                    )
                )
            )
    parts.append(
        _nav_row(
            DocListCB(page=page - 1).pack() if page > 0 else None,
            DocListCB(page=page + 1).pack() if has_next else None,
            "🏠 В меню",
        )
    )

    if documents:
        text = "📄 <b>Документы</b>\n\n"
        text += "\n".join(
            f"{index}. {esc(document.title)}"
            for index, document in enumerate(documents, start=1)
        )
        text += "\n\nВыберите документ:"
    else:
        text = (
            "📄 Пока нет документов.\n\nОтправьте файл одним сообщением — "
            "я зашифрую его и предложу сохранить."
        )
    return RichScreen(
        html="".join(parts),
        text=text,
        markup=documents_list_keyboard(documents, page, has_next),
    )


def document_card_screen(document, *, can_download: bool = True) -> RichScreen:
    doc_id = uuid_hex(document.id)
    type_label = DOCUMENT_TYPE_LABELS.get(
        document.document_type, str(document.document_type)
    )
    facts = [
        f"<b>Тип:</b> {esc(type_label)}",
        f"<b>Чувствительность:</b> {esc(_sensitivity_label(document.sensitivity))}",
        f"<b>Размер:</b> {document.size_bytes / 1024:.1f} КБ",
    ]
    if document.mime_type:
        facts.append(f"<b>Тип файла:</b> {esc(document.mime_type)}")
    parts = [
        "<h3>📄 Документ</h3>",
        f"<p><b>{esc(document.title)}</b></p>",
        "<p>" + "<br>".join(facts) + "</p>",
        "<p><i>Файл хранится в зашифрованном виде.</i></p>",
    ]

    actions = ""
    first_row = []
    if can_download:
        first_row.append(
            button(
                "📤 Получить файл",
                DocCB(action="download", doc_id=doc_id).pack(),
                style="primary",
            )
        )
    first_row.append(
        button("🗑 Удалить", DocCB(action="delete", doc_id=doc_id).pack(), style="danger")
    )
    actions += row(*first_row)
    actions += row(button("📄 К списку", MenuCB(action="documents").pack(), style="link"))

    return RichScreen(
        html="".join(parts) + actions,
        text=document_card_text(document) + "\n\nДействия:",
        markup=document_card_keyboard(doc_id, can_download=can_download),
    )


# --- Секреты -----------------------------------------------------------------


def secret_list_screen(secrets, page: int, has_next: bool) -> RichScreen:
    parts = ["<h3>🔐 Секреты</h3>"]
    if page > 0 or has_next:
        parts.append(f"<p><i>Страница {page + 1}</i></p>")
    parts.append(
        row(
            button(
                "➕ Новый секрет",
                MenuCB(action="secret_new").pack(),
                style="primary",
            )
        )
    )
    if not secrets:
        parts.append("<p>Пусто. Значения не показываются в списках и поиске.</p>")
    else:
        parts.append("<p>Значения не показываются в списках и поиске. Выберите секрет:</p>")
        for index, secret in enumerate(secrets, start=1):
            parts.append(
                row(
                    button(
                        f"{index}. {_label(secret.name)}",
                        SecretCB(action="view", secret_id=uuid_hex(secret.id)).pack(),
                    )
                )
            )
    parts.append(
        _nav_row(
            SecretListCB(page=page - 1).pack() if page > 0 else None,
            SecretListCB(page=page + 1).pack() if has_next else None,
            "🏠 В меню",
        )
    )

    if secrets:
        text = "🔐 <b>Секреты</b>\n\n"
        text += "\n".join(
            f"{index}. {esc(secret.name)}" for index, secret in enumerate(secrets, start=1)
        )
        text += (
            "\n\n<i>Значения не показываются в списках и поиске.</i>"
            "\n\nВыберите секрет:"
        )
    else:
        text = (
            "🔐 Секретов пока нет.\n\nКоманда /secret добавляет секрет: значение "
            "шифруется и не попадает в поиск, AI и MCP."
        )
    return RichScreen(
        html="".join(parts),
        text=text,
        markup=secrets_list_keyboard(secrets, page, has_next),
    )


def secret_card_screen(secret) -> RichScreen:
    secret_id = uuid_hex(secret.id)
    parts = [
        "<h3>🔐 Секрет</h3>",
        f"<p><b>{esc(secret.name)}</b></p>",
        f"<p><b>Тип:</b> {esc(secret.secret_type)}</p>",
    ]
    if secret.purpose:
        parts.append(f"<blockquote>{esc(secret.purpose)}</blockquote>")
    parts.append(
        "<p><i>Значение хранится в зашифрованном виде и не показывается в поиске.</i></p>"
    )

    actions = row(
        button(
            "👁 Показать значение",
            SecretCB(action="reveal", secret_id=secret_id).pack(),
            style="primary",
        ),
        button(
            "🗑 Удалить",
            SecretCB(action="delete", secret_id=secret_id).pack(),
            style="danger",
        ),
    )
    actions += row(button("🔐 К списку", MenuCB(action="secrets").pack(), style="link"))

    return RichScreen(
        html="".join(parts) + actions,
        text=secret_card_text(secret) + "\n\nДействия:",
        markup=secret_card_keyboard(secret_id),
    )


def secret_reveal_screen(secret, value: str) -> RichScreen:
    secret_id = uuid_hex(secret.id)
    note = (
        "Попробую удалить сообщение через 90 секунд. "
        "Если бот перезапустится — удалите его сами."
    )
    html = (
        "<h3>👁 Значение секрета</h3>"
        f"<p><b>{esc(secret.name)}</b></p>"
        f"<pre>{esc(value)}</pre>"
        f"<p><i>{note}</i></p>"
        + row(
            button(
                "🙈 Скрыть",
                SecretCB(action="view", secret_id=secret_id).pack(),
            )
        )
        + row(button("🔐 К списку", MenuCB(action="secrets").pack(), style="link"))
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🙈 Скрыть",
                    callback_data=SecretCB(action="view", secret_id=secret_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔐 К списку",
                    callback_data=MenuCB(action="secrets").pack(),
                )
            ],
        ]
    )
    text = (
        f"👁 <b>{esc(secret.name)}</b>\n<code>{esc(value)}</code>\n\n<i>{note}</i>"
    )
    return RichScreen(html=html, text=text, markup=markup)


__all__ = [
    "document_card_screen",
    "document_list_screen",
    "knowledge_card_screen",
    "knowledge_list_screen",
    "secret_card_screen",
    "secret_list_screen",
    "secret_reveal_screen",
]
