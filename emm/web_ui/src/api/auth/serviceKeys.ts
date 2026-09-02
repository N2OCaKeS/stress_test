/**
 * Генерация сервисных ключей шифрования — `/api/auth/v1/admin/service-keys`.
 *
 * Источник истины — `auth_service` admin-endpoint `POST /admin/service-keys/generate`
 * (гейт `account_admin`). Тело запроса пустое; ответ несёт свежий случайный
 * ключ AES-256-GCM в base64. Ключ показывается ровно один раз и нигде на
 * стороне auth_service не хранится — caller обязан сразу передать его в
 * `rotate` нужного сервиса и не держать в памяти дольше необходимого.
 */

import { apiPost } from "@/api/client";

/** Ответ `/admin/service-keys/generate`. `key_b64` — одноразовый, не логировать. */
export interface ServiceKeyGenerateResponse {
  key_b64: string;
  key_bytes: number;
  algorithm: string;
}

export function generateServiceKey(): Promise<ServiceKeyGenerateResponse> {
  return apiPost<ServiceKeyGenerateResponse>(
    "/auth/v1/admin/service-keys/generate",
    {},
  );
}
