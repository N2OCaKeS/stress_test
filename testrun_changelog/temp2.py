import requests
from parse_repo import parse_packages
from package_group import GROUPS

res = requests.get("https://releases.devos.astralinux.ru/frozen/1.7/1.7.7/1.7.7.6/base-repository/dists/1.7_x86-64/main/binary-amd64/Packages")
text = res.text

all_info_packages_from_repo = parse_packages(text)

# for pkg in packages:
#     print(f"Пакет: {pkg['name']}")
#     if 'depends' in pkg:
#         print("Зависимости:")
#         for dep in pkg['depends']:
#             print(f"  - {dep}")
#     print()

# print(packages[:10])

NEW_GROUPS = dict()
for group_name, packages in GROUPS.items():
    # print(group_name, packages)
    for package in list(packages):
        # print(package)
        # Ищем зависимости пакета
        for pkg_info in all_info_packages_from_repo:
            if pkg_info["name"] == package:
                # print(package)
                # Добавляем зависимости, если их еще нет в списке
                if pkg_info.get("depends"):
                    for dep in pkg_info["depends"]:
                        if dep not in packages:
                            packages.append(dep)
    
    NEW_GROUPS[group_name] = packages

# print(NEW_GROUPS)
for group_name, packages in NEW_GROUPS.items():
    if group_name == "FreeIPA":
        print(packages)