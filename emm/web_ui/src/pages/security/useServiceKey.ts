import { useCallback, useState } from "react";

/**
 * Введённый админом service-to-service API-ключ для debug-ручек
 * `/authorization/introspect` и `/authorization/service-access`. Эти эндпоинты
 * закрыты `require_service_token`, а не пользовательской сессией — UI обязан
 * предъявить именно `SERVICE_API_KEY`, иначе бэк отвечает 401.
 *
 * Храним в `sessionStorage`, а не в `localStorage`: ключ чувствительный, не
 * должен переживать закрытие вкладки. Между двумя страницами (Introspect и
 * Service-access) значение переиспользуется по общему ключу хранилища.
 */
const STORAGE_KEY = "dbos.auth.serviceKey";
const IDENTITY_KEY = "dbos.auth.serviceIdentity";

function read(key: string): string {
  try {
    return sessionStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function write(key: string, value: string): void {
  try {
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch {
    // приватный режим / отключённый storage — деградируем до in-memory
  }
}

export interface ServiceKeyState {
  serviceKey: string;
  setServiceKey: (v: string) => void;
  serviceIdentity: string;
  setServiceIdentity: (v: string) => void;
}

export function useServiceKey(): ServiceKeyState {
  const [serviceKey, setKeyState] = useState(() => read(STORAGE_KEY));
  const [serviceIdentity, setIdentityState] = useState(() => read(IDENTITY_KEY));

  const setServiceKey = useCallback((v: string) => {
    setKeyState(v);
    write(STORAGE_KEY, v);
  }, []);
  const setServiceIdentity = useCallback((v: string) => {
    setIdentityState(v);
    write(IDENTITY_KEY, v);
  }, []);

  return { serviceKey, setServiceKey, serviceIdentity, setServiceIdentity };
}
