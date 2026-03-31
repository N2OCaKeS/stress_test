import requests

API_KEY = ""
HEADER = {"Authorization": f"Bearer {API_KEY}"}

server_name = "stand11_LowServer3"

server_list = requests.get(
    "http://allta.devos.astralinux.ru:21501/api/server/v1/manage/",
    headers=HEADER,
).json()

server_os = ""

for server in server_list:
    if str(server.get("name", "")).strip() == server_name.strip():
        server_os = str(server.get("os_version") or server.get("os_version_name") or "").strip()
        break

if not server_os:
    raise SystemExit(f"Не нашли os_version для сервера: {server_name}")

get_os_pass = requests.get(
    f"http://allta.devos.astralinux.ru:21501/api/server/v1/passwords/{server_os}",
    headers=HEADER,
).json()

print(get_os_pass['password'])
