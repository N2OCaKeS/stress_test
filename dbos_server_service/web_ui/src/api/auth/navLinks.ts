/**
 * Настраиваемая кнопка левой панели (по умолчанию «allta»).
 *
 * Источник истины — auth_service:
 *   GET  /api/auth/v1/nav-links          — любой аутентифицированный: кнопки,
 *                                           видимые отделу пользователя.
 *   GET  /api/auth/v1/admin/nav-links    — account_admin: полная конфигурация.
 *   PUT  /api/auth/v1/admin/nav-links    — account_admin: замена конфигурации.
 */

import { apiGet, apiPut } from "@/api/client";

/** Одна видимая кнопка (ответ публичного GET). */
export interface NavLinkItem {
  label: string;
  url: string;
}

/** Полная конфигурация кнопки (админский GET). */
export interface NavLinkConfig {
  enabled: boolean;
  label: string;
  url: string | null;
  all_departments: boolean;
  department_ids: string[];
  updated_at?: string | null;
  updated_by?: string | null;
}

/** Тело PUT — полная замена конфигурации. */
export interface NavLinkConfigUpdate {
  enabled: boolean;
  label: string;
  url: string | null;
  all_departments: boolean;
  department_ids: string[];
}

/** Кнопки, видимые отделу текущего пользователя. Пусто — кнопка не настроена. */
export function getNavLinks(): Promise<NavLinkItem[]> {
  return apiGet<NavLinkItem[]>("/auth/v1/nav-links");
}

/** Полная конфигурация кнопки (нужен account_admin). */
export function getNavLinksConfig(): Promise<NavLinkConfig> {
  return apiGet<NavLinkConfig>("/auth/v1/admin/nav-links");
}

/** Заменить конфигурацию кнопки (нужен account_admin). */
export function updateNavLinksConfig(
  body: NavLinkConfigUpdate,
): Promise<NavLinkConfig> {
  return apiPut<NavLinkConfig>("/auth/v1/admin/nav-links", body);
}
