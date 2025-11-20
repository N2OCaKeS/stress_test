from __future__ import annotations

import os
from getpass import getpass
from pathlib import Path

from allta import ConfluencePublisher, PageBuilder

PREVIEW_FILE = Path("demo_confluence_report.html")
SAMPLE_ATTACHMENT = Path("sample_attachment.txt")
ENV_PATH = Path(".env")
_ENV_CACHE: dict[str, str] | None = None


def _load_env() -> dict[str, str]:
    """Парсит .env файл один раз за запуск скрипта."""

    global _ENV_CACHE
    if _ENV_CACHE is not None:
        return _ENV_CACHE
    env_map: dict[str, str] = {}
    if ENV_PATH.exists():
        for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if value.startswith(("'", '"')) and value.endswith(("'", '"')):
                value = value[1:-1]
            env_map[key] = value
    _ENV_CACHE = env_map
    return env_map


def _env_value(key: str) -> str | None:
    """Возвращает значение переменной окружения с учётом .env."""

    if key in os.environ:
        raw = os.environ[key]
        return raw if raw != "" else None
    env_map = _load_env()
    value = env_map.get(key)
    return value if value else None


def _as_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    return value not in {"0", "false", "no", "n"}


def _pull_confluence_settings() -> dict[str, str | bool | None]:
    """Берёт параметры публикации из .env (если они там заданы)."""

    return {
        "base_url": (_env_value("CONFLUENCE_BASE_URL") or "").strip() or None,
        "username": (_env_value("CONFLUENCE_USERNAME") or "").strip() or None,
        "space": (_env_value("CONFLUENCE_SPACE") or "").strip() or None,
        "title": (_env_value("CONFLUENCE_TITLE") or "").strip() or None,
        "parent_title": (_env_value("CONFLUENCE_PARENT_TITLE") or "").strip() or None,
        "use_token": _as_bool(_env_value("CONFLUENCE_USE_TOKEN")),
        "token": (_env_value("CONFLUENCE_TOKEN") or "").strip() or None,
        "password": (_env_value("CONFLUENCE_PASSWORD") or "").strip() or None,
    }


def _require_input(current: str | None, prompt_text: str) -> str:
    """Возвращает обязательное значение, при необходимости спрашивая пользователя."""

    value = (current or "").strip()
    while not value:
        value = input(prompt_text).strip()
    return value


def _optional_input(current: str | None, prompt_text: str) -> str | None:
    """Возвращает необязательное значение, разрешая пустой ввод."""

    if current is not None:
        return current
    user_value = input(prompt_text).strip()
    return user_value or None


