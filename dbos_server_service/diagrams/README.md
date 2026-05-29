# Диаграммы DBOS Server Manager

Файлы открываются в [draw.io desktop](https://github.com/jgraph/drawio-desktop/releases) или плагином `hediet.vscode-drawio` в VS Code (в devcontainer уже есть). Online — `https://app.diagrams.net/` → `File → Open from → Device`.

| Файл | Содержимое |
|---|---|
| [01_permissions.drawio](01_permissions.drawio) | Модель прав: платформенный уровень (account_admin / department_admin / DepartmentServiceAccess), сервис-уровень (guest/reader/operator/admin + кастомные роли отдела), action-based матрица, идентичности (User/Group/Bot/PAT/SERVICE_API_KEY), поток выдачи прав, каскадная деактивация. |
| [02_services.drawio](02_services.drawio) | Карта взаимодействия: клиенты (cli, web_settings, curl, bot) → backend (auth_service, loging_service, server_service, server_worker, config_service) → хранилища (4 PostgreSQL + Redis) → внешние системы (iDRAC, SSH). Жирные стрелки — синхронный HTTP, пунктир — очередь/событие. |
| [03_per_service.drawio](03_per_service.drawio) | Внутреннее устройство каждого сервиса в отдельных вкладках: `auth_service`, `loging_service`, `server_service`, `server_worker`, `config / web / cli (план)`. Слои API → services → DB, ключевые сущности, инварианты. |
