class Checker:
    def __init__(self, groups):
        self.groups = groups
    
    def check_package_changes_by_test_categories(self, lst_packages):
        found_groups = []
        for group, packages in self.groups.items():
            if any(pkg in lst_packages for pkg in packages):
                found_groups.append(group)
        return found_groups