def build_demo_page() -> tuple[PageBuilder, Path]:
    """
    Минимальный пример построения страницы средствами PageBuilder.

    Returns:
        tuple[PageBuilder, Path]: Экземпляр builder'а и созданный html-файл.
    """

    SAMPLE_ATTACHMENT.write_text("Hello from stress demo!\n", encoding="utf-8")

    builder = PageBuilder(title="PageBuilder Demo")
    builder.add_heading("Кратко", level=2)
    builder.add_paragraph(
        "Пример отчёта: таблица метрик, график, галерея и вложение.\n"
        "Каждый блок добавляется обычными python-вызовами."
    )
    builder.add_header_table(
        [
            # {"label": "Astra version", "value": "1.8.4(orel)"},
            # {"label": "Kernel", "value": "6.1.152-1-generic"},
            {
                "label": "Ranging",
                "value": {
                    "link": "https://life.astralinux.ru/pages/viewpage.action?pageId=150939635",
                    "link_text": "подробнее",
                    "items": [
                        {"label": "Процент ошибок", "value": 0.25},
                        {
                            "label": "Сред. время аутентификации и авторизации",
                            "value": 0.25,
                        },
                        {
                            "label": "Время задержки аутент. и авториз. почти последнего пользователя",
                            "value": 0.25,
                        },
                    ],
                },
            },
            {
                "label": "Test1",
                "value": {
                    # "link": "https://life.astralinux.ru/pages/viewpage.action?pageId=150939635",
                    # "link_text": "подробнее",
                    "items": [
                        {"label": "Процент ошибок", "value": 0.25},
                        {
                            "label": "Сред. время аутентификации и авторизации",
                            "value": 0.25,
                        },
                        {
                            "label": "Время задержки аутент. и авториз. почти последнего пользователя",
                            "value": 0.25,
                        },
                    ],
                },
            },
            {
                "label": "ARM",
                "value": {
                    "stand_number": "5",
                },
            },
            {
                "label": "Lead time",
                "value": {
                    "link": "https://life.astralinux.ru/pages/viewpage.action?pageId=150939635",
                    "link_text": "подробнее",
                    "items": [
                        {"label": "Процент ошибок", "value": 0.25},
                        {
                            "label": "Сред. время аутентификации и авторизации",
                            "value": 0.25,
                        },
                        {
                            "label": "Время задержки аутент. и авториз. почти последнего пользователя",
                            "value": 0.25,
                        },
                    ],
                },
            },
        ],
        defaults={"Lead time": "00:45:19"},
    )
    builder.add_table(
        {
            "title": "Быстрые факты",
            "headers": ["Метрика", "Значение"],
            "rows": [
                {"Метрика": "Тестов пройдено", "Значение": "42"},
                {"Метрика": "Средняя нагрузка, Гбит/с", "Значение": "8.2"},
            ],
        }
    )
    builder.add_chart(
        [
            {
                "title": "Line",
                "type": "line",
                "x_key": "T",
                "series": ["throughput"],
                "width": 800,
                "height": 360,
                "x_label": "Время",
                "y_label": "Пропускная способность",
                "data": [
                    {"T": "T+00", "throughput": 8.1},
                    {"T": "T+05", "throughput": 8.4},
                    {"T": "T+10", "throughput": 8.0},
                ],
            },
            {
                "title": "Area",
                "type": "area",
                "x_key": "T",
                "series": ["latency"],
                "width": 800,
                "height": 360,
                "x_label": "Время",
                "y_label": "Латентность (мс)",
                "data": [
                    {"T": "T+00", "latency": 210},
                    {"T": "T+05", "latency": 180},
                    {"T": "T+10", "latency": 220},
                ],
            },
            {
                "title": "Bar",
                "type": "bar",
                "x_key": "Неделя",
                "series": ["SLA"],
                "width": 800,
                "height": 360,
                "x_label": "Неделя",
                "y_label": "SLA (%)",
                "data": [
                    {"Неделя": "W1", "SLA": 99.2},
                    {"Неделя": "W2", "SLA": 99.4},
                    {"Неделя": "W3", "SLA": 99.6},
                ],
            },
            {
                "title": "Column",
                "type": "column",
                "x_key": "Неделя",
                "series": ["Errors"],
                "width": 800,
                "height": 360,
                "x_label": "Неделя",
                "y_label": "Ошибки (%)",
                "data": [
                    {"Неделя": "W1", "Errors": 0.8},
                    {"Неделя": "W2", "Errors": 0.6},
                    {"Неделя": "W3", "Errors": 0.4},
                ],
            },
            {
                "title": "Pie",
                "type": "pie",
                "x_key": "Категория",
                "series": ["value"],
                "width": 800,
                "height": 360,
                "data": [
                    {"Категория": "API", "value": 40},
                    {"Категория": "UI", "value": 35},
                    {"Категория": "DB", "value": 25},
                ],
            },
        ],
        columns=2,
    )
    builder.add_gallery(
        [
            {
                "title": "Тестовый стенд",
                "caption": "Внешний вид",
                "src": "libs/demo_media/Без названия21.png",
            },
            {
                "title": "Графики нагрузки",
                "caption": "CSV → Chart",
                "src": "libs/demo_media/Без названия21.png",
            },
        ],
        columns=2,
    )
    builder.add_attachment(
        SAMPLE_ATTACHMENT,
        description="Создаётся автоматически при запуске скрипта.",
    )

    builder.render_to_file(PREVIEW_FILE)
    return builder, PREVIEW_FILE


def publish_demo_page(builder: PageBuilder, preview_path: Path):
    """
    Минимальная публикация страницы через ConfluencePublisher.

    Args:
        builder: Готовый объект PageBuilder.
        preview_path: HTML-файл, прикладываемый к публикации.

    Returns:
        str: Идентификатор созданной/обновлённой страницы.
    """

    print("\n=== Публикация в Confluence ===")
    config = _pull_confluence_settings()
    base_url = _require_input(
        config.get("base_url"),
        "Confluence URL (например, https://wiki.example.org): ",
    )
    username = _require_input(config.get("username"), "Имя пользователя: ")
    space = _require_input(config.get("space"), "Пространство: ")
    title = config.get("title")
    if not title:
        title = input("Название страницы: ").strip() or "Demo Stress Report"
    # parent_title = _optional_input(
    #     config.get("parent_title"), "Родительская страница (можно пусто): "
    # )
    use_token_flag = config.get("use_token")
    if use_token_flag is None:
        use_token_flag = input("Использовать API token? [Y/n]: ").strip().lower() in {
            "",
            "y",
            "yes",
            "д",
            "да",
        }
    token = config.get("token")
    password = config.get("password")
    if use_token_flag:
        token = token or getpass("API token: ")
        password = None
    else:
        password = password or getpass("Пароль: ")
        token = None

    publisher = ConfluencePublisher(
        base_url=base_url,
        username=username,
        token=token,
        password=password,
    )
    attachments = [preview_path, *builder.attachments]
    page_id = publisher.publish(
        space=space,
        title=title,
        parent_title="",
        body=builder.render(),
        attachments=attachments,
        labels=["stress-demo"],
    )
    print(f"Страница опубликована: {page_id}")
    return page_id


if __name__ == "__main__":
    demo_builder, preview = build_demo_page()
    print(f"HTML сохранён в {preview.resolve()}")
    # choice = input("Отправить страницу в Confluence? [y/N]: ").strip().lower()
    # if choice in {"y", "yes", "д", "да"}:
    publish_demo_page(demo_builder, preview)
    # else:
    # print("Публикация пропущена. Файл можно загрузить вручную в Confluence.")
