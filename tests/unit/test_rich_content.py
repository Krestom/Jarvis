"""Rich-экраны знаний, документов и секретов."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

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
from jarvis.bot.screens.content import (
    document_card_screen,
    document_list_screen,
    knowledge_card_screen,
    knowledge_list_screen,
    secret_card_screen,
    secret_list_screen,
    secret_reveal_screen,
)
from jarvis.domain.enums import DocumentType, KnowledgeType, SecretType, Sensitivity


def _knowledge(
    title: str = "Бэкап сервера",
    *,
    content: str = "Ежедневно в 3:00.",
    summary: str | None = None,
    sensitivity=Sensitivity.NORMAL,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=title,
        summary=summary,
        content=content,
        type=KnowledgeType.INSTRUCTION,
        sensitivity=sensitivity,
    )


def _document(
    title: str = "Паспорт",
    *,
    sensitivity=Sensitivity.SENSITIVE,
    mime_type: str | None = "application/pdf",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=title,
        document_type=DocumentType.PASSPORT,
        sensitivity=sensitivity,
        size_bytes=2048,
        mime_type=mime_type,
    )


def _secret(name: str = "API сервиса", *, purpose: str | None = "Доступ к API"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        secret_type=SecretType.API_TOKEN,
        purpose=purpose,
    )


def _primary_count(html: str) -> int:
    return html.count('style="primary"')


# --- Знания ------------------------------------------------------------------


def test_knowledge_list_has_item_buttons_and_fallback():
    items = [_knowledge("Первая"), _knowledge("Вторая")]
    screen = knowledge_list_screen(items, page=0, has_next=True)

    assert KbCB(action="view", item_id=uuid_hex(items[0].id)).pack() in screen.html
    assert KbCB(action="view", item_id=uuid_hex(items[1].id)).pack() in screen.html
    assert KbListCB(page=1).pack() in screen.html
    assert "1. Первая" in screen.html
    assert "Выберите запись:" in screen.text
    assert screen.markup is not None


def test_knowledge_list_page_two_has_back():
    screen = knowledge_list_screen([_knowledge()], page=1, has_next=False)
    assert "Страница 2" in screen.html
    assert KbListCB(page=0).pack() in screen.html


def test_knowledge_list_escapes_title():
    screen = knowledge_list_screen([_knowledge("<b>опасно</b>")], page=0, has_next=False)
    assert "<b>опасно</b>" not in screen.html
    assert "&lt;b&gt;опасно&lt;/b&gt;" in screen.html


def test_knowledge_list_empty_has_no_item_buttons():
    screen = knowledge_list_screen([], page=0, has_next=False)
    assert "Пусто." in screen.html
    assert "kb:view" not in screen.html
    assert MenuCB(action="main").pack() in screen.html
    assert screen.markup is not None


def test_knowledge_card_actions_and_styles():
    item = _knowledge()
    screen = knowledge_card_screen(item)

    assert KbCB(action="full", item_id=uuid_hex(item.id)).pack() in screen.html
    assert KbCB(action="similar", item_id=uuid_hex(item.id)).pack() in screen.html
    assert KbCB(action="delete", item_id=uuid_hex(item.id)).pack() in screen.html
    assert MenuCB(action="knowledge").pack() in screen.html
    assert 'style="danger"' in screen.html
    assert _primary_count(screen.html) == 1
    assert screen.markup is not None


def test_knowledge_card_full_text_and_sensitivity():
    item = _knowledge(
        "Инструкция",
        content="Полный текст инструкции",
        summary="Коротко",
        sensitivity=Sensitivity.SENSITIVE,
    )
    screen = knowledge_card_screen(item, full=True)

    assert KbCB(action="view", item_id=uuid_hex(item.id)).pack() in screen.html
    assert KbCB(action="full", item_id=uuid_hex(item.id)).pack() not in screen.html
    assert "Полный текст инструкции" in screen.html
    assert "Коротко" in screen.html
    assert "чувствительная" in screen.html
    assert screen.text


def test_knowledge_card_escapes_fields():
    item = _knowledge("<i>заголовок</i>", content="<script>x</script>", summary="a & b")
    screen = knowledge_card_screen(item, full=True)

    assert "<i>заголовок</i>" not in screen.html
    assert "&lt;i&gt;заголовок&lt;/i&gt;" in screen.html
    assert "<script>" not in screen.html
    assert "&lt;script&gt;" in screen.html
    assert "a &amp; b" in screen.html


# --- Документы ---------------------------------------------------------------


def test_document_list_has_item_buttons_and_fallback():
    documents = [_document("Паспорт"), _document("Договор")]
    screen = document_list_screen(documents, page=0, has_next=False)

    assert DocCB(action="view", doc_id=uuid_hex(documents[0].id)).pack() in screen.html
    assert DocCB(action="view", doc_id=uuid_hex(documents[1].id)).pack() in screen.html
    assert "1. Паспорт" in screen.html
    assert "Выберите документ:" in screen.text
    assert screen.markup is not None


def test_document_list_pagination_and_empty():
    screen = document_list_screen([_document()], page=1, has_next=True)
    assert DocListCB(page=0).pack() in screen.html
    assert DocListCB(page=2).pack() in screen.html

    empty = document_list_screen([], page=0, has_next=False)
    assert "Пусто." in empty.html
    assert "doc:view" not in empty.html
    assert MenuCB(action="main").pack() in empty.html


def test_document_list_escapes_title():
    screen = document_list_screen([_document("<b>файл</b>")], page=0, has_next=False)
    assert "<b>файл</b>" not in screen.html
    assert "&lt;b&gt;файл&lt;/b&gt;" in screen.html


def test_document_card_actions_and_facts():
    document = _document()
    screen = document_card_screen(document)

    assert DocCB(action="download", doc_id=uuid_hex(document.id)).pack() in screen.html
    assert DocCB(action="delete", doc_id=uuid_hex(document.id)).pack() in screen.html
    assert MenuCB(action="documents").pack() in screen.html
    assert "паспорт" in screen.html
    assert "2.0 КБ" in screen.html
    assert "application/pdf" in screen.html
    assert _primary_count(screen.html) == 1
    assert screen.markup is not None


def test_document_card_without_download_has_no_download_button():
    screen = document_card_screen(_document(), can_download=False)
    assert "dc:download" not in screen.html
    assert MenuCB(action="documents").pack() in screen.html


# --- Секреты -----------------------------------------------------------------


def test_secret_list_has_new_button_and_items():
    secrets = [_secret("Первый"), _secret("Второй")]
    screen = secret_list_screen(secrets, page=0, has_next=True)

    assert MenuCB(action="secret_new").pack() in screen.html
    assert SecretCB(action="view", secret_id=uuid_hex(secrets[0].id)).pack() in screen.html
    assert SecretCB(action="view", secret_id=uuid_hex(secrets[1].id)).pack() in screen.html
    assert SecretListCB(page=1).pack() in screen.html
    assert _primary_count(screen.html) == 1
    assert "Значения не показываются" in screen.html
    assert screen.markup is not None


def test_secret_list_empty_still_offers_new_secret():
    screen = secret_list_screen([], page=0, has_next=False)
    assert "Пусто." in screen.html
    assert MenuCB(action="secret_new").pack() in screen.html
    assert "sc:view" not in screen.html
    assert screen.markup is not None


def test_secret_list_escapes_name():
    screen = secret_list_screen([_secret("<b>токен</b>")], page=0, has_next=False)
    assert "<b>токен</b>" not in screen.html
    assert "&lt;b&gt;токен&lt;/b&gt;" in screen.html


def test_secret_card_reveal_and_delete():
    secret = _secret()
    screen = secret_card_screen(secret)

    assert SecretCB(action="reveal", secret_id=uuid_hex(secret.id)).pack() in screen.html
    assert SecretCB(action="delete", secret_id=uuid_hex(secret.id)).pack() in screen.html
    assert MenuCB(action="secrets").pack() in screen.html
    assert "Доступ к API" in screen.html
    assert _primary_count(screen.html) == 1
    assert screen.markup is not None


def test_secret_reveal_shows_value_and_hide_button():
    secret = _secret()
    screen = secret_reveal_screen(secret, "<b>bad</b> & </code>")

    assert "<pre>&lt;b&gt;bad&lt;/b&gt; &amp; &lt;/code&gt;</pre>" in screen.html
    assert SecretCB(action="view", secret_id=uuid_hex(secret.id)).pack() in screen.html
    assert MenuCB(action="secrets").pack() in screen.html
    assert "copy_text" not in screen.html
    assert "&lt;b&gt;bad&lt;/b&gt; &amp; &lt;/code&gt;" in screen.text
    assert screen.markup is not None
    hide = screen.markup.inline_keyboard[0][0]
    assert hide.text == "🙈 Скрыть"
    assert hide.callback_data == SecretCB(
        action="view", secret_id=uuid_hex(secret.id)
    ).pack()
