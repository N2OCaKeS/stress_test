"""
Утилиты для публикации готовых отчётов в Confluence.

Основной класс :class:`ConfluencePublisher` инкапсулирует клиент
``atlassian.Confluence`` и предоставляет высокоуровневые методы для
создания страниц, обновления содержимого и загрузки вложений.
"""

from pathlib import Path
from typing import Iterable, Sequence

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
        payload: list[dict[str, str]] = [
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
            data=payload,
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
