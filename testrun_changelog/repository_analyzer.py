import requests
from dataclasses import dataclass
from typing import List, Dict, Optional

from checker import Checker
from package_groups import GROUPS
from utils import fetch_packages
from errors import PackagesNotFound
from logging_conf import testrun_logger
from packagemanager import PackageGroupManager
from parsers import RepositoryParser, TablePackageExtractor, PackageParser


@dataclass
class RepositoryConfig:
    repo_url: str
    tables_to_process: List[str]


class RepositoryProcessor:
    def __init__(self, config: RepositoryConfig):
        self.config = config
        self.packages_from_changelog = []
        self.packages_from_components = []

    def process(self, include_components: bool = False) -> None:
        rp = RepositoryParser(repo_line=self.config.repo_url)
        url_changelog, urls_packages = rp.get_all_urls()
        self._process_changelog(url_changelog)

        if include_components:
            self._process_components(urls_packages)
    
    def _process_changelog(self, changelog_url: str) -> None:
        tpe = TablePackageExtractor(changelog_url=changelog_url)
        for table_name in self.config.tables_to_process:
            packages_from_table = tpe.extract_from_table(table_name)
            self.packages_from_changelog.extend(packages_from_table)
            testrun_logger.info(f"Парсинг changelog: Таблица {table_name}, Репозиторий {self.config.repo_url}")

    def _process_components(self, component_urls: List[str]) -> None:
        testrun_logger.info("Парсинг пакетов по компонентам")
        for url_comp_packages in component_urls:
            res = fetch_packages(url_comp_packages)
            packages_comp = PackageParser.parse_packages(res)
            self.packages_from_components.extend(packages_comp)


class RepositoryAnalysisController:
    def __init__(self):
        self.repositories = []
        self.all_packages_from_changelog = []
        self.all_packages_from_components = []
    
    def add_repository(self, config: RepositoryConfig) -> None:
        self.repositories.append(config)
    
    def run_analysis(self, include_components: bool = False, ret_groups_with_pkgs: bool = False) -> Dict | List:
        for config in self.repositories:
            processor = RepositoryProcessor(config)
            processor.process(include_components)
            
            self.all_packages_from_changelog.extend(processor.packages_from_changelog)
            self.all_packages_from_components.extend(processor.packages_from_components)
        
        if include_components:
            testrun_logger.info(f"Указан параметр о включении зависимостей первого уровня")
            pgm = PackageGroupManager(
                initial_groups=GROUPS,
                all_packages_info=self.all_packages_from_components
            )
            new_groups = pgm.process_groups()
        else:
            new_groups = GROUPS
    
        checker = Checker(groups=new_groups)
        if ret_groups_with_pkgs:
            return checker.check_package_changes_by_test_categories_dct(self.all_packages_from_changelog)
        return checker.check_package_changes_by_test_categories(self.all_packages_from_changelog)