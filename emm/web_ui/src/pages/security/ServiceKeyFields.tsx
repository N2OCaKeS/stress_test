import type { ServiceKeyState } from "./useServiceKey";

/**
 * Поля ввода service-to-service ключа для debug-ручек `/authorization/*`.
 * Эти эндпоинты не принимают пользовательскую сессию — нужен `SERVICE_API_KEY`.
 * `X-Service-Identity` обязателен только когда бэк сконфигурён на per-service
 * ключи (`SERVICE_API_KEYS`); в legacy-режиме (один общий ключ) его можно
 * оставить пустым.
 */
export function ServiceKeyFields({ svc }: { svc: ServiceKeyState }) {
  return (
    <div className="surface border border-token rounded p-3 flex flex-col gap-3">
      <div className="text-[11px] text-dim">
        Ручка закрыта `require_service_token` — предъявите `SERVICE_API_KEY`
        (не пользовательскую сессию). Ключ хранится только в этой вкладке
        (`sessionStorage`).
      </div>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">SERVICE_API_KEY</span>
        <input
          className="input mono"
          type="password"
          value={svc.serviceKey}
          onChange={(e) => svc.setServiceKey(e.target.value)}
          placeholder="Bearer-ключ из SERVICE_API_KEY / SERVICE_API_KEYS"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">
          X-Service-Identity (обязателен при per-service ключах)
        </span>
        <input
          className="input mono"
          value={svc.serviceIdentity}
          onChange={(e) => svc.setServiceIdentity(e.target.value)}
          placeholder="напр. server_service — опц. в legacy-режиме"
        />
      </label>
    </div>
  );
}
