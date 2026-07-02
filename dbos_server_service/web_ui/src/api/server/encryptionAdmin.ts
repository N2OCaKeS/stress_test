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

/**
 * Режим перешифровки при ротации ключа:
 *   - `lazy` (по умолчанию) — новая версия активируется сразу, старые строки
 *     дошифровываются в фоне; сервис продолжает работать, строки читаются и
 *     старым, и новым ключом;
 *   - `force` — экстренная полная перешифровка; сервис блокирует все запросы
 *     (503 REENCRYPT_IN_PROGRESS), кроме статуса/health, до завершения.
 */
export type RotateMode = "lazy" | "force";

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

export function rotate(
  newKeyB64: string,
  mode: RotateMode = "lazy",
): Promise<ServerRotateResponse> {
  return apiPost<ServerRotateResponse>(`${BASE}/rotate`, {
    new_key_b64: newKeyB64,
    mode,
  });
}

export function retire(version: number): Promise<ServerRetireResponse> {
  return apiPost<ServerRetireResponse>(`${BASE}/retire/${version}`, {});
}
