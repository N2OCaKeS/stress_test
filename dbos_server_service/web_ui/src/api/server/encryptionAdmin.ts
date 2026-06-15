/**
 * Админ-ротация ключа шифрования `server_service` —
 * `/api/server/v1/admin/encryption/*`.
 *
 * Источник истины — `server_service` admin-endpoints (гейт `account_admin`,
 * иначе 403 ACCOUNT_ADMIN_REQUIRED):
 *   - `GET /admin/encryption/migration_status` — прогресс перешифрования.
 *   - `POST /admin/encryption/rotate` тело `{new_key_b64}` — сидит новую
 *     версию ключа активной; идемпотентно по тому же ключу.
 *   - `POST /admin/encryption/retire/{version}` — выводит старую версию;
 *     409 KEYSTORE_CANNOT_RETIRE_ACTIVE / KEYSTORE_VERSION_IN_USE.
 *
 * `new_key_b64` — одноразовый ключ из `auth generateServiceKey()`; не логируем
 * и не держим в state дольше времени запроса.
 */

import { apiGet, apiPost } from "@/api/client";

const BASE = "/server/v1/admin/encryption";

/** Состояние outbox перешифрования (фоновая дорасшифровка legacy-строк). */
export interface ServerEncryptionOutbox {
  pending?: number;
  in_progress?: number;
  failed?: number;
  [k: string]: number | undefined;
}

/** Ответ `GET /migration_status` для `server_service`. */
export interface ServerMigrationStatus {
  remaining: number;
  total: number;
  by_version: Record<string, number>;
  migrated_pct: number;
  active_version: number;
  outbox: ServerEncryptionOutbox;
  remaining_legacy_total: number;
  outbox_pending: number;
}

/** Ответ `POST /rotate`. `seeded` — счётчики засиженных под новую версию строк. */
export interface ServerRotateResponse {
  new_version: number;
  previous_version: number;
  seeded: Record<string, number>;
  idempotent: boolean;
}

/** Ответ `POST /retire/{version}`. */
export interface ServerRetireResponse {
  version: number;
  retired: boolean;
  remaining_on_version: number;
}

export function getMigrationStatus(): Promise<ServerMigrationStatus> {
  return apiGet<ServerMigrationStatus>(`${BASE}/migration_status`);
}

export function rotate(newKeyB64: string): Promise<ServerRotateResponse> {
  return apiPost<ServerRotateResponse>(`${BASE}/rotate`, {
    new_key_b64: newKeyB64,
  });
}

export function retire(version: number): Promise<ServerRetireResponse> {
  return apiPost<ServerRetireResponse>(`${BASE}/retire/${version}`, {});
}
