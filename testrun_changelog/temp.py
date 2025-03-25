import requests

from parse_repo import parse_packages_lines, get_packagetext_from_repo
from parse_chage_log import parse_version_changes

repo_line = "deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.7/1.7.7.6/base-repository 1.7_x86-64 main contrib non-free"

def convert_repo(repo_line):
    components = repo_line.split()
    repo_info = {
        "type": components[0],  # 'deb'
        "url": components[1],   # URL репозитория
        "suite": components[2], # '1.7_x86-64'
        "components": components[3:]  # ['main', 'contrib', 'non-free']
    }

    temp = repo_info.get("url").split("/")
    name_repo = temp[-1].split("-")[0]
    build_vers = temp[-2]

    url_changelog = "/".join(temp[:-1]) + "/sources/changelogs/" + f"changelog-{name_repo}-{build_vers}.html"
    url_packages =  []
    for component in repo_info.get('components'):
        url_package = "/".join(temp) + "/dists/" + f"{repo_info.get('suite')}/{component}/" + "binary-amd64/Packages"
        url_packages.append(url_package)

    return url_changelog, url_packages

def get_chagngelog(url):
    res_change_log_html = requests.get(url=url)
    return res_change_log_html.text

url_chlog, url_pckgs = convert_repo(repo_line=repo_line)
html_change_log = get_chagngelog(url=url_chlog)
packages_changes = parse_version_changes(html_content=html_change_log)

all_packgs = []
for url_repo_packg in url_pckgs:
    text_packg = get_packagetext_from_repo(url_repo_packg)
    comp_packages = parse_packages_lines(text=text_packg)
    # print(len(comp_packages))
    all_packgs.extend(comp_packages)
    
random_packg = packages_changes.get('fixed_bugs')[0].get('package')

print(random_packg)
print(random_packg in all_packgs)