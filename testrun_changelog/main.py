import requests
import argparse
from checker import Checker
from package_groups import GROUPS
from packagemanager import PackageGroupManager
from parsers import RepositoryParser, TablePackageExtractor, PackageParser

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-fld', '--dependencies',
                    action='store_true',
                    required=False,
                    help='first level dependencies',
                    dest='FLD')
args = parser.parse_args()
# 1 
repo_line = "deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.7/1.7.7.6/base-repository 1.7_x86-64 main contrib non-free"
# repo_line18 = "deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.2/1.8.2.6/installation 1.8_x86-64 main contrib non-free"

# 2 Получили changelog
rp = RepositoryParser(repo_line=repo_line)
url_changelog, urls_packages = rp.get_all_urls()

# 3
# тут условие, нужно ли расширить по зависимостям 1 уровня или нет 
# получить информацию по зависимостям пакетов
# pp = PackageParser()
if args.FLD:
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

tpe = TablePackageExtractor(changelog_url=url_changelog)
packages_from_all_table_changelog = list()
for table_name in ['Added_binaries', 'Changelog', 'Upgraded_binaries']:
    packages_from_one_table_changelog = tpe.extract_from_table(table_name)
    packages_from_all_table_changelog.extend(packages_from_one_table_changelog)

checker = Checker(groups=new_groups)
groups = checker.check_package_changes_by_test_categories(packages_from_all_table_changelog)
print(groups)

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