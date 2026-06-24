/**
 * Обёртки `server_service` `/console-macros` — кнопки-команды для консоли.
 *
 * Источник истины (backend): server_service console-macros роут. Список
 * `GET /console-macros` отдаёт мои личные макросы плюс системные моего отдела;
 * системные (`is_system: true`) создаёт/правит только dep_admin. Хранение в БД
 * означает, что панель синхронизируется между устройствами — список всегда
 * тянется с сервера.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";

const BASE = "/server/v1";

/**
 * Макрос консоли: именованная команда. `command_text` уходит в терминал как
 * есть (UI добавляет перевод строки при отправке). `display_order` задаёт
 * порядок кнопок в панели. `is_system` — общедепартаментный макрос (виден всем
 * в отделе, правится только dep_admin); личный макрос несёт `user_id` владельца.
 */
export interface ConsoleMacro {
  id: string;
  name: string;
  command_text: string;
  display_order: number;
  is_system: boolean;
  user_id: string | null;
  department_id: string | null;
  created_at?: string;
  updated_at?: string;
}

/** Тело создания макроса. `display_order` опционален — backend проставит хвост. */
export interface ConsoleMacroCreateRequest {
  name: string;
  command_text: string;
  display_order?: number;
  /** Только dep_admin; для обычного пользователя поле игнорируется/запрещено. */
  is_system?: boolean;
}

/** Частичное обновление макроса. */
export interface ConsoleMacroUpdateRequest {
  name?: string;
  command_text?: string;
  display_order?: number;
  is_system?: boolean;
}

/**
 * Список доступных макросов: личные текущего пользователя + системные его
 * отдела. Backend отдаёт плоский массив — сортировку по группам/`display_order`
 * UI делает сам.
 */
export function listConsoleMacros(): Promise<ConsoleMacro[]> {
  return apiGet<ConsoleMacro[]>(`${BASE}/console-macros`);
}

/**
 * Создать макрос. Личный — любой пользователь; системный (`is_system: true`) —
 * только dep_admin (иначе backend ответит 403).
 */
export function createConsoleMacro(
  body: ConsoleMacroCreateRequest,
): Promise<ConsoleMacro> {
  return apiPost<ConsoleMacro>(`${BASE}/console-macros`, body);
}

/** Изменить макрос. Системные правит только dep_admin. */
export function updateConsoleMacro(
  macroId: string,
  body: ConsoleMacroUpdateRequest,
): Promise<ConsoleMacro> {
  return apiPatch<ConsoleMacro>(`${BASE}/console-macros/${macroId}`, body);
}

/** Удалить макрос. Системные удаляет только dep_admin. */
export function deleteConsoleMacro(macroId: string): Promise<void> {
  return apiDelete<void>(`${BASE}/console-macros/${macroId}`);
}
