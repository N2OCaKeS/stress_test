# web_settings

## Назначение

`web_settings` — web-приложение для централизованного администрирования платформы через GUI.

Основная идея интерфейса:

- дать единое окно управления системой;
- визуализировать дерево прав и ролей;
- упростить настройку сервисов без необходимости работать напрямую с API;
- не раскрывать чувствительные данные шире, чем это разрешено правами пользователя.

## Основные возможности

- просмотр и настройка прав доступа;
- визуализация дерева ролей, сервисов и прав;
- просмотр документации сервисов;
- просмотр логов;
- работа с ботами и сервисными учётными записями;
- создание и настройка сущностей внутри прикладных сервисов;
- быстрый переход к операциям администрирования по отделу и сервису.

## Пример сценария

Администратор `config_service` должен иметь возможность:

- увидеть, какие права существуют для его сервиса;
- увидеть, у каких токенов какие права настроены;
- быстро добавить новый токен;
- донастроить, какие роли могут его читать и изменять;
- создать или обновить нужные права через `auth_service` в рамках своего отдела.

## Границы ответственности

`web_settings` не должен становиться отдельным источником истины по ролям, токенам, логам или серверам.

Web-интерфейс отвечает за:

- визуализацию данных из backend-сервисов;
- удобное редактирование разрешённых сущностей;
- работу пользователя через GUI;
- безопасное отображение данных в соответствии с правами.

Web-интерфейс не отвечает за:

- самостоятельное хранение ролей и политик доступа;
- принятие финального решения о доступе;
- хранение аудита как основного хранилища.

## Модель доступа

`web_settings` использует ту же модель доступа, что и backend-сервисы:

- роли пользователя определяются в `auth_service`;
- доступ к сервисам определяется отделом;
- внутри каждого сервиса действуют собственные матрицы прав;
- сам `web_settings` не создаёт роли напрямую, а вызывает соответствующие backend API.

Интерфейс должен уметь корректно скрывать:

- недоступные сервисы;
- запрещённые операции;
- чувствительные поля и секреты;
- административные элементы, если у пользователя нет прав.

## Интеграции

- `auth_service` — аутентификация, роли, управление пользователями и ботами;
- `config_service` — токены, секреты и матрицы доступа к ним;
- `logging_service` — просмотр аудита;
- `server_service` — просмотр и управление серверами.

Все операции выполняются через внешние API backend-сервисов.

## Технологии

- `React`
- `Docker`
- `Kubernetes`

---

# Dev workflow (Phase 2 React app)

Этот каталог теперь содержит как `mockups/` (45 статических HTML), так и
production React-приложение (Vite + React 19 + TypeScript + Tailwind +
Radix + Lucide), которое последовательно портирует все 45 mockup-страниц.

## Стек

- **Vite 6** + **React 19** + **TypeScript 5.7** (strict)
- **Tailwind 3.4** + CSS-variables (`var(--bg)`, `var(--accent)`, …) — 4 темы
- **Radix UI** (dialog, dropdown, select, tabs, popover, toast)
- **Lucide React** — иконки
- **react-router-dom 7**
- **Vitest** + **@testing-library/react** — unit-тесты

## Команды

```bash
cd web_ui
npm install        # ставит зависимости (Phase 2 — впервые)
npm run dev        # vite dev server на :5173 (фоновый mockups :8765 не трогать)
npm run typecheck  # tsc -b --noEmit
npm run lint
npm run test       # vitest run
npm run build      # tsc -b && vite build → dist/
```

## Структура

