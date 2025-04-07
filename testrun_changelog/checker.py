class Checker:
    def __init__(self, groups):
        self.groups = groups
    
    def check_package_changes_by_test_categories(self, lst_packages):
        found_groups = []
        for group, packages in self.groups.items():
            if any(pkg in lst_packages for pkg in packages):
                found_groups.append(group)
        return found_groups
    
    def check_package_changes_by_test_categories_dct(self, lst_packages):
        groups_with_found_pkgs = {}
        for group, packages in self.groups.items():
            matched = [pkg for pkg in packages if pkg in lst_packages]
            if matched:
                groups_with_found_pkgs[group] = matched
        return groups_with_found_pkgs