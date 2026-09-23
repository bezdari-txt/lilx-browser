"""Translations of the browser chrome (Qt widgets).

Internal pages have their own dictionary in ``resources/pages/assets/i18n.js``.
English strings are the keys; a missing translation falls back to English.
"""

from __future__ import annotations

from PySide6.QtCore import QLocale

LANGUAGES: dict[str, str] = {"en": "English", "ru": "Русский"}
DEFAULT_LANGUAGE = "en"

_RU: dict[str, str] = {
    "New tab": "Новая вкладка",
    "Close tab": "Закрыть вкладку",
    "Reopen closed tab": "Открыть закрытую вкладку",
    "Next tab": "Следующая вкладка",
    "Previous tab": "Предыдущая вкладка",
    "Focus address bar": "Перейти в адресную строку",
    "Back": "Назад",
    "Forward": "Вперёд",
    "Reload": "Обновить",
    "Stop": "Остановить",
    "Reload without cache": "Обновить без кэша",
    "Home": "Домой",
    "History": "История",
    "Downloads": "Загрузки",
    "Settings": "Настройки",
    "Zoom in": "Увеличить",
    "Zoom out": "Уменьшить",
    "Actual size": "Исходный размер",
    "Full screen": "Полноэкранный режим",
    "Quit lilx": "Выйти из lilx",
    "Tab {n}": "Вкладка {n}",
    "Last tab": "Последняя вкладка",
    "Menu": "Меню",
    "Search or enter address": "Поиск или адрес",
    "Downloading {name}": "Загрузка: {name}",
    "Page crashed — reload": "Страница упала — обновите",
    "lilBlock: {count} blocked on this page": "lilBlock: заблокировано на странице — {count}",
    "lilBlock is off": "lilBlock выключен",
    "lilBlock is off on {host}": "lilBlock выключен на {host}",
    "Block ads on {host}": "Блокировать рекламу на {host}",
    "Not available on this page": "Недоступно на этой странице",
    "Enable lilBlock": "Включить lilBlock",
    "lilBlock settings…": "Настройки lilBlock…",
    "Resetting lilx…": "Сброс lilx…",
    "Choose download folder": "Выберите папку для загрузок",
    "Bookmarks": "Закладки",
    "Extensions": "Расширения",
    "No extensions installed": "Расширения не установлены",
    "No enabled extensions": "Нет включённых расширений",
    "{name} has no popup": "У «{name}» нет всплывающего окна",
    "Manage extensions…": "Управление расширениями…",
    "Install extension from .zip": "Установить расширение из .zip",
    "Install unpacked extension (folder)": "Установить распакованное расширение (папку)",
    "Extension archive (*.zip)": "Архив расширения (*.zip)",
    "Bookmark this page": "Добавить страницу в закладки",
    "Remove bookmark": "Удалить из закладок",
    "No bookmarks yet": "Закладок пока нет",
    "Show bookmarks on the home page": "Закладки на главной странице",
    "This page cannot be bookmarked": "Эту страницу нельзя добавить в закладки",
    "Bookmark added": "Добавлено в закладки",
    "Bookmark removed": "Удалено из закладок",
}
_TABLES: dict[str, dict[str, str]] = {"ru": _RU}

_current = DEFAULT_LANGUAGE


def system_language() -> str:
    return "ru" if QLocale.system().language() == QLocale.Language.Russian else "en"


def resolve_language(setting: str) -> str:
    """Empty setting (first run) follows the system locale."""
    return setting if setting in LANGUAGES else system_language()


def set_language(code: str) -> None:
    global _current
    _current = code if code in LANGUAGES else DEFAULT_LANGUAGE


def current_language() -> str:
    return _current


def tr(text: str, **values: object) -> str:
    translated = _TABLES.get(_current, {}).get(text, text)
    return translated.format(**values) if values else translated
