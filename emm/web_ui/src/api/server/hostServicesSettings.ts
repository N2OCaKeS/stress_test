/**
 * Настройки SSH-доступа к хосту, на котором крутятся ALLTA-сервисы
 * (systemd-юниты, управляемые со страницы «Здоровье служб»).
 *
 * Платформенный singleton: host/port/user + приватный ключ. Ключ write-only —
 * GET отдаёт только факт `private_key_is_set`, само значение никогда не
 * возвращается (тот же паттерн, что у пароля ACS в `acsSettings.ts`).
 *
 * Источник истины — `server_service`:
 *   GET/PUT /api/server/v1/settings/host-services
 * Гейтится account_admin; остальным backend ответит 403.
 */

import { apiGet, apiPut } from "@/api/client";
import type {
  HostServicesSettings,
  HostServicesSettingsUpdate,
} from "@/api/server/types";

const BASE = "/server/v1";

/** Прочитать текущие настройки SSH-доступа к хосту. 403 — нет account_admin. */
export function getHostServicesSettings(): Promise<HostServicesSettings> {
  return apiGet<HostServicesSettings>(`${BASE}/settings/host-services`);
}

/** Обновить настройки SSH-доступа (частичное обновление). */
export function updateHostServicesSettings(
  body: HostServicesSettingsUpdate,
): Promise<HostServicesSettings> {
  return apiPut<HostServicesSettings>(`${BASE}/settings/host-services`, body);
}
