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
"""

# Расширять по мере добавления новых платформ в парк. Префикс должен быть
# уникальным относительно прочих (не подстрока другого) и совпадать с тем,
# что reader/operator увидит в карточке сервера.
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


def is_known_os(name: str | None) -> bool:
    """True iff `name` начинается с одного из `KNOWN_OS_PREFIXES`.

    None/пустая строка → False. Worker отдаёт `os_version` строкой из
    `/etc/os-release` (например `Astra Linux SE 1.8.5`); whitelist'им
    по префиксу-семейству, минор-версии не зашиваем.
    """
    if not name:
        return False
    return name.startswith(KNOWN_OS_PREFIXES)
