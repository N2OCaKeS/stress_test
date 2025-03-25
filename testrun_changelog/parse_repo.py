import requests

def get_packagetext_from_repo(url):
    res = requests.get(url=url)
    return res.text

def parse_packages_lines(text):
    packages = []
    
    for line in text.splitlines():
        if line.startswith('Package:'):
            package_name = line.split(':', 1)[1].strip()
            packages.append(package_name)
    
    return packages

# lst_comp = ['main', 'contrib', 'non-free']
# url_repo = "deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.7/1.7.7.1/base-repository 1.7_x86-64 main contrib non-free"


# url = "https://releases.devos.astralinux.ru/frozen/1.7/1.7.7/1.7.7.6/base-repository/dists/1.7_x86-64/main/binary-amd64/Packages"

# text_with_package = get_packagetext_from_repo(url=url)
# packages = parse_packages_lines(text=text_with_package)
# print(packages)