```
web_ui/
├── mockups/            ← 45 готовых HTML, эталон для порта (НЕ трогать)
├── index.html          ← Vite entry
├── package.json
├── vite.config.ts
├── tailwind.config.ts
├── postcss.config.js
├── tsconfig*.json
├── vitest.config.ts
├── eslint.config.js
└── src/
    ├── main.tsx              ← ReactDOM.createRoot
    ├── App.tsx               ← <BrowserRouter> + <ThemeProvider> + <PersonaProvider>
    ├── App.css               ← imports themes.css + tailwind directives
    ├── vite-env.d.ts
    ├── styles/
    │   └── themes.css        ← 4 темы (vscode-dark/light, dark-orange, blue) + reusable .surface/.card/.chip
    ├── contexts/
    │   ├── ThemeContext.tsx  ← useTheme(), persist в localStorage["dbos-theme"]
    │   └── PersonaContext.tsx← usePersona(), persist в localStorage["dbos-persona"]
    ├── types/persona.ts      ← Persona, ServiceName, ServiceRole, ThemeName
    ├── mocks/                ← статические данные, потом заменим на API
    │   ├── personas.ts       ← 6 персон с RBAC-объектом
    │   ├── auth.ts           ← 47 users, 4 depts
    │   ├── secret.ts         ← 70 credentials
    │   ├── server.ts         ← 90 servers
    │   ├── worker.ts         ← 50 tasks + 12 DLQ
    │   └── log.ts            ← 200 audit events
    ├── components/
    │   ├── shell/
    │   │   ├── Shell.tsx          ← TopBar + Left + (optional) Middle + main
    │   │   ├── TopBar.tsx         ← logo + breadcrumb + persona + theme + logout
    │   │   ├── LeftPanel.tsx      ← service chips, фильтруется по persona.accessible_services
    │   │   ├── MiddlePanel.tsx    ← generic list + search + group selector
    │   │   ├── WorkzonePanel.tsx  ← generic правая колонка с tabs
    │   │   └── ThemeSwitcher.tsx
    │   └── ui/               ← (Radix-обёртки добавляются по мере необходимости)
    └── pages/
        ├── auth/
        │   ├── PersonaSelector.tsx  ← `/` mockup-режим
        │   └── Login.tsx            ← `/login` stub
        ├── home/Home.tsx            ← reference-страница (порт home-dep_admin.html)
        ├── Placeholder.tsx          ← stub для непереведённых страниц
        └── NotFound.tsx             ← `/404`
```

## Конвенция порта

Каждая страница в `mockups/` соответствует одному React-компоненту в
`src/pages/<service>/<Page>-<persona>.tsx`. Reference-паттерн закреплён в
`src/pages/home/Home.tsx`:

1. Импортирует `Shell` + (опционально) `MiddlePanel`/`WorkzonePanel`.
2. Берёт текущую персону через `usePersona()`.
3. Mock-данные — из `src/mocks/*` (фильтрует по dept персоны).
4. Стили — Tailwind classes + классы из `themes.css` (.card, .chip, .badge…).
5. Иконки — `lucide-react` (заменяет `<i data-lucide="...">` из mockup-ов).
6. Ссылки — `react-router-dom` `<Link>` вместо `<a href="*.html">`.

Сличение «1-в-1»: открывай рядом mockup на `http://localhost:8765/<page>.html`
и React-страницу на `http://localhost:5173/<route>`.

## Порт-чеклист (44 страницы осталось)

Готово (1):
- [x] `home-dep_admin.html` → `src/pages/home/Home.tsx`

Phase 2 next batch — home-* (5):
- [ ] `home-account_admin.html` (bob)
- [ ] `home-logging_admin.html` (carol)
- [ ] `home-logging_reader.html` (dave)
- [ ] `home-secret_admin.html` (eve)
- [ ] `home-multi_admin.html` (frank)

Server (3): `server-{account,dep,multi}_admin.html`
Secret (4): `secret-{account,dep,secret,multi}_admin.html`
Users (2): `users-{account,dep}_admin.html`
Departments (1): `departments-account_admin.html`
Logs (2): `log-logging_{admin,reader}.html`
Worker tasks (3): `worker-account_admin.html`, `tasks-{dep,multi}_admin.html`
Admin (5): `admin-{account,dep,logging,secret,multi}_admin.html`
Bots (3): `bots-{account,dep,multi}_admin.html`
Service-roles (4): `service_roles-{account,dep,secret,multi}_admin.html`
Rules (1): `rules-logging_admin.html`
Retention (1): `retention-logging_admin.html`
DLQ (3): `dlq-{account,dep,multi}_admin.html`
System (5): `login.html`, `404.html`, `settings-account_admin.html`,
            `wizard-create-credential.html`, `wizard-rotation.html`
Patterns (1): `patterns.html` (UI-демо, не страница)

Итого по mockups/: 45 — 1 (Home) = 44.

## Темы

CSS-variables держатся в `src/styles/themes.css`. Tailwind мапит их через
`tailwind.config.ts` (`colors: { bg: 'var(--bg)', accent: 'var(--accent)', ... }`),
так что `bg-accent`, `text-dim`, `border-token`, `text-ok` — обычные tailwind-классы.
Переключение тем — через `useTheme()`; провайдер выставляет `data-theme`
на `<html>` и сохраняет в `localStorage["dbos-theme"]`.

## Персоны

В Phase 2 — статические в `src/mocks/personas.ts`. В Phase 3 `PersonaProvider`
будет читать `/me` от `auth_service` и контекст превратится в реальный
authenticated-state.

