# Astra-openvpn-server

Комплекс нагрузочного тестирования для проверки Astra OpenVPN Server.

## Test 1

### Astra-openvpn-server (ovpn)

Нагрузочный тест Astra OpenVPN:

- Готовит инфраструктуру (Libvirt): сборка/восстановление ВМ, сетевые настройки, заливка скриптов.
- Настраивает сервер openvpn и iperf на `testvm1`.
- Запускает генерацию клиентов на остальных ВМ, собирает статистику `stats.csv`.
- Обрабатывает результаты (`ovpn/result.py`) и публикует их в Confluence + Zefir/Jira.
