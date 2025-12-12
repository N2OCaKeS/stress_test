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
                continue
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
            self._client.update_page(page_id=page_id, title=title, body=body)
            return page_id

        parent_id = (
            self._client.get_page_id(space=space, title=parent_title)
            if parent_title
            else None
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
        return (
            result["id"]
            if isinstance(result, dict) and result.get("id")
            else self._client.get_page_id(space=space, title=title)
        )

    # ------------------------------------------------------------------
    def publish_results_from_params(
        self,
        *,
        conf_space: str,
        conf_parent_page: str,
        conf_new_page_name: str,
        test_cycle_version: str | None,
        body: "PageBuilder | str | Any",
        attachments_dir: Path | str | None = None,
        attachments: Sequence[str | Path] | None = None,
    ):
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
        """

        html_body = self._render_body(body)
        release_parent_title, release_page_title = self._derive_release_titles(
            parent_title=conf_parent_page,
            page_title=conf_new_page_name,
            tcv=test_cycle_version,
        )
        self._publish_stress_results(
            space=conf_space,
            parent_title=conf_parent_page,
            page_title=conf_new_page_name,
            body=html_body,
            attachments_dir=attachments_dir,
            attachments=attachments,
            release_parent_title=release_parent_title,
            release_page_title=release_page_title,
        )

    def _derive_release_titles(
        self, *, parent_title: str, page_title: str, tcv: str | None
    ) -> tuple[str | None, str | None]:
        release_version = self._release_version_from_tcv(tcv)
        if not release_version:
            return None, None

        release_parent = self._replace_version_token(
            value=parent_title,
            release_version=release_version,
            delimiter=" ",
        )
        release_page = self._replace_version_token(
            value=page_title,
            release_version=release_version,
            delimiter="_",
        )
        return release_parent, release_page

    def _release_version_from_tcv(self, tcv: str | None) -> str | None:
        if not tcv:
            return None
        parts = tcv.split(".")
        if len(parts) == 4 and parts[3] != "UU":
            return ".".join(parts[:3])
        if len(parts) == 6 and parts[3] == "UU":
            return ".".join(parts[:5])
        return None

    def _replace_version_token(
        self, *, value: str, release_version: str, delimiter: str
    ) -> str:
        tokens = value.split(delimiter)
        replaced = [
            release_version if release_version in token else token
            for token in tokens
        ]
        return delimiter.join(replaced)

    def _publish_stress_results(
        self,
        *,
        space: str,
        parent_title: str,
        page_title: str,
        body: str,
        attachments_dir: Path | str | None,
        attachments: Sequence[str | Path] | None,
        release_parent_title: str | None,
        release_page_title: str | None,
    ):
        attachments_list = self._collect_attachments(
            attachments_dir, attachments
        )
        cur_top, cur_version = self._derive_stress_titles(page_title)

        if release_page_title and release_parent_title:
            rel_top, rel_version = self._derive_stress_titles(
                release_page_title
            )
            self._ensure_container_page(space=space, title=rel_top)
            self._ensure_container_page(
                space=space, title=rel_version, parent_title=rel_top
            )
            self._ensure_container_page(
                space=space,
                title=release_parent_title,
                parent_title=rel_version,
            )
            self.publish(
                space=space,
                title=release_page_title,
                parent_title=release_parent_title,
                body=body,
            )
            self._ensure_container_page(
                space=space, title=cur_version, parent_title=rel_version
            )
        else:
            self._ensure_container_page(space=space, title=cur_top)
            self._ensure_container_page(
                space=space, title=cur_version, parent_title=cur_top
            )

        self._ensure_container_page(
            space=space, title=parent_title, parent_title=cur_version
        )
        self.publish(
            space=space,
            title=page_title,
            parent_title=parent_title,
            body=body,
            attachments=attachments_list,
        )

    def _collect_attachments(
        self,
        attachments_dir: Path | str | None,
        attachments: Sequence[str | Path] | None,
    ) -> List[Path | str] | None:
        files: List[Path | str] = []
        if attachments:
            files.extend(attachments)
        if attachments_dir:
            directory = Path(attachments_dir)
            if directory.exists() and directory.is_dir():
                for file_path in sorted(directory.iterdir()):
                    if file_path.is_file():
                        files.append(file_path)
        return files or None

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
            return self._client.get_page_id(space=space, title=title)
        parent_id = (
            self._client.get_page_id(space=space, title=parent_title)
            if parent_title
            else None
        )
        result = self._client.create_page(
            space=space,
            title=title,
            body="",
            parent_id=parent_id,
            type="page",
            representation="storage",
            editor="v2",
        )
        return (
            result["id"]
            if isinstance(result, dict) and result.get("id")
            else self._client.get_page_id(space=space, title=title)
        )

    def _render_body(self, body: Any) -> str:
        """
        Принимает либо готовый HTML, либо PageBuilder и возвращает HTML.
        """

        if body is None:
            return ""
        render = getattr(body, "render", None)
        if callable(render):
            return str(render())
        return str(body)
