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
parser.add_argument('-vers', '--version',
                    action='store',
                    required=True,
                    help='Astra Linux build version',
                    dest='ALVERS')
args = parser.parse_args()
# 1 
# repo_line = "deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.7/1.7.7.6/base-repository 1.7_x86-64 main contrib non-free"
repo_line18 = "deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.2/1.8.2.6/installation 1.8_x86-64 main contrib non-free"
repo_line18_dev = "deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.2/1.8.2.6/devel-repository 1.8_x86-64 main contrib non-free"

repositories = [repo_line18, repo_line18_dev]

all_packages_from_changelog = []
all_packages_all_component = []

for repo in repositories:
    rp = RepositoryParser(repo_line=repo)
    url_changelog, urls_packages = rp.get_all_urls()
    
    tpe = TablePackageExtractor(changelog_url=url_changelog)
    for table_name in ['Added_binaries', 'Changelog', 'Upgraded_binaries']:
        packages_from_table = tpe.extract_from_table(table_name)
        all_packages_from_changelog.extend(packages_from_table)
    
    if args.FLD:
        for url_comp_packages in urls_packages:
            res = requests.get(url_comp_packages)
            packages_comp = PackageParser.parse_packages(res.text)
            all_packages_all_component.extend(packages_comp)

if args.FLD:
    pgm = PackageGroupManager(initial_groups=GROUPS, all_packages_info=all_packages_all_component)
    new_groups = pgm.process_groups()
else:
    new_groups = GROUPS

checker = Checker(groups=new_groups)
groups = checker.check_package_changes_by_test_categories(all_packages_from_changelog)
print(groups)