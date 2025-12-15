"""
Утилиты для публикации готовых отчётов в Confluence.

Основной класс :class:`ConfluencePublisher` инкапсулирует клиент
``atlassian.Confluence`` и предоставляет высокоуровневые методы для
создания страниц, обновления содержимого и загрузки вложений.
"""

from pathlib import Path
from typing import Iterable, Sequence, Any, List, Dict, cast, TYPE_CHECKING

if TYPE_CHECKING:  # for type hints only
    from .page_builder import PageBuilder

from atlassian import Confluence


class ConfluencePublisher:
    """
    Публикует HTML-страницы и файлы вложений в Confluence.

    Класс создаёт либо обновляет страницу, прикрепляет указанные файлы и
    задаёт метки. Таким образом тестам достаточно предоставить готовое
    тело страницы, не заботясь о низкоуровневых вызовах REST API.
    """

    def __init__(
        self,
        *,
        base_url,
        username,
        password=None,
        token=None,
    ):
        """
        Создаёт подключение к Confluence.

        Args:
            base_url (str): Базовый URL экземпляра Confluence.
            username (str): Имя пользователя.
            password (str | None): Пароль пользователя.
            token (str | None): API token, если используется.

        Raises:
            ValueError: Если не указаны пароль и токен одновременно.
        """

        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        auth_kwargs = {"url": base_url, "username": username}
        if password:
            auth_kwargs["password"] = password
        elif token:
            auth_kwargs["token"] = token
        else:
            raise ValueError("Either password or token must be provided")
        self._client = Confluence(**auth_kwargs)

    # ------------------------------------------------------------------
    def publish(
        self,
        *,
        space,
        title,
        body,
        parent_title=None,
        attachments=None,
        labels=None,
    ):
        """
        Создаёт или обновляет страницу и при необходимости загружает вложения.

        Args:
            space (str): Пространство Confluence.
            title (str): Название страницы.
            body (str): Тело страницы в формате Storage.
            parent_title (str | None): Родительская страница, если нужна иерархия.
            attachments (Sequence[str | Path] | None): Пути к файлам для прикрепления.
            labels (Sequence[str] | None): Метки страницы.

        Returns:
            str: Идентификатор созданной или обновлённой страницы.
        """

        page_id = self._ensure_page(
            space=space,
            title=title,
            parent_title=parent_title,
            body=body,
        )
        if attachments:
            self.attach_files(page_id=page_id, files=attachments)
        if labels:
            self._ensure_labels(page_id=page_id, labels=labels)
        return page_id

    def _log(self, message: str) -> None:
        """
        Простая обёртка над print для единообразного логирования.
        """

        print(f"[ConfluencePublisher] {message}")

    # ------------------------------------------------------------------
    def attach_files(self, *, page_id, files: Iterable[Path | str]):
        """
        Прикрепляет файлы к существующей странице.

        Args:
            page_id (str): Идентификатор страницы.
            files (Sequence[str | Path]): Коллекция путей до файлов.
        """

        for file_path in files:
            path = Path(file_path)
            if not path.exists() or not path.is_file():
                self._log(f"[skip] Вложение не найдено: {path}")
                continue
            self._log(f"Прикрепляю файл {path.name} к странице {page_id}")
            self._client.attach_file(page_id=page_id, filename=str(path))

    def _ensure_labels(self, *, page_id, labels: Sequence[str]):
        """
        Обновляет метки страницы, если они были переданы.

        Args:
            page_id (str): Идентификатор страницы.
            labels (Sequence[str]): Список меток.
        """

        if not labels:
            return
        payload: List[Dict[str, str]] = [
            {"prefix": "global", "name": label} for label in labels if label
        ]
        if not payload:
            return

        set_labels = getattr(self._client, "set_page_labels", None)
        if callable(set_labels):
            set_labels(page_id, payload)
            return

        set_label = getattr(self._client, "set_page_label", None)
        if callable(set_label):
            for label in payload:
                set_label(page_id, label["name"])
            return

        self._client.post(
            f"rest/api/content/{page_id}/label", 
            json=cast(Any, payload),  # Confluence API принимает список объектов label
        )

    def _ensure_page(self, *, space, title, parent_title, body):
        """
        Создаёт страницу или обновляет существующую.

        Args:
            space (str): Пространство Confluence.
            title (str): Название страницы.
            parent_title (str | None): Родительская страница.
            body (str): HTML в формате Storage.

        Returns:
            str: Идентификатор страницы.
        """

        if self._client.page_exists(space=space, title=title):
            page_id = self._client.get_page_id(space=space, title=title)
            self._log(f"Обновляю страницу '{title}' ({page_id})")
            self._client.update_page(page_id=page_id, title=title, body=body)
            if not page_id:
                raise RuntimeError(f"Не удалось получить id страницы '{title}' для обновления")
            return page_id

        parent_id = (
            self._client.get_page_id(space=space, title=parent_title)
            if parent_title
            else None
        )
        self._log(
            f"Создаю страницу '{title}' в пространстве '{space}'"
            f"{f' под родителем {parent_title}' if parent_title else ''}"
        )
        result = self._client.create_page(
            space=space,
            title=title,
            body=body,
            parent_id=parent_id,
            type="page",
            representation="storage",
            editor="v2",
        )
        page_id = (
            result["id"]
            if isinstance(result, dict) and result.get("id")
            else self._client.get_page_id(space=space, title=title)
        )
        if not page_id:
            raise RuntimeError(f"Не удалось создать страницу '{title}' в пространстве '{space}'")
        return page_id

    # ------------------------------------------------------------------
    def publish_results_from_params(
        self,
        *,
        conf_space: str,
        conf_parent_page: str,
        conf_new_page_name: str,
        test_cycle_version: str | None,
        body: "PageBuilder",
        attachments_dir: Path | str | None = None,
        attachments: Sequence[str | Path] | None = None,
    ) -> Dict[str, str | None]:
        """
        Публикует результат теста, принимая тот же набор параметров,
        что и ``Public`` в старой логике.

        Args:
            conf_space: Пространство Confluence.
            conf_parent_page: Родительская страница (``c_pp``).
            conf_new_page_name: Итоговая страница (``c_np``).
            test_cycle_version: Значение ``-tcv`` для вычисления релизной страницы.
            body: HTML отчёта или экземпляр PageBuilder.
            attachments_dir: Каталог с артефактами отчёта.
            attachments: Дополнительные файлы.

        Returns:
            dict: Идентификаторы опубликованных страниц.
        """

        self._log(
            f"Запуск публикации '{conf_new_page_name}' в пространство '{conf_space}'"
        )
        render_fn = getattr(body, "render", None)
        if not callable(render_fn):
            raise TypeError("body должен быть PageBuilder с методом render()")
        html_body = str(render_fn())
        self._log(f"Сформировано тело отчёта длиной {len(html_body)} символов")

        release_parent_title: str | None = None
        release_page_title: str | None = None
        release_version: str | None = None
        if test_cycle_version:
            parts = test_cycle_version.split(".")
            if len(parts) == 4 and parts[3] != "UU":
                release_version = ".".join(parts[:3])
            elif len(parts) == 6 and parts[3] == "UU":
                release_version = ".".join(parts[:5])

        if release_version:
            parent_tokens = conf_parent_page.split(" ")
            page_tokens = conf_new_page_name.split("_")
            release_parent_title = " ".join(
                release_version if release_version in token else token
                for token in parent_tokens
            )
            release_page_title = "_".join(
                release_version if release_version in token else token
                for token in page_tokens
            )

        if release_page_title and release_parent_title:
            self._log(
                "Обнаружена релизная версия: "
                f"родитель '{release_parent_title}', страница '{release_page_title}'"
            )
        else:
            self._log("Релизная версия не определена, публикуем только основную страницу")

        try:
            attachments_list: List[Path | str] = []
            if attachments:
                attachments_list.extend(attachments)
            if attachments_dir:
                directory = Path(attachments_dir)
                if directory.exists() and directory.is_dir():
                    for file_path in sorted(directory.iterdir()):
                        if file_path.is_file():
                            attachments_list.append(file_path)

            if attachments_list:
                self._log(f"Найдено вложений: {len(attachments_list)}")
            else:
                self._log("Вложений нет")

            cur_top, cur_version = self._derive_stress_titles(conf_new_page_name)
            release_page_id: str | None = None

            if release_page_title and release_parent_title:
                rel_top, rel_version = self._derive_stress_titles(release_page_title)
                self._log(f"Готовлю релизное дерево страниц: {rel_top} -> {rel_version}")
                self._ensure_container_page(space=conf_space, title=rel_top)
                self._ensure_container_page(
                    space=conf_space, title=rel_version, parent_title=rel_top
                )
                self._ensure_container_page(
                    space=conf_space,
                    title=release_parent_title,
                    parent_title=rel_version,
                )
                release_page_id = self.publish(
                    space=conf_space,
                    title=release_page_title,
                    parent_title=release_parent_title,
                    body=html_body,
                )
                self._log(
                    f"Релизная страница '{release_page_title}' опубликована (id={release_page_id})"
                )
                self._ensure_container_page(
                    space=conf_space, title=cur_version, parent_title=rel_version
                )
            else:
                self._ensure_container_page(space=conf_space, title=cur_top)
                self._ensure_container_page(
                    space=conf_space, title=cur_version, parent_title=cur_top
                )

            self._ensure_container_page(
                space=conf_space, title=conf_parent_page, parent_title=cur_version
            )
            page_id = self.publish(
                space=conf_space,
                title=conf_new_page_name,
                parent_title=conf_parent_page,
                body=html_body,
                attachments=attachments_list or None,
            )
            self._log(f"Основная страница '{conf_new_page_name}' опубликована (id={page_id})")
            if attachments_list:
                self._log(f"К странице '{conf_new_page_name}' прикреплено файлов: {len(attachments_list)}")
            result = {"page_id": page_id, "release_page_id": release_page_id}
        except Exception as exc:
            self._log(f"[ERROR] Публикация отчёта завершилась с ошибкой: {exc}")
            raise

        self._log(
            "Публикация завершена: "
            f"основная страница id={result['page_id']}, "
            f"релизная страница id={result.get('release_page_id')}"
        )
        return result

    def _derive_stress_titles(self, page_title: str) -> tuple[str, str]:
        segments = page_title.split("_")
        version_segment = segments[1] if len(segments) > 1 else page_title
        top_page = f"STRESS ⬝ {version_segment[:3]}"
        version_page = f"STRESS_report ⬝ {version_segment}"
        return top_page, version_page

    def _ensure_container_page(
        self, *, space: str, title: str, parent_title: str | None = None
    ):
        if self._client.page_exists(space=space, title=title):
            page_id = self._client.get_page_id(space=space, title=title)
            self._log(f"Страница '{title}' уже существует (id={page_id})")
            return page_id
        parent_id = (
            self._client.get_page_id(space=space, title=parent_title)
            if parent_title
            else None
        )
        self._log(
            f"Создаю контейнер-страницу '{title}'"
            f"{f' под родителем {parent_title}' if parent_title else ''}"
        )
        result = self._client.create_page(
            space=space,
            title=title,
            body=self._container_body(),
            parent_id=parent_id,
            type="page",
            representation="storage",
            editor="v2",
        )
        page_id = (
            result["id"]
            if isinstance(result, dict) and result.get("id")
            else self._client.get_page_id(space=space, title=title)
        )
        if not page_id:
            raise RuntimeError(f"Не удалось создать контейнер-страницу '{title}' в пространстве '{space}'")
        return page_id

    def _container_body(self) -> str:
        """
        Тело страницы с макросом для отображения потомков, аналогично старой логике.
        """

        return (
            "Страница создана автоматически.<br/><br/>"
            "<ac:structured-macro ac:name=\"children\">"
            "<ac:parameter ac:name=\"all\">true</ac:parameter>"
            "</ac:structured-macro>"
        )
