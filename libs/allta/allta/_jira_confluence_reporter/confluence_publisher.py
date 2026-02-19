"""
Утилиты для публикации готовых отчётов в Confluence.

Основной класс :class:`ConfluencePublisher` инкапсулирует клиент
``atlassian.Confluence`` и предоставляет высокоуровневые методы для
создания страниц, обновления содержимого и загрузки вложений.

-------------------------------------------------------------------------------
Особенности реализации
-------------------------------------------------------------------------------

1) Версионное дерево страниц
----------------------------
При наличии ``test_cycle_version`` и включённом ``create_tree=True`` создаётся дерево:

STRESS ⬝ <global>
  └─ STRESS_report ⬝ <branch>
       ├─ STRESS_report ⬝ <full>
       │    └─ STRESS_report <full> ⬝ <parent>
       └─ STRESS_report <branch> ⬝ <parent>

Где ``<branch>`` — "релизная" версия ветки, а ``<full>`` — полная версия прогона.
Страница ``STRESS_report <branch> ⬝ <parent>`` всегда обновляется и хранит последний прогон ветки.

Где:
- global = первые 2 числовых сегмента версии (например, 1.8)
- branch:
    * для numeric веток: первые 3 сегмента (например, 1.7.8 для 1.7.8.15)
    * для UU-веток: первые 5 сегментов (например, 1.7.9.UU.1 для 1.7.9.UU.1.2)
- full = полная версия как есть (например, 1.7.8.15 / 1.7.9.UU.1.2)

2) Обход ограничения Confluence по уникальности заголовков
----------------------------------------------------------
Confluence не позволяет иметь две страницы с одинаковым title в одном space.
Чтобы можно было создавать "одинаково названные" страницы под разными родителями,
используется добавление НЕвидимого суффикса к title.

Пользователь видит одинаковые названия, но Confluence получает разные заголовки.

3) Правило включения имени родителя в "скрытый" токен
-----------------------------------------------------
В токен, который влияет на уникальный скрытый суффикс, имя ближайшего родителя
добавляется ТОЛЬКО если версия "трёхчастная" и чисто числовая, например:
1.7.8 / 1.3.5 / 1.6.7

Для 1.8 или 1.8.4.46 родитель в токен НЕ добавляется по этому правилу.

4) Переименование вложений в branch-ветке
----------------------------------------
Если имя вложения содержит full-версию, для страницы
``STRESS_report <branch> ⬝ <parent>`` создаётся временная копия
с заменой ``full -> branch``.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Iterable, Sequence, Any, List, Dict, cast, TYPE_CHECKING, Tuple

if TYPE_CHECKING:  # for type hints only
    from .page_builder import PageBuilder

from atlassian import Confluence


class ConfluencePublisher:
    """
    Публикует HTML-страницы и файлы вложений в Confluence.

    Класс создаёт либо обновляет страницу, прикрепляет указанные файлы и,
    при необходимости, создаёт контейнерные страницы-узлы для формирования
    дерева отчётов.
    """

    # --- Невидимые символы для "скрытых" суффиксов ---
    _ZWSP: str = "\u200B"  # zero-width space: 0
    _ZWNJ: str = "\u200C"  # zero-width non-joiner: 1
    _WJ: str = "\u2060"    # word joiner: рамка суффикса

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str | None = None,
        token: str | None = None,
    ) -> None:
        """
        Создаёт подключение к Confluence.

        Args:
            base_url (str):
                Базовый URL экземпляра Confluence. Если не начинается с ``http``,
                будет дополнен как ``https://<base_url>``.
            username (str):
                Имя пользователя.
            password (str | None):
                Пароль пользователя.
            token (str | None):
                API token, если используется.

        Raises:
            ValueError:
                Если не указаны пароль и токен одновременно.
        """
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        auth_kwargs: Dict[str, Any] = {"url": base_url, "username": username}
        if password:
            auth_kwargs["password"] = password
        elif token:
            auth_kwargs["token"] = token
        else:
            raise ValueError("Either password or token must be provided")
        self._client: Confluence = Confluence(**auth_kwargs)

    # ------------------------------------------------------------------
    def _log(self, message: str) -> None:
        """
        Простая обёртка над print для единообразного логирования.

        Args:
            message (str): Сообщение для вывода.
        """
        print(f"[ConfluencePublisher] {message}")

    def _normalize(self, s: str | None) -> str | None:
        """
        Нормализует строку: приводит к ``str``, делает ``strip()``, превращает пустую в ``None``.

        Args:
            s (str | None): Входная строка или ``None``.

        Returns:
            str | None: Нормализованная строка или ``None``.
        """
        if s is None:
            return None
        t = str(s).strip()
        return t or None

    # ------------------------------------------------------------------
    # Скрытые суффиксы: делаем title уникальным, но визуально не меняем.
    # ------------------------------------------------------------------
    def _zw_bits(self, b: bytes) -> str:
        """
        Преобразует байты в строку из zero-width символов.

        Правило:
            - бит 0 -> ZWSP
            - бит 1 -> ZWNJ

        Args:
            b (bytes): Байты.

        Returns:
            str: Строка из zero-width символов.
        """
        out: List[str] = []
        for byte in b:
            for i in range(8):
                bit = (byte >> (7 - i)) & 1
                out.append(self._ZWNJ if bit else self._ZWSP)
        return "".join(out)

    def _hidden_suffix(self, token: str) -> str:
        """
        Формирует невидимый суффикс на основе token (sha1 -> первые 6 байт -> bits).

        Args:
            token (str): Строка-токен, определяющая уникальность.

        Returns:
            str: Невидимый суффикс (рамка WJ + bits + WJ).
        """
        digest: bytes = hashlib.sha1(token.encode("utf-8")).digest()[:6]  # 48 бит достаточно
        return f"{self._WJ}{self._zw_bits(digest)}{self._WJ}"

    def _with_hidden_suffix(self, visible_title: str, token: str) -> str:
        """
        Возвращает "effective title": видимый заголовок + невидимый суффикс.

        Args:
            visible_title (str): Заголовок, который будет "виден" пользователю.
            token (str): Токен для генерации суффикса.

        Returns:
            str: Заголовок для сохранения в Confluence (unique).

        Raises:
            ValueError: Если visible_title пустой.
        """
        v = self._normalize(visible_title)
        if not v:
            raise ValueError("title не должен быть пустым")
        return v + self._hidden_suffix(token)

    def _is_three_part_numeric_version(self, version: str) -> bool:
        """
        Проверяет, является ли версия вида X.Y.Z, где X,Y,Z - числа.

        Args:
            version (str): Строка версии.

        Returns:
            bool: True/False.
        """
        parts = [p for p in version.split(".") if p]
        if len(parts) != 3:
            return False
        return all(part.isdigit() for part in parts)

    def _token(
        self,
        *,
        kind: str,
        version: str,
        parent_title: str | None,
        visible_title: str,
    ) -> str:
        """
        Собирает токен, влияющий на уникальность скрытого суффикса.

        Правила:
        - kind: "STRESS" для global-страниц, "STRESS_REPORT" для остальных.
        - version: версия узла (global/branch/full или "nover" при выключенном дереве).
        - parent_title добавляется ТОЛЬКО если:
            * parent_title задан
            * version - трёхчастная и чисто числовая (например 1.8.4)

        Args:
            kind (str): Тип страницы ("STRESS" / "STRESS_REPORT").
            version (str): Версия-ключ (например "1.8", "1.8.4", "1.8.4.46", "1.7.5.UU.2", "nover").
            parent_title (str | None): Ближайший родитель (видимый).
            visible_title (str): Видимый заголовок страницы.

        Returns:
            str: Token.
        """
        base = f"{kind}|{version}|{visible_title}"
        if parent_title and self._is_three_part_numeric_version(version):
            base += f"|P={parent_title}"
        return base

    # ------------------------------------------------------------------
    # Confluence helpers: поиск/создание по exact title
    # ------------------------------------------------------------------
    def _get_page_id_by_exact_title(self, *, space: str, title: str) -> str | None:
        """
        Получает page_id по точному title.

        Важно:
            Мы используем exact title, т.к. effective_title включает невидимые символы.

        Args:
            space (str): Пространство Confluence.
            title (str): Точный заголовок страницы.

        Returns:
            str | None: Идентификатор страницы или None.
        """
        try:
            pid = self._client.get_page_id(space=space, title=title)
            return str(pid) if pid else None
        except Exception:
            return None

    def _ensure_page_by_effective_title(
        self,
        *,
        space: str,
        effective_title: str,
        body: str,
        parent_id: str | None,
        update_if_exists: bool,
    ) -> str:
        """
        Создаёт или обновляет страницу по effective_title.

        Args:
            space (str): Пространство Confluence.
            effective_title (str): Точный заголовок, который будет сохранён в Confluence
                (может содержать невидимый суффикс).
            body (str): Тело страницы (Storage format).
            parent_id (str | None): ID родительской страницы (или None).
            update_if_exists (bool): Если True — обновляет существующую страницу.
                Если False — при существовании просто возвращает её id.

        Returns:
            str: page_id созданной/обновлённой страницы.

        Raises:
            RuntimeError: Если не удалось создать страницу.
        """
        existing = self._get_page_id_by_exact_title(space=space, title=effective_title)
        if existing:
            if update_if_exists:
                self._log(f"Обновляю страницу '{effective_title}' (id={existing})")
                self._client.update_page(page_id=existing, title=effective_title, body=body)
            else:
                self._log(f"Страница '{effective_title}' уже существует (id={existing}), пропускаю")
            return existing

        self._log(
            f"Создаю страницу '{effective_title}' в пространстве '{space}'"
            f"{f' под parent_id={parent_id}' if parent_id else ''}"
        )
        result = self._client.create_page(
            space=space,
            title=effective_title,
            body=body,
            parent_id=parent_id,
            type="page",
            representation="storage",
            editor="v2",
        )
        page_id = (
            result["id"]
            if isinstance(result, dict) and result.get("id")
            else self._get_page_id_by_exact_title(space=space, title=effective_title)
        )
        if not page_id:
            raise RuntimeError(f"Не удалось создать страницу '{effective_title}' в пространстве '{space}'")
        return str(page_id)

    # ------------------------------------------------------------------
    def publish(
        self,
        *,
        space: str,
        title: str,
        body: str,
        parent_id: str | None = None,
        attachments: Sequence[str | Path] | None = None,
        labels: Sequence[str] | None = None,
        _effective_title: str | None = None,
    ) -> str:
        """
        Создаёт или обновляет страницу и при необходимости загружает вложения.

        Args:
            space (str): Пространство Confluence.
            title (str): Видимый заголовок страницы (для логов/смыслового имени).
            body (str): Тело страницы в формате Storage.
            parent_id (str | None): ID родительской страницы. Если None — создастся на корне space.
            attachments (Sequence[str | Path] | None): Пути к файлам для прикрепления.
            labels (Sequence[str] | None): Метки страницы.
            _effective_title (str | None): Внутренний параметр. Если передан — используется как
                точный title в Confluence (может содержать невидимые суффиксы).
                Если не передан — используется `title` как есть.

        Returns:
            str: Идентификатор созданной или обновлённой страницы.
        """
        effective_title = _effective_title or title

        page_id = self._ensure_page_by_effective_title(
            space=space,
            effective_title=effective_title,
            body=body,
            parent_id=str(parent_id) if parent_id else None,
            update_if_exists=True,
        )

        if attachments:
            self.attach_files(page_id=page_id, files=attachments)
        if labels:
            self._ensure_labels(page_id=page_id, labels=labels)
        return page_id

    # ------------------------------------------------------------------
    def attach_files(self, *, page_id: str, files: Iterable[Path | str]) -> None:
        """
        Прикрепляет файлы к существующей странице.

        Args:
            page_id (str): Идентификатор страницы.
            files (Iterable[Path | str]): Коллекция путей до файлов.

        Returns:
            None
        """
        for file_path in files:
            path = Path(file_path)
            if not path.exists() or not path.is_file():
                self._log(f"[skip] Вложение не найдено: {path}")
                continue
            self._log(f"Прикрепляю файл {path.name} к странице {page_id}")
            self._client.attach_file(page_id=page_id, filename=str(path))

    def _ensure_labels(self, *, page_id: str, labels: Sequence[str]) -> None:
        """
        Обновляет метки страницы.

        Args:
            page_id (str): Идентификатор страницы.
            labels (Sequence[str]): Список меток.

        Returns:
            None
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
            json=cast(Any, payload),
        )

    # ------------------------------------------------------------------
    def _container_body(self) -> str:
        """
        Возвращает тело контейнерной страницы.

        Returns:
            str: Storage-HTML с макросом children.
        """
        return (
            "Страница создана автоматически.<br/><br/>"
            "<ac:structured-macro ac:name=\"children\">"
            "<ac:parameter ac:name=\"all\">true</ac:parameter>"
            "</ac:structured-macro>"
        )

    def _ensure_container_page(
        self,
        *,
        space: str,
        effective_title: str,
        parent_id: str | None,
    ) -> str:
        """
        Создаёт контейнер-страницу (если её нет). Если есть — ничего не меняет.

        Args:
            space (str): Пространство Confluence.
            effective_title (str): Точный title для Confluence (может содержать невидимые символы).
            parent_id (str | None): ID родителя или None.

        Returns:
            str: page_id контейнера.

        Raises:
            RuntimeError: Если не удалось создать контейнер.
        """
        existing = self._get_page_id_by_exact_title(space=space, title=effective_title)
        if existing:
            self._log(f"Контейнер '{effective_title}' уже существует (id={existing})")
            return existing

        self._log(
            f"Создаю контейнер-страницу '{effective_title}'"
            f"{f' под parent_id={parent_id}' if parent_id else ''}"
        )
        return self._ensure_page_by_effective_title(
            space=space,
            effective_title=effective_title,
            body=self._container_body(),
            parent_id=parent_id,
            update_if_exists=False,
        )

    # ------------------------------------------------------------------
    def _parse_versions(self, test_cycle_version: str | None) -> Tuple[str, str, str] | None:
        """
        Парсит версии для построения дерева.

        Правила:
        - global = первые 2 сегмента;
        - branch:
            * для numeric-версий: первые 3 сегмента;
            * для UU-ветки (``X.Y.Z.UU.K...``): первые 5 сегментов;
        - full = полная версия.

        Args:
            test_cycle_version (str | None): Строка версии тестового цикла.

        Returns:
            tuple[str, str, str] | None:
                (global, branch, full) или None, если версия отсутствует/не распознана.
        """
        tcv = self._normalize(test_cycle_version)
        if not tcv:
            return None

        parts = [p.strip() for p in tcv.split(".") if p.strip()]
        if len(parts) < 2:
            return None

        global_v = ".".join(parts[:2])
        full_v = ".".join(parts)

        if len(parts) >= 5 and parts[3].upper() == "UU":
            branch_v = ".".join(parts[:5])
        elif len(parts) >= 3:
            branch_v = ".".join(parts[:3])
        else:
            branch_v = ".".join(parts)

        return global_v, branch_v, full_v

    def _tree_page_names(self, *, global_v: str, branch_v: str, full_v: str) -> Tuple[str, str, str]:
        """
        Формирует видимые названия контейнерных страниц версионного дерева.

        Returns:
            tuple[str, str, str]:
                (global_title, branch_title, full_title)
        """
        return (
            f"STRESS ⬝ {global_v}",
            f"STRESS_report ⬝ {branch_v}",
            f"STRESS_report ⬝ {full_v}",
        )

    def _report_page_title(self, *, version: str, parent_visible: str | None) -> str:
        """
        Формирует видимый заголовок страницы с отчётом.
        """
        if parent_visible:
            return f"STRESS_report {version} ⬝ {parent_visible}"
        return f"STRESS_report {version}"

    @classmethod
    def preview_hierarchy(
        cls,
        *,
        test_cycle_versions: str | Sequence[str],
        conf_parent_page: str | None = None,
    ) -> str:
        """
        Возвращает текстовый предпросмотр иерархии страниц без публикации в Confluence.

        Args:
            test_cycle_versions (str | Sequence[str]):
                Одна версия или список версий, например:
                ``"1.7.9.UU.1.1"`` или ``["1.7.9.UU.1.1", "1.7.9.UU.1.2"]``.
            conf_parent_page (str | None):
                Название каталога/группы тестов (например ``"Системные службы"``).

        Returns:
            str: Дерево страниц в виде многострочного текста.
        """
        helper = object.__new__(cls)

        if isinstance(test_cycle_versions, str):
            raw_versions: List[str] = [test_cycle_versions]
        else:
            raw_versions = [str(v) for v in test_cycle_versions]

        parent_visible = helper._normalize(conf_parent_page)

        # dict[global_title][branch_title] -> данные ветки
        tree: Dict[str, Dict[str, Dict[str, Any]]] = {}
        invalid_versions: List[str] = []

        for raw in raw_versions:
            parsed = helper._parse_versions(raw)
            if not parsed:
                invalid_versions.append(str(raw))
                continue

            global_v, branch_v, full_v = parsed
            global_title, branch_title, full_title = helper._tree_page_names(
                global_v=global_v,
                branch_v=branch_v,
                full_v=full_v,
            )
            full_page_title = helper._report_page_title(version=full_v, parent_visible=parent_visible)
            branch_page_title = helper._report_page_title(version=branch_v, parent_visible=parent_visible)

            global_node = tree.setdefault(global_title, {})
            branch_node = global_node.setdefault(
                branch_title,
                {
                    "full_nodes": {},
                    "branch_page_title": branch_page_title,
                    "last_full_version": full_v,
                },
            )
            branch_node["last_full_version"] = full_v

            if full_v != branch_v:
                branch_node["full_nodes"].setdefault(full_title, full_page_title)

        lines: List[str] = []
        global_items = list(tree.items())

        for global_idx, (global_title, branches) in enumerate(global_items):
            lines.append(global_title)
            branch_items = list(branches.items())

            for branch_idx, (branch_title, branch_node) in enumerate(branch_items):
                branch_is_last = branch_idx == len(branch_items) - 1
                branch_prefix = "└─ " if branch_is_last else "├─ "
                lines.append(f"{branch_prefix}{branch_title}")

                branch_indent = "   " if branch_is_last else "│  "
                full_items = list(branch_node["full_nodes"].items())
                branch_page_title = str(branch_node["branch_page_title"])
                last_full_version = str(branch_node["last_full_version"])
                branch_version = branch_title.split("⬝", 1)[1].strip() if "⬝" in branch_title else branch_title
                branch_page_note = ""
                if last_full_version != branch_version:
                    branch_page_note = f" (перезаписывается последней версией: {last_full_version})"
                else:
                    branch_page_note = " (перезаписывается)"

                child_count = len(full_items) + 1  # + branch page

                for full_idx, (full_title, full_page_title) in enumerate(full_items):
                    child_is_last = full_idx == child_count - 1
                    child_prefix = "└─ " if child_is_last else "├─ "
                    lines.append(f"{branch_indent}{child_prefix}{full_title}")

                    full_indent = branch_indent + ("   " if child_is_last else "│  ")
                    lines.append(f"{full_indent}└─ {full_page_title}")

                branch_page_prefix = "└─ "
                lines.append(f"{branch_indent}{branch_page_prefix}{branch_page_title}{branch_page_note}")

            if global_idx != len(global_items) - 1:
                lines.append("")

        if invalid_versions:
            if lines:
                lines.append("")
            lines.append("[skip] Нераспознанные версии:")
            for v in invalid_versions:
                lines.append(f"- {v}")

        return "\n".join(lines) if lines else "Нечего строить: версии не распознаны."

    def _prepare_attachments_for_version(
        self,
        *,
        files: Sequence[Path | str],
        from_version: str,
        to_version: str,
        tmpdir: Path,
    ) -> List[Path]:
        """
        Подготавливает список вложений для ветки с другой версией.

        Если имя файла содержит from_version — создаёт временную копию файла
        в tmpdir с заменой from_version -> to_version и возвращает путь до копии.
        Иначе возвращает исходный путь.

        Это нужно, чтобы Confluence видел разные имена вложений для full/branch страниц.

        Args:
            files (Sequence[Path | str]): Вложения (пути).
            from_version (str): Полная версия (full).
            to_version (str): Версия для ветки branch.
            tmpdir (Path): Временный каталог.

        Returns:
            list[Path]: Список путей к файлам (оригиналы и/или временные копии).
        """
        out: List[Path] = []
        for f in files:
            p = Path(f)
            if not p.exists() or not p.is_file():
                continue
            name = p.name
            if from_version and (from_version in name) and (to_version != from_version):
                new_name = name.replace(from_version, to_version)
                dst = tmpdir / new_name
                shutil.copy2(p, dst)
                out.append(dst)
            else:
                out.append(p)
        return out

    # ------------------------------------------------------------------
    def publish_results_from_params(
        self,
        *,
        conf_space: str,
        conf_parent_page: str | None,
        conf_new_page_name: str,
        test_cycle_version: str | None,
        body: "PageBuilder",
        attachments_dir: Path | str | None = None,
        attachments: Sequence[str | Path] | None = None,
        create_tree: bool = True,
    ) -> Dict[str, str | None]:
        """
        Публикует результат теста, строя дерево версий и публикуя страницы.

        Args:
            conf_space (str):
                Пространство Confluence (space key).
            conf_parent_page (str | None):
                Ближайшая родительская страница (видимое имя), например "Системные службы".
                Может быть None/пустой — тогда страница публикуется прямо в версионный узел.
            conf_new_page_name (str):
                Итоговая страница отчёта (видимое имя). Обычно содержит версию в имени.
            test_cycle_version (str | None):
                Версия тестового цикла, например:
                    1.8.4.46
                    1.7.5.UU.2
                Если не задана — дерево не строится, публикуется упрощённо.
            body (PageBuilder):
                Экземпляр PageBuilder с методом render(), который отдаёт HTML в Storage формате.
            attachments_dir (Path | str | None):
                Каталог с артефактами отчёта (все файлы из каталога будут прикреплены).
            attachments (Sequence[str | Path] | None):
                Дополнительные файлы для прикрепления.
            create_tree (bool):
                Если True — создаёт дерево версий (global/branch/full) и публикует:
                    - page_id: страница конкретного прогона (full)
                    - release_page_id: "последняя" страница ветки (branch), которая перезаписывается
                Если False — не создаёт версионные контейнеры, публикует только:
                    - parent (если задан) как контейнер с макросом children
                    - страницу отчёта под parent (или на корне space если parent не задан)

        Returns:
            dict[str, str | None]:
                {
                    "page_id": id страницы full-версии (основная),
                    "release_page_id": id страницы branch-версии (перезаписываемая) или None
                }

        Raises:
            TypeError:
                Если body не имеет render().
            ValueError:
                Если conf_new_page_name пуст.
        """
        space = conf_space
        parent_visible = self._normalize(conf_parent_page)
        page_visible = self._normalize(conf_new_page_name)
        if not page_visible:
            raise ValueError("conf_new_page_name не должен быть пустым")

        self._log(f"Запуск публикации '{page_visible}' в пространство '{space}'")

        render_fn = getattr(body, "render", None)
        if not callable(render_fn):
            raise TypeError("body должен быть PageBuilder с методом render()")
        html_body = str(render_fn())
        self._log(f"Сформировано тело отчёта длиной {len(html_body)} символов")

        attachments_list: List[Path | str] = []
        if attachments:
            attachments_list.extend(list(attachments))
        if attachments_dir:
            directory = Path(attachments_dir)
            if directory.exists() and directory.is_dir():
                for fp in sorted(directory.iterdir()):
                    if fp.is_file():
                        attachments_list.append(fp)

        if attachments_list:
            self._log(f"Найдено вложений: {len(attachments_list)}")
        else:
            self._log("Вложений нет")

        versions = self._parse_versions(test_cycle_version)

        # --- create_tree=False или нет версии: только parent (если есть) и страница ---
        if (not create_tree) or (not versions):
            if not versions:
                self._log("test_cycle_version не задан/не распознан: публикую без версионного дерева")
            else:
                self._log("create_tree=False: пропускаю создание версионного дерева")

            parent_id: str | None = None
            if parent_visible:
                token_parent = self._token(
                    kind="STRESS_REPORT",
                    version="nover",
                    parent_title=None,
                    visible_title=parent_visible,
                )
                eff_parent = self._with_hidden_suffix(parent_visible, token_parent)
                parent_id = self._ensure_container_page(
                    space=space,
                    effective_title=eff_parent,
                    parent_id=None,
                )

            token_page = self._token(
                kind="STRESS_REPORT",
                version="nover",
                parent_title=parent_visible,
                visible_title=page_visible,
            )
            eff_page = self._with_hidden_suffix(page_visible, token_page)

            page_id = self.publish(
                space=space,
                title=page_visible,
                _effective_title=eff_page,
                parent_id=parent_id,
                body=html_body,
                attachments=attachments_list or None,
            )
            self._log(f"Страница '{page_visible}' опубликована (id={page_id})")
            return {"page_id": page_id, "release_page_id": None}

        # --- Полное дерево ---
        global_v, branch_v, full_v = versions
        self._log(f"Версии: global='{global_v}', branch='{branch_v}', full='{full_v}'")
        global_title, branch_title, full_title = self._tree_page_names(
            global_v=global_v,
            branch_v=branch_v,
            full_v=full_v,
        )

        with tempfile.TemporaryDirectory(prefix="conf_pub_") as td:
            tmpdir = Path(td)

            # 1) global: STRESS
            token_global = self._token(
                kind="STRESS",
                version=global_v,
                parent_title=None,
                visible_title=global_title,
            )
            eff_global = self._with_hidden_suffix(global_title, token_global)
            global_id = self._ensure_container_page(space=space, effective_title=eff_global, parent_id=None)

            # 2) branch: STRESS_REPORT
            token_branch = self._token(
                kind="STRESS_REPORT",
                version=branch_v,
                parent_title=global_title,
                visible_title=branch_title,
            )
            eff_branch = self._with_hidden_suffix(branch_title, token_branch)
            branch_id = self._ensure_container_page(space=space, effective_title=eff_branch, parent_id=global_id)

            # 3) full: STRESS_REPORT (под branch, если full отличается)
            full_parent_id = branch_id
            full_parent_title = branch_title
            if full_v != branch_v:
                token_full = self._token(
                    kind="STRESS_REPORT",
                    version=full_v,
                    parent_title=branch_title,
                    visible_title=full_title,
                )
                eff_full = self._with_hidden_suffix(full_title, token_full)
                full_parent_id = self._ensure_container_page(space=space, effective_title=eff_full, parent_id=branch_id)
                full_parent_title = full_title

            # 4) Страница конкретного прогона: STRESS_report <full> ⬝ <parent>
            report_full_title = self._report_page_title(version=full_v, parent_visible=parent_visible)
            token_full_page = self._token(
                kind="STRESS_REPORT",
                version=full_v,
                parent_title=full_parent_title,
                visible_title=report_full_title,
            )
            eff_full_page = self._with_hidden_suffix(report_full_title, token_full_page)

            page_id_full = self.publish(
                space=space,
                title=report_full_title,
                _effective_title=eff_full_page,
                parent_id=full_parent_id,
                body=html_body,
                attachments=attachments_list or None,
            )
            self._log(f"Страница full-версии '{report_full_title}' опубликована (id={page_id_full})")

            # 5) Страница "последний прогон ветки": STRESS_report <branch> ⬝ <parent>
            report_branch_title = self._report_page_title(version=branch_v, parent_visible=parent_visible)
            token_branch_page = self._token(
                kind="STRESS_REPORT",
                version=branch_v,
                parent_title=branch_title,
                visible_title=report_branch_title,
            )
            eff_branch_page = self._with_hidden_suffix(report_branch_title, token_branch_page)

            branch_attachments: List[Path | str] | None = None
            if attachments_list:
                branch_attachments = self._prepare_attachments_for_version(
                    files=attachments_list,
                    from_version=full_v,
                    to_version=branch_v,
                    tmpdir=tmpdir,
                )

            release_page_id = self.publish(
                space=space,
                title=report_branch_title,
                _effective_title=eff_branch_page,
                parent_id=branch_id,
                body=html_body,
                attachments=branch_attachments or None,
            )
            self._log(
                f"Страница branch-версии '{report_branch_title}' опубликована/обновлена (id={release_page_id})"
            )

            return {"page_id": page_id_full, "release_page_id": release_page_id}
