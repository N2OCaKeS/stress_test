from package_group import GROUPS

# lst_packages = ["apache2", "postgresql", "some_other_package"]  # Пример списка пакетов


def check_cat(lst_packages):
    for group, packages in GROUPS.items():
        if any(pkg in lst_packages for pkg in packages):
            print(group)