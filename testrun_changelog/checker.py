from logging_conf import testrun_logger


class Checker:
    def __init__(self, groups):
        self.groups = groups
    
    def check_package_changes_by_test_categories(self, lst_packages):
        found_groups = []
        for group, packages in self.groups.items():
            if any(pkg in lst_packages for pkg in packages):
                found_groups.append(group)
        
        testrun_logger.info("Поиск пакетов в changelog. Возвращается список компонентов")
        return found_groups
    
    def check_package_changes_by_test_categories_dct(self, lst_packages):
        groups_with_found_pkgs = {}
        for group, packages in self.groups.items():
            matched = [pkg for pkg in packages if pkg in lst_packages]
            if matched:
                groups_with_found_pkgs[group] = matched
        testrun_logger.info("Поиск пакетов в changelog. Возвращается словарь: Компонент: пакеты")
        return groups_with_found_pkgs