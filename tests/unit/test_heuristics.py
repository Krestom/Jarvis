from jarvis.conversation.heuristics import (
    extract_search_query,
    first_line,
    is_question,
    looks_like_search,
)


def test_looks_like_search() -> None:
    assert looks_like_search("найди инструкцию по бэкапам")
    assert looks_like_search("Поиск серверов")
    assert looks_like_search("что я знаю о бэкапах")
    assert not looks_like_search("У меня появилась задача")
    assert not looks_like_search("надо найти что-то")


def test_extract_search_query() -> None:
    assert extract_search_query("найди резервное копирование") == "резервное копирование"
    assert extract_search_query("Найди, что я решал по архитектуре") == "что я решал по архитектуре"
    assert extract_search_query("поиск: подключение к серверу") == "подключение к серверу"
    assert extract_search_query("что я знаю о бэкапах") == "бэкапах"
    # если после маркера ничего нет — запрос остаётся исходным
    assert extract_search_query("найди") == "найди"


def test_is_question() -> None:
    assert is_question("как дела?")
    assert not is_question("дела")


def test_first_line() -> None:
    assert first_line("первая строка\nвторая") == "первая строка"
    assert first_line("") == ""
    assert first_line("x" * 100).endswith("…")
