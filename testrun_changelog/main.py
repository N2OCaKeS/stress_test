import requests

from checker import Checker
from package_groups import GROUPS
from packagemanager import PackageGroupManager
from parsers import RepositoryParser, TablePackageExtractor, PackageParser

# 1 
# repo_line = "deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.7/1.7.7.6/base-repository 1.7_x86-64 main contrib non-free"
repo_line18 = "deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.2/1.8.2.6/installation 1.8_x86-64 main contrib non-free"

# 2 Получили changelog
rp = RepositoryParser(repo_line=repo_line18)
url_changelog, urls_packages = rp.get_all_urls()

tpe = TablePackageExtractor(changelog_url=url_changelog)
added_binaries = tpe.extract_from_table("Added_binaries")
changelog = tpe.extract_from_table("Changelog")
upgraded_binaries = tpe.extract_from_table("Upgraded_binaries")
print(upgraded_binaries)

# 3
# тут условие, нужно ли расширить по зависимостям 1 уровня или нет 
# получить информацию по зависимостям пакетов
# pp = PackageParser()
packages_all_component = []
for url_comp_packages in urls_packages:
    res = requests.get(url_comp_packages)
    packages_comp = PackageParser.parse_packages(res.text)
    packages_all_component.extend(packages_comp)
# print(packages_all_component)
# 4
pgm = PackageGroupManager(initial_groups=GROUPS, all_packages_info=packages_all_component)
new_groups = pgm.process_groups()
# print(new_groups)

# 5
# Проверяем есть ли совпадения с changelog
checker = Checker(groups=new_groups)
groups1 = checker.check_package_changes_by_test_categories(added_binaries)
groups2 = checker.check_package_changes_by_test_categories(upgraded_binaries)
groups3 = checker.check_package_changes_by_test_categories(changelog)
print(groups2)

"""
    TODO
    1) нужно узнать либо версию либо репу
    2) спрашиваем такой ли url на changelog или проверяем доступность
    3) спрашивем нужно ли смотреть первые зависимости пакетов
    Если да:
        4) Проверяем доступность информации о пакетах
        5) Собираем информацию о всех пакетах (название, зависимости)
        6) Расширяем список пакетов по группам
        7) Проверяем совпадение с changelog
        8) Строим прогон по ключам
    Если нет:
        4) Проверяем совпадение с changelog
        5) Строим прогон по ключам
"""