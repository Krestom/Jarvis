"""Конвертер markdown→HTML для текстов LLM и наличие правил разметки в промптах."""

from __future__ import annotations

from jarvis.agent.prompts import SYSTEM_HEADER
from jarvis.ai.prompts import (
    SYSTEM_PROMPT,
    TELEGRAM_FORMAT_RULES,
    format_llm_text,
    normalize_llm_layout,
    normalize_llm_markdown,
)


def test_paired_bold_is_converted():
    assert normalize_llm_markdown("Это **важно** здесь") == "Это <b>важно</b> здесь"


def test_multiple_pairs_are_converted():
    assert normalize_llm_markdown("**Раз** и **два**") == "<b>Раз</b> и <b>два</b>"


def test_plain_text_unchanged():
    text = "Обычный ответ без разметки. 2 * 3 = 6"
    assert normalize_llm_markdown(text) == text


def test_unbalanced_asterisks_untouched():
    assert normalize_llm_markdown("одна * звёздочка") == "одна * звёздочка"
    assert normalize_llm_markdown("**не закрыто") == "**не закрыто"
    assert normalize_llm_markdown("закрыто** без начала") == "закрыто** без начала"


def test_single_asterisk_italic_not_converted():
    assert normalize_llm_markdown("это *курсив* — не трогаем") == "это *курсив* — не трогаем"


def test_cross_line_bold_not_converted():
    text = "**начало\nконец**"
    assert normalize_llm_markdown(text) == text


def test_empty_and_existing_html():
    assert normalize_llm_markdown("") == ""
    assert normalize_llm_markdown("<b>уже HTML</b>") == "<b>уже HTML</b>"


def test_format_rules_present_in_prompts():
    assert TELEGRAM_FORMAT_RULES in SYSTEM_PROMPT
    assert TELEGRAM_FORMAT_RULES in SYSTEM_HEADER
    assert "<b>" in TELEGRAM_FORMAT_RULES
    assert "Markdown запрещён" in TELEGRAM_FORMAT_RULES


# --- Раскладка (normalize_llm_layout) ----------------------------------------


def test_glued_bullets_are_split():
    text = "Итог: полезнее.• Меньше задержек. Быстрее.• Только forward secrecy."
    assert normalize_llm_layout(text) == (
        "Итог: полезнее.\n\n"
        "<blockquote>• Меньше задержек. Быстрее.\n\n• Только forward secrecy.</blockquote>"
    )


def test_bullet_run_gets_blank_lines_and_quote():
    text = "Ключевые изменения:\n• Первый пункт\n• Второй пункт"
    result = normalize_llm_layout(text)
    assert result == (
        "Ключевые изменения:\n\n<blockquote>• Первый пункт\n\n• Второй пункт</blockquote>"
    )


def test_single_bullet_is_not_quoted():
    text = "Внимание:\n• Один пункт"
    assert normalize_llm_layout(text) == "Внимание:\n\n• Один пункт"


def test_long_bullet_run_uses_expandable_quote():
    items = "\n".join(f"• пункт {index}" for index in range(1, 8))
    result = normalize_llm_layout(f"Список:\n{items}")
    assert result.count("<blockquote expandable>") == 1
    assert "</blockquote>" in result


def test_bullets_inside_existing_quote_are_not_nested():
    text = "<blockquote>• один\n\n• два</blockquote>"
    result = normalize_llm_layout(text)
    assert "<blockquote>• один" in result
    assert result.count("<blockquote") == 1


def test_single_bullet_quote_from_model_is_unwrapped_and_rewrapped():
    text = "<blockquote>• Первый</blockquote>\n• Второй"
    result = normalize_llm_layout(text)
    assert result == "<blockquote>• Первый\n\n• Второй</blockquote>"


def test_glued_sentences_become_paragraphs():
    text = "Итог сделан.Практический вывод: обновиться стоит.Источники уточнены."
    result = normalize_llm_layout(text)
    assert result == (
        "Итог сделан.\n\nПрактический вывод: обновиться стоит.\n\nИсточники уточнены."
    )


def test_no_extra_blank_lines_and_trailing_spaces():
    text = "Абзац один.  \n\n\n\nАбзац два."
    assert normalize_llm_layout(text) == "Абзац один.\n\nАбзац два."


def test_layout_idempotent_for_clean_text():
    text = "Заголовок\n\n<blockquote>• один\n\n• два</blockquote>\n\nИтог."
    assert normalize_llm_layout(normalize_llm_layout(text)) == normalize_llm_layout(text)


def test_format_llm_text_combines_bold_and_layout():
    text = "**Важно**: вывод готов.Далее детали."
    assert format_llm_text(text) == "<b>Важно</b>: вывод готов.\n\nДалее детали."


def test_blank_line_after_quote_before_text():
    text = "Список:\n• один\n• два\nИтог."
    result = normalize_llm_layout(text)
    assert result.endswith("</blockquote>\n\nИтог.")


def test_br_tags_become_newlines_and_quotes():
    text = (
        "Вот чем полезен TLS 1.3.<br><br>"
        "• <b>Скорость</b>: рукопожатие сократилось.<br><br>"
        "• <b>Безопасность</b>: старые шифры вырезаны.<br><br>"
        "Источники:<br><br>"
        "• https://example.com"
    )
    result = format_llm_text(text)
    assert "<br" not in result.lower()
    assert "<blockquote>• <b>Скорость</b>: рукопожатие сократилось." in result
    assert "</blockquote>\n\nИсточники:" in result


def test_single_br_is_line_break():
    assert normalize_llm_layout("Строка один.<br>Строка два.") == (
        "Строка один.\nСтрока два."
    )


def test_sources_heading_starts_new_paragraph():
    text = (
        "Варианты: • Первый рецепт. • Второй рецепт. Обязательно дренаж. "
        "Источники: • https://a.dom • https://b.dom"
    )
    result = normalize_llm_layout(text)
    assert "\n\nИсточники:\n\n<blockquote>• https://a.dom" in result
    assert result.count("<blockquote>") == 2


def test_sources_heading_at_line_start_untouched():
    text = "Итог.\n\nИсточники:\n\n• https://a.dom"
    assert normalize_llm_layout(text) == text


def test_long_prose_wall_gets_paragraphs():
    sentence = (
        "Спатифиллуму нужен рыхлый грунт, который хорошо пропускает воду "
        "и воздух к корням растения."
    )
    text = " ".join([sentence] * 6)
    result = normalize_llm_layout(text)
    assert "\n\n" in result
    assert result.replace("\n\n", " ") == text  # контент не потерян и не искажён


def test_short_prose_not_split():
    text = "Короткий ответ про грунт. И ещё одно предложение."
    assert normalize_llm_layout(text) == text


def test_format_rules_mention_json_newlines():
    assert r"\n" in TELEGRAM_FORMAT_RULES
    assert "списки не оборачивай" in TELEGRAM_FORMAT_RULES.lower()
    assert "<br>" in TELEGRAM_FORMAT_RULES
    assert "сплошным абзацем" in TELEGRAM_FORMAT_RULES
    assert "Источники" in TELEGRAM_FORMAT_RULES
