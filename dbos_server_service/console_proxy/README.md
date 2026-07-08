# console_proxy

Веб-прокси графической консоли ВМ: мост **WebSocket ↔ TCP** для VNC (noVNC) и
SPICE (spice-html5). Браузер открывает консоль ВМ по HTTPS/WSS, прокси проверяет
короткоживущий токен, добирается до VNC/SPICE-сокета на хабе и гоняет байты.

## Как это работает

1. Пользователь в web_ui жмёт «Консоль» → `POST /api/server/v1/vms/{id}/console`
   отдаёт `{ws_path, token, kind, ...}`. `token` — подписанный JWT (HS256), в нём
   `vm_id`, `hub_ip`, `hub_server_id`, `kind`, `port`/`display`/`domain`, `exp`.
2. web_ui открывает `wss://<host>/vm-console/<kind>/<vm_id>?token=<jwt>` — ingress
   отдаёт этот префикс сюда. Страница-обёртка (noVNC/spice-html5, self-hosted)
   подтягивается с того же origin (CSP='self', CDN запрещён).
3. Прокси проверяет подпись/срок токена тем же секретом `VM_CONSOLE_TOKEN_SECRET`,
   что использует server_service, резолвит TCP-таргет консоли на хабе и мостит
   его в WebSocket.

Токен можно передать query-параметром `?token=` (так делает noVNC) или
WS-subprotocol'ом `bearer.<jwt>` — второй способ не светит токен в URL/логах.

## Достижимость хаба (важно)

qemu/libvirt по умолчанию биндят VNC/SPICE на `127.0.0.1` **хаба** — снаружи
сокет не виден. Отсюда два режима (`CONSOLE_TARGET_MODE`):

- **ssh** (дефолт, безопасно): прокси открывает SSH-туннель на хаб под его
  управляющей учёткой (креды тянутся из server_service `/internal/servers/{id}/
  management/credentials`) и внутри туннеля коннектится к `127.0.0.1:<port>`.
  Порт при отсутствии в токене резолвится через `virsh domdisplay <domain>`.
- **direct**: libvirt настроен слушать VNC/SPICE на mgmt-LAN, прокси коннектится
  прямо на `hub_ip:<port>` из токена. Только для доверенного сегмента.

## Запуск локально

```bash
cd console_proxy
poetry install
PYTHONPATH=. VM_CONSOLE_TOKEN_SECRET=dev-secret CONSOLE_TARGET_MODE=direct \
  poetry run python -m src.main   # слушает :8085
```

## Тесты

```bash
cd console_proxy && PYTHONPATH=. poetry run pytest    # либо: make test-console
```

Покрыто: валидация токена (подпись/срок/issuer/kind/tamper), выбор резолвера,
прямой bridge на мок-TCP, полный WS↔TCP end-to-end и close-коды при отказе.

## Статика (noVNC + spice-html5)

Вшита в образ на этапе сборки (`fetch_vendor.sh` тянет пинованные релизы в
`static/assets/`). В рантайме ничего не грузится с CDN.

## Деплой

- Образ: `console_proxy/docker/Dockerfile` (собирается `scripts/k8s/build_and_import.sh`).
- Манифесты: `k8s/66-console-proxy.yaml`, ingress-роут `/vm-console`, NetworkPolicy
  `console-proxy-ingress`/`console-proxy-egress`, секреты `VM_CONSOLE_TOKEN_SECRET`
  + `CONSOLE_PROXY_SERVER_API_KEY` в `k8s/20-secrets.yaml`.
