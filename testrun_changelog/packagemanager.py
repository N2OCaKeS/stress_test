class PackageGroupManager:
    def __init__(self, initial_groups, all_packages_info):
        self.initial_groups = initial_groups
        self.all_packages_info = all_packages_info
        self.new_groups = {}

    # def _expand_dependencies(self, package):
    #     for pkg_info in self.all_packages_info:
    #         if pkg_info.name == package:
    #             return pkg_info.depends
    #     return []
    
    def _expand_dependencies(self, package):
        for pkg_info in self.all_packages_info:
            if pkg_info.name == package:
                all_deps = []
                for dep in pkg_info.depends:
                    alternatives = [alt.strip() for alt in dep.split('|')]
                    
                    # Фильтруем только существующие пакеты
                    existing_alts = [
                        alt for alt in alternatives 
                        if any(pkg.name == alt for pkg in self.all_packages_info)
                    ]
                    
                    # Добавляем ВСЕ доступные альтернативы в список зависимостей
                    all_deps.extend(existing_alts)
                
                return all_deps
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