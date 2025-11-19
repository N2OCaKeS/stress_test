from __future__ import annotations

from allta import Libvirt, LibvirtManager, BaseDecorators, ConfluencePublisher, PageBuilder
from time import sleep
from pathlib import Path
from getpass import getpass
from random import uniform
from collections.abc import Iterable

DEFAULT_MEDIA_DIRECTORY = Path(__file__).with_name("demo_media")
vms_dates = {
    "testvm1": {
        "cpu": "4",
        "ram": "4096",
        "disk": "100",
        "additional_disks": {
            "disk1": {
                "size": "100",  # default 10 gb
                "mount_point": "/home/testuser2",  # default none, if default then not mount in vm
                "fs_type": "ext4",  # default ext4
            },
            "disk2": {
                "size": "100",  # default 10 gb
                "mount_point": "/home/testuser2",  # default none, if default then not mount in vm
                "fs_type": "ntfs",  # default ext4 FOR QCOW DISK
            },
            "disk3": {
                "device": "/dev/vdb",  # default none
                "fs_type": "ext4", # default none FOR BLOCK DISK if default then NOT format
                "mount_point": "/vms" # default none, if default then not mount in vm
            },
            "disk4": {
                "device": "/dev/vdc2",  # default none
            },
        },
    }
}
vms = list(vms_dates.keys())
box = "1.8.1.o"
rc = "1.8.1.6"


class Libvirt_test:
    def build():
        Libvirt.prepare()
        Libvirt.build(box=box, rc=rc, vms=vms, vms_dates=vms_dates, bridge=True)

    def build_old():
        old = Libvirt.build(box=box, rc=rc, vms=vms, vms_dates=vms_dates, bridge=False)
        LibvirtManager.Vm.bridge(
            vms_date=old, new_vms_date=vms_dates, username="u", password="1"
        )

    def check():
        Libvirt.check(vms=vms, vms_dates=vms_dates)

    def execute():
        example_task1 = {
            "testvm1": {
                "prepare_task": {
                    "command": "sudo apt-get install iperf -y",
                    "signal set": "1",
                }
            }
        }
        Libvirt.execute(commands=example_task1, vms_dates=vms_dates)

    def execute_no_wait():
        example_task1 = {
            "testvm1": {
                "prepare_task": {
                    "command": "sudo apt-get install linux-tools-$(uname -r) -y",
                    "signal set": "1",
                },
                "test_task": {
                    "command": "sudo perf record -g -a &",
                    "signal get": "1",
                    "nowait": True,  # default = False
                    "nowait_timeout": 3,  # default = 30 sec
                },
            }
        }
        Libvirt.execute(commands=example_task1, vms_dates=vms_dates)

    def scp():
        scp = {
            "testvm1": [
                {"mode": "push", "path_host": "test.py", "path_vm": "/home/test.py"}
            ]
        }
        Libvirt.scp(scp_settings=scp, vms_dates=vms_dates)


class LibvirtManager_test:
    @staticmethod
    def additional_disk():
        return LibvirtManager.Vm.additional_disk(
            vms_dates=vms_dates, disk_path="/home/u"
        )


class Decorators_test:
    @staticmethod
    @BaseDecorators.timer
    def timer():
        sleep(0.000234)


# Libvirt_test.build()
# LibvirtManager_test.additional_disk()


def generate_random_chart_data(points: int = 36) -> list[dict[str, float | int]]:
    """
    Возвращает список случайных значений для построения графика.

    Args:
        points (int): Количество точек на оси времени.

    Returns:
        list: Коллекция словарей с целочисленной колонкой ``Минута`` и
        значениями для серий ``test1``, ``test2`` и ``test3``.
    """

    test1 = 8.0
    test2 = 6.5
    test3 = 7.2
    data: list[dict[str, float | int]] = []
    for idx in range(points):
        test1 = max(5.0, min(11.0, test1 + uniform(-0.8, 0.9)))
        test2 = max(4.0, min(9.0, test2 + uniform(-0.7, 0.7)))
        test3 = max(5.0, min(10.5, test3 + uniform(-0.6, 0.8)))
        minute_mark = idx
        data.append(
            {
                "Минута": minute_mark,
                "test1": round(test1, 2),
                "test2": round(test2, 2),
                "test3": round(test3, 2),
            }
        )
    return data


def summarize_series(data: list[dict[str, float | int]], series: list[str]) -> list[dict[str, float]]:
    """Подсчитывает средние значения по указанным сериям."""

    summary: list[dict[str, float]] = []
    for name in series:
        values = [float(row.get(name, 0)) for row in data]
        if not values:
            continue
        summary.append({"Серия": name, "Среднее, Гбит/с": round(sum(values) / len(values), 2)})
    return summary


def build_demo_report_page(media_sources: Iterable[str | Path] | None = None):
    """
    Формирует демонстрационную страницу и сохраняет предпросмотр.

    Returns:
        tuple: Пара из ``PageBuilder`` и ``Path`` до html-файла.
    """

    builder = PageBuilder(title="OpenVPN Stress Demo")
    builder.add_heading("Сводка", level=2)
    builder.add_paragraph(
        "Эта страница показывает, как собрать отчёт из чистого Python без шаблонов.\n"
        "Все блоки описываются через методы builder'a."
    )
    builder.add_ordered_list(
        [
            "Подготовка стенда",
            "Запуск тестов",
            "Сбор результатов",
            "Публикация в Confluence",
        ]
    )

    builder.add_table(
        {
            "title": "Основные метрики",
            "headers": ["Метрика", "Значение", "Комментарий"],
            "rows": [
                {"Метрика": "Пропускная способность", "Значение": "8.2 Гбит/с", "Комментарий": "в пике"},
                {"Метрика": "Средняя задержка", "Значение": "4.3 мс", "Комментарий": "95-й перцентиль"},
                {"Метрика": "Ошибки", "Значение": "0", "Комментарий": "не обнаружено"},
            ],
            "description": "Значения берутся из логов testsuite.",
        }
    )

    builder.add_heading("Графики", level=2)
    builder.add_paragraph(
        "Данные генерируются при каждом запуске и визуализируются встроенным "
        "макросом Confluence Chart."
    )
    chart_series = ["test1", "test2", "test3"]
    random_data = generate_random_chart_data()
    builder.add_chart(
        {
            "title": "Стресс-тест: временные ряды rows",
            "type": "line",
            "x_key": "Минута",
            "series": chart_series,
            "width": 960,
            "height": 420,
            "x_label": "Минуты теста",
            "x_unit": "мин",
            "y_label": "Пропускная способность",
            "y_unit": "Гбит/с",
            "data": random_data,
            "time_series": False,
            "series_orientation": "rows",
            "series_label": "Серия",
            "colors": ["#0052CC", "#36B37E", "#FF5630"],
        }
    )

    builder.add_chart(
        {
            "title": "Стресс-тест: временные ряды columns",
            "type": "line",
            "x_key": "Минута",
            "series": chart_series,
            "width": 960,
            "height": 420,
            "x_label": "Минуты теста",
            "x_unit": "мин",
            "y_label": "Пропускная способность",
            "y_unit": "Гбит/с",
            "data": random_data,
            "time_series": False,
            "series_orientation": "columns",
            "series_label": "Серия",
            "colors": ["#0052CC", "#36B37E", "#FF5630"],
        }
    )    

    builder.add_chart(
        {
            "title": "Стресс-тест: усреднённые показатели",
            "type": "column",
            "x_key": "Серия",
            "series": ["Среднее, Гбит/с"],
            "width": 720,
            "height": 360,
            "y_label": "Средняя пропускная способность",
            "y_unit": "Гбит/с",
            "data": summarize_series(random_data, chart_series),
            "colors": ["#0052CC", "#36B37E", "#FF5630"],
        }
    )

    media_files = collect_media_files(media_sources)
    if media_files:
        add_media_section(
            builder,
            media_files,
            section_title="Фото испытательного стенда и вложения",
            description=(
                "Скрипт автоматически прикладывает найденные файлы и фотографии. "
                "Они будут загружены вместе со страницей и отображены ниже."
            ),
        )

    preview_path = Path("demo_confluence_report.html")
    builder.render_to_file(preview_path)
    return builder, preview_path


def publish_demo_report(
    *,
    base_url,
    username,
    space,
    title,
    token=None,
    password=None,
    parent_title=None,
    builder=None,
    preview_path=None,
    attachments: Iterable[str | Path] | None = None,
    display_attachments: bool = True,    
):
    """
    Публикует демонстрационную страницу в указанное пространство Confluence.

    Args:
        base_url (str): URL инстанса Confluence.
        username (str): Пользователь от чьего имени идёт публикация.
        space (str): Пространство Confluence.
        title (str): Название новой страницы.
        token (str, optional): API token, если используется.
        password (str, optional): Пароль пользователя.
        parent_title (str, optional): Родительская страница.
        builder (PageBuilder, optional): Готовый объект страницы.
        preview_path (Path, optional): Файл предпросмотра, который нужно прикрепить.

    Returns:
        str: Идентификатор созданной/обновлённой страницы.
    """

    if builder is None or preview_path is None:
        builder, preview_path = build_demo_report_page()

    extra_media = collect_media_files(attachments)
    if extra_media:
        if display_attachments:
            add_media_section(
                builder,
                extra_media,
                section_title="Дополнительные вложения",
                description="Файлы, добавленные во время публикации.",
            )
        else:
            register_media_attachments(builder, extra_media, display=False)        

    publisher = ConfluencePublisher(
        base_url=base_url,
        username=username,
        password=password,
        token=token,
    )
    page_id = publisher.publish(
        space=space,
        title=title,
        parent_title=parent_title,
        body=builder.render(),
        attachments=prepare_attachment_payload(preview_path, builder.attachments),
        labels=["stress-report", "demo"],
    )
    return page_id


def interactive_publish_demo_report(builder, preview_path):
    """
    Запускает интерактивный мастер публикации тестовой страницы.

    Args:
        builder (PageBuilder): Объект страницы, подготовленный заранее.
        preview_path (Path): HTML-файл для предпросмотра и вложения.
    """

    print("\n=== Интерактивная публикация демо-страницы Confluence ===")
    answer = input("Создать тестовую страницу в Confluence? [y/N]: ").strip().lower()
    if answer not in {"y", "yes", "д", "да"}:
        print("Публикация пропущена по запросу пользователя.")
        return

    base_url = input("Введите базовый URL Confluence (например, https://wiki.example.org): ").strip()
    username = input("Введите имя пользователя: ").strip()
    space = input("Укажите пространство: ").strip()
    title = input("Название новой страницы: ").strip() or "Demo Stress Report"
    parent_title = input("Родительская страница (можно оставить пустым): ").strip() or None

    auth_choice = input("Использовать API token? [Y/n]: ").strip().lower()
    token = password = None
    if auth_choice in {"", "y", "yes", "д", "да"}:
        token = getpass("API token: ")
    else:
        password = getpass("Пароль пользователя: ")

    if not base_url or not username or not space:
        print("Недостаточно данных для публикации. Проверьте ввод и повторите попытку.")
        return

    try:
        page_id = publish_demo_report(
            base_url=base_url,
            username=username,
            token=token,
            password=password,
            space=space,
            title=title,
            parent_title=parent_title,
            builder=builder,
            preview_path=preview_path,
        )
    except Exception as error:
        print(f"Ошибка публикации: {error}")
        return

    print(f"Страница успешно опубликована. ID: {page_id}")
    print(
        "Проверьте Confluence, чтобы убедиться, что вложения и содержимое отображаются корректно."
    )


def collect_media_files(sources: Iterable[str | Path] | None) -> list[Path]:
    """
    Собирает список файлов для вложения в отчёт.

    Args:
        sources: Список путей к файлам или каталогам. Если ``None`` и существует
            каталог ``demo_media`` рядом со скриптом, то файлы берутся оттуда.

    Returns:
        list[Path]: Отфильтрованные пути до файлов.
    """

    resolved_sources: list[Path] = []
    if sources is None:
        if DEFAULT_MEDIA_DIRECTORY.exists():
            resolved_sources = [DEFAULT_MEDIA_DIRECTORY]
    elif isinstance(sources, (str, Path)):
        resolved_sources = [Path(sources)]
    else:
        resolved_sources = [Path(item) for item in sources]

    attachments: list[Path] = []
    seen: set[Path] = set()
    for source in resolved_sources:
        path = Path(source).expanduser()
        if not path.exists():
            continue
        candidates = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            attachments.append(resolved)
    return attachments


def register_media_attachments(
    builder: PageBuilder, attachments: Iterable[Path], *, display: bool = True
):
    """Добавляет файлы в список вложений builder."""

    for path in attachments:
        if display:
            builder.add_attachment(
                path,
                title=build_media_title(path),
                display=True,
            )
        else:
            builder.add_attachment(path, display=False)


def add_media_section(
    builder: PageBuilder,
    attachments: Iterable[Path],
    *,
    section_title: str,
    description: str | None = None,
):
    """Формирует визуальный блок с вложениями."""

    attachments = list(attachments)
    if not attachments:
        return
    builder.add_heading(section_title, level=2)
    if description:
        builder.add_paragraph(description)
    register_media_attachments(builder, attachments, display=True)


def build_media_title(path: Path) -> str:
    """Возвращает человекочитаемый заголовок для блока вложения."""

    if path.suffix.lower() in PageBuilder.IMAGE_EXTENSIONS:
        candidate = path.stem
    else:
        candidate = path.name
    title = candidate.replace("_", " ").strip()
    return title or path.name


def prepare_attachment_payload(preview_path: Path, registered: Iterable[Path]) -> list[Path]:
    """Формирует итоговый список файлов для загрузки в Confluence."""

    candidates = [preview_path, *registered]
    payload: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        path = Path(candidate)
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        payload.append(path)
    return payload

if __name__ == "__main__":
    demo_builder, preview_file = build_demo_report_page()
    # interactive_publish_demo_report(*build_demo_report_page())
    publish_demo_report(
        base_url="https://life.astralinux.ru",
        username="mfilippenko",
        token="",
        space="~mfilippenko",
        title="Demo Stress Report from allta lib",
        parent_title=None,
        builder=demo_builder,
        preview_path=preview_file,
    )
