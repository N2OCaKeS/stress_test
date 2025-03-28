import requests

def get_packagetext_from_repo(url):
    res = requests.get(url=url)
    return res.text

def parse_packages_old(text):
    packages = []
    
    for line in text.splitlines():
        if line.startswith('Package:'):
            package_name = line.split(':', 1)[1].strip()
            packages.append(package_name)
    
    return packages


def parse_packages(text):
    packages = []
    current_pkg = {}
    
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('Package:'):
            if current_pkg:  # Если уже есть данные о пакете, сохраняем их
                packages.append(current_pkg)
                current_pkg = {}
            current_pkg['name'] = line.split(':', 1)[1].strip()
        elif line.startswith('Depends:') and 'name' in current_pkg:
            depends = line.split(':', 1)[1].strip()
            # Упрощаем зависимости, убирая версии
            depends_list = [d.split(' (')[0].strip() for d in depends.split(',')]
            current_pkg['depends'] = depends_list
    
    if current_pkg:  # Добавляем последний пакет
        packages.append(current_pkg)
    
    return packages

# lst_comp = ['main', 'contrib', 'non-free']
# url_repo = "deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.7/1.7.7.1/base-repository 1.7_x86-64 main contrib non-free"


# url = "https://releases.devos.astralinux.ru/frozen/1.7/1.7.7/1.7.7.6/base-repository/dists/1.7_x86-64/main/binary-amd64/Packages"

# text_with_package = get_packagetext_from_repo(url=url)
# packages = parse_packages_lines(text=text_with_package)
# print(packages)