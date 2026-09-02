/**
 * Админ-ротация ключа шифрования `secret_service` —
 * `/api/secret/v1/admin/encryption/*`.
 *
 * Зеркалит `server/encryptionAdmin`, но `migration_status` несёт чуть иной
 * набор полей (`active_version`, `total_rows`, `by_version`, `remaining_legacy`,
 * `migrated_pct`, `outbox_pending_count`). Гейт — `account_admin`, иначе 403
 * ACCOUNT_ADMIN_REQUIRED. `rotate` / `retire` — те же контракты, что у server.
 *
 * `new_key_b64` — одноразовый ключ из `auth generateServiceKey()`; не логируем
 * и не держим в state дольше времени запроса.
 */

import { apiGet, apiPost } from "@/api/client";

const BASE = "/secret/v1/admin/encryption";

/** Режим перешифровки — см. `server/encryptionAdmin`. */
export type RotateMode = "lazy" | "force";

/** Ответ `GET /migration_status` для `secret_service`. */
export interface SecretMigrationStatus {
  active_version: number;
  total_rows: number;
  by_version: Record<string, number>;
  remaining_legacy: number;
  migrated_pct: number;
  outbox_pending_count: number;
  /** Версии ключа в keystore (список либо просто их число). */
  versions_in_keystore?: number[] | number;
  /** Текущий режим перешифровки. */
  mode?: RotateMode | string;
  /** Активен ли force-режим (сервис блокирует запросы). */
  force_active?: boolean;
  /** Оценка времени до завершения, сек. */
  eta_seconds?: number | null;
  /** Скорость дошифровки, строк/сек. */
  throughput?: number | null;
}

/** Ответ `POST /rotate`. */
export interface SecretRotateResponse {
  new_version: number;
  previous_version: number;
  seeded: Record<string, number>;
  idempotent: boolean;
}

/** Ответ `POST /retire/{version}`. */
export interface SecretRetireResponse {
  version: number;
  retired: boolean;
  remaining_on_version: number;
}

export function getMigrationStatus(): Promise<SecretMigrationStatus> {
  return apiGet<SecretMigrationStatus>(`${BASE}/migration_status`);
}

export function rotate(
  newKeyB64: string,
  mode: RotateMode = "lazy",
): Promise<SecretRotateResponse> {
  return apiPost<SecretRotateResponse>(`${BASE}/rotate`, {
    new_key_b64: newKeyB64,
    mode,
  });
}

export function retire(version: number): Promise<SecretRetireResponse> {
  return apiPost<SecretRetireResponse>(`${BASE}/retire/${version}`, {});
}
