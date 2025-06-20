import requests
from bs4 import BeautifulSoup

from utils import fetch_changelog
from logging_conf import testrun_logger

class Package:
    def __init__(self, name=None, depends=None):
        self.name = name
        self.depends = depends or []
    
    def __repr__(self):
        return f"Package(name='{self.name}', depends={self.depends})"
    
    def to_dict(self):
        return {
            'name': self.name,
            'depends': self.depends
        }


class PackageParser:
    @staticmethod
    def parse_packages(text):
        packages = []
        current_pkg = None
        
        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('Package:'):
                if current_pkg:  # Если уже есть данные о пакете, сохраняем их
                    packages.append(current_pkg)
                current_pkg = Package(name=line.split(':', 1)[1].strip())
            elif line.startswith('Depends:') and current_pkg and current_pkg.name:
                depends = line.split(':', 1)[1].strip()
                # Упрощаем зависимости, убирая версии
                depends_list = [d.split(' (')[0].strip() for d in depends.split(',')]
                current_pkg.depends = depends_list
        
        if current_pkg:  # Добавляем последний пакет
            packages.append(current_pkg)
        
        return packages

    @staticmethod
    def parse_packages_to_dicts(text):
        return [pkg.to_dict() for pkg in PackageParser.parse_packages(text)]


class TablePackageExtractor:
    def __init__(self, changelog_url):
        self.changelog_url = changelog_url
        self.changelog_html = ""
        self.soup = BeautifulSoup("", 'html.parser')  # Инициализируем пустым объектом
        
        try:
            self.changelog_html = fetch_changelog(changelog_url)
            self.soup = BeautifulSoup(self.changelog_html, 'html.parser')
        except requests.exceptions.HTTPError as e:
            testrun_logger.warning(f"Ошибка HTTP при загрузке журнала изменений из {changelog_url}: {e}")
        except requests.exceptions.RequestException as e:
            testrun_logger.warning(f"Ошибка запроса при получении журнала изменений из {changelog_url}: {e}")
        except Exception as e:
            testrun_logger.warning(f"Неожиданная ошибка обработки журнала изменений от {changelog_url}: {e}")
    
    def is_loaded(self):
        return bool(self.changelog_html) and bool(self.soup.find())
    
    def extract_from_table(self, section_name, package_column=0):
        if not self.is_loaded():
            return set()
        
        try:
            section = self.soup.find('a', {'name': section_name})
            if not section:
                return set()
            
            table = section.find_next('table')
            if not table:
                return set()
            
            packages = set()
            for row in table.find_all('tr')[1:]:
                cells = row.find_all('td')
                if len(cells) > package_column:
                    package_name = cells[package_column].get_text(strip=True)
                    if '(' in package_name:
                        package_name = package_name.split('(')[0].strip()
                    packages.add(package_name)

            return packages
        except Exception as e:
            testrun_logger.warning(f"Ошибка извлечения пакетов из раздела {section_name}: {e}")
            return set()


class RepositoryParser:
    def __init__(self, repo_line):
        self.repo_line = repo_line
        self.components = repo_line.split()
        self.type = self.components[0]  # 'deb'
        self.url = self.components[1]   # URL репозитория
        self.suite = self.components[2] # '1.7_x86-64'
        self.repo_components = self.components[3:]  # ['main', 'contrib', 'non-free']
        
        self._parse_url()
    def _parse_url(self):
        temp = self.url.split("/")
        self.name_repo = temp[-1].split("-")[0]
        self.build_vers = temp[-2]
        self.base_url = "/".join(temp[:-1])
    
    def get_changelog_url(self):
        return f"{self.base_url}/sources/changelogs/changelog-{self.name_repo}-{self.build_vers}.html"
    
    def get_packages_urls(self):
        urls = []
        for component in self.repo_components:
            urls.append(f"{self.url}/dists/{self.suite}/{component}/binary-amd64/Packages")
        return urls
    
    def get_all_urls(self):
        testrun_logger.info("Получение changelog url и packages urls")
        return self.get_changelog_url(), self.get_packages_urls()