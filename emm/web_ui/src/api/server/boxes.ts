/**
 * Thin wrappers для `server_service` `/boxes/*` endpoints — реестр боксов
 * (образов гостевых ВМ) отдела.
 *
 * Бокс описывает, откуда качать образ (`download_url`), его формат
 * (`format`: tar/qcow/raw…), базовую учётку внутри образа
 * (`base_user_login` + пароль) и какие версии ОС / стартовые снимки он несёт.
 * Реестр dept-scoped: каждый отдел ведёт свой каталог, гейт — department-роль
 * (`box.view/create/update/delete`, пароль — `box.view_password`).
 *
 * Пароль базовой учётки на вход передаётся base64 (`base_user_password_b64`),
 * как у server_account; в списке/карточке он наружу не отдаётся, кроме reveal'а
 * держателю `view_password` (тогда `base_user_password_b64` приходит в ответе,
 * его надо декодировать `lib/base64::fromBase64`).
 *
 * Source of truth:
 *   server_service/src/api/v1/endpoints/boxes.py
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type { Iso8601, OffsetPaginatedResponse } from "@/api/server/types";
import { toBase64 } from "@/lib/base64";

const BASE = "/server/v1";

// ── типы ────────────────────────────────────────────────────────────────────

/**
 * Запись реестра боксов (ответ GET/POST /boxes). `base_user_password_b64`
 * приходит только держателю `view_password` при reveal'е (base64(plaintext)),
 * в обычном списке — `null`/отсутствует.
 */
export interface Box {
  id: string;
  name: string;
  /** Формат образа: `tar` / `qcow` / `raw` и т.п. */
  format: string;
  /** Откуда качать образ (https/ftp/smb/http). */
  download_url: string;
  /** Логин базовой учётки внутри образа. */
  base_user_login: string;
  /** Версии ОС, которые несёт бокс. */
  os_versions: string[];
  /** Имена стартовых снимков образа. */
  initial_snapshots: string[];
  department_id: string;
  /** base64(plaintext) пароля базовой учётки — только на reveal. null иначе. */
  base_user_password_b64?: string | null;
  created_at: Iso8601;
  updated_at: Iso8601;
  created_by?: string | null;
}

/** Тело `POST /boxes` (wire-формат: пароль уже base64). */
export interface BoxCreateRequest {
  name: string;
  format: string;
  download_url: string;
  base_user_login: string;
  base_user_password_b64?: string;
  os_versions?: string[];
  initial_snapshots?: string[];
  department_id: string;
}

/**
 * Вход `createBox` с plaintext-паролем базовой учётки. Кодирование в
 * `base_user_password_b64` делает сам wrapper — формы передают сырой пароль.
 */
export type BoxCreateInput = Omit<BoxCreateRequest, "base_user_password_b64"> & {
  /** Plaintext пароля базовой учётки. Пусто/`null` — не задавать. */
  password?: string | null;
};

/** Тело `PATCH /boxes/{id}` — частичное обновление (wire-формат). */
export interface BoxUpdateRequest {
  name?: string;
  format?: string;
  download_url?: string;
  base_user_login?: string;
  base_user_password_b64?: string;
  os_versions?: string[];
  initial_snapshots?: string[];
}

/**
 * Вход `updateBox` с plaintext-паролем. Поле `password` кодируется в
 * `base_user_password_b64`; остальные поля идут как есть.
 */
export type BoxUpdateInput = Omit<BoxUpdateRequest, "base_user_password_b64"> & {
  /** Plaintext нового пароля. Не задан — пароль не меняется. */
  password?: string | null;
};

/** Параметры фильтрации `GET /boxes`. */
export interface ListBoxesQuery {
  /** Отдел-владелец. Опущен — отдел вызывающего (backend решает по контексту). */
  department_id?: string;
  limit?: number;
  offset?: number;
}

// ── client ──────────────────────────────────────────────────────────────────

/** `GET /api/server/v1/boxes` — страница реестра боксов (offset envelope). */
export function listBoxes(
  query: ListBoxesQuery = {},
): Promise<OffsetPaginatedResponse<Box>> {
  return apiGet<OffsetPaginatedResponse<Box>>(`${BASE}/boxes`, {
    query: {
      department_id: query.department_id,
      limit: query.limit,
      offset: query.offset,
    },
  });
}

/**
 * `GET /api/server/v1/boxes/{id}` — карточка бокса. Держателю `view_password`
 * backend доносит `base_user_password_b64` (base64(plaintext)) — для reveal'а.
 */
export function getBox(id: string): Promise<Box> {
  return apiGet<Box>(`${BASE}/boxes/${id}`);
}

/**
 * `POST /api/server/v1/boxes` — регистрация нового бокса. Plaintext-пароль
 * базовой учётки шифруется at-rest; наружу в ответе не возвращается.
 */
export function createBox(input: BoxCreateInput): Promise<Box> {
  const { password, ...rest } = input;
  const body: BoxCreateRequest = { ...rest };
  if (password) body.base_user_password_b64 = toBase64(password);
  return apiPost<Box>(`${BASE}/boxes`, body);
}

/** `PATCH /api/server/v1/boxes/{id}` — частичное обновление записи. */
export function updateBox(id: string, input: BoxUpdateInput): Promise<Box> {
  const { password, ...rest } = input;
  const body: BoxUpdateRequest = { ...rest };
  if (password) body.base_user_password_b64 = toBase64(password);
  return apiPatch<Box>(`${BASE}/boxes/${id}`, body);
}

/** `DELETE /api/server/v1/boxes/{id}` — удаление записи реестра. */
export function deleteBox(id: string): Promise<void> {
  return apiDelete<void>(`${BASE}/boxes/${id}`);
}
