"""Whitelist валидных префиксов имён OS, которые worker может прислать в inventory.sync.

`_resolve_or_create_os` опирается на этот список: имя, начинающееся с одного из
префиксов → существующий flow (lookup / create version-record); имя, не
совпадающее ни с одним префиксом → запись не создаётся, эмитим WARNING
`os.unknown_observed`, inventory.sync продолжается без апдейта
`server.os_version_id`.

Префиксы, а не полные имена, потому что worker отдаёт строку из
`/etc/os-release` с версией ("Astra Linux SE 1.8.5", "Ubuntu 24.04",
"Red Hat Enterprise Linux 9.4"), а каталог тех же дистрибутивов растёт по
минорам — захардкодить каждую полную строку нерабочее. Сравнение
case-sensitive: worker нормализует имя ровно к тому виду, который оператор
видит в карточке.

По новому inventory-контракту worker шлёт чистую версию (`1.8.1.6`) без имени
дистрибутива — редакция ОС уезжает отдельным полем `os_security_mode`. Такую
dotted-версию тоже принимаем в каталог (см. `_VERSION_RE`).
"""

import re

# Расширять по мере добавления новых платформ в парк. Префикс должен быть
# уникальным относительно прочих (не подстрока другого) и совпадать с тем,
# что пользователь увидит в карточке сервера.
KNOWN_OS_PREFIXES = (
    # Astra Linux (Special Edition / Common Edition)
    "Astra Linux",
    # Ubuntu / Debian
    "Ubuntu",
    "Debian",
    # RHEL-семейство
    "Red Hat Enterprise Linux",
    "RHEL",
    "CentOS",
    "AlmaLinux",
    "Rocky Linux",
    # ALT Linux (российский дистрибутив)
    "ALT Linux",
    "ALT Server",
    # РЕД ОС
    "RED OS",
)

# Чистая dotted-версия ("1.8.1.6", "1.7.5"): минимум один разделитель, чтобы
# голое число из os-release не создавало запись в каталоге.
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")


def is_known_os(name: str | None) -> bool:
    """True для имени с whitelist-префиксом либо чистой dotted-версии.

    None/пустая строка → False. Полную строку из `/etc/os-release`
    (`Astra Linux SE 1.8.5`) ловим по префиксу-семейству; чистую версию нового
    контракта (`1.8.1.6`) — version-паттерном. Минор-версии не зашиваем.
    """
    if not name:
        return False
    if name.startswith(KNOWN_OS_PREFIXES):
        return True
    return bool(_VERSION_RE.match(name))
