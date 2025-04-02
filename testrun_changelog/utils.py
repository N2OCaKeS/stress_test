import json
import requests

def fetch_repository(version):
    try:
        res = requests.get("http://allta.devos.astralinux.ru/rest/api/get-repo-path")
    except requests.exceptions.RequestException as e:
            print(f"Ошибка при получении данных с http://allta.devos.astralinux.ru/rest/api/get-repo-path: {e}")
    info_version_repo = json.loads(res.text)
    # print(info_version_repo)
    return info_version_repo[version]

fetch_repository("1.8.2.6")