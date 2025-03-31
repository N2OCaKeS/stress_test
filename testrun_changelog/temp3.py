import requests
from parse_repo import parse_packages
from package_group import GROUPS

res = requests.get("https://releases.devos.astralinux.ru/frozen/1.7/1.7.7/1.7.7.6/base-repository/dists/1.7_x86-64/main/binary-amd64/Packages")
text = res.text
all_info_packages_from_repo = parse_packages(text)

class PackageGroupManager:
    def __init__(self, initial_groups, all_packages_info):
        self.initial_groups = initial_groups
        self.all_packages_info = all_packages_info
        self.new_groups = {}

    def _expand_dependencies(self, package):
        for pkg_info in self.all_packages_info:
            if pkg_info["name"] == package:
                return pkg_info.get("depends", [])
        return []
    
    def _process_group(self, group_name, packages):
        expanded_packages = list(packages)
        
        for package in list(expanded_packages):
            dependencies = self._expand_dependencies(package)
            for dep in dependencies:
                if dep not in expanded_packages:
                    expanded_packages.append(dep)
        
        return expanded_packages
    
    def process_groups(self):
        for group_name, packages in self.initial_groups.items():
            self.new_groups[group_name] = self._process_group(group_name, packages)
        
        return self.new_groups
    

manager = PackageGroupManager(GROUPS, all_info_packages_from_repo)
NEW_GROUPS = manager.process_groups()
# print(NEW_GROUPS)