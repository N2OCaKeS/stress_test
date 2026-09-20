/**
 * Общая основа колокольных поллеров: периодически тянем список сущностей,
 * ловим переход в терминальный статус и держим «прочитано / не прочитано» в
 * localStorage под ключом конкретного пользователя.
 *
 * Конкретные хуки (`useMyTaskNotifications`, `useDepartmentRunNotifications`)
 * только описывают источник данных, признак терминальности и текст toast'а.
 *
 * Новым терминалом считаем только реальный переход: сущность уже была в карте
 * с нетерминальным статусом. Сразу-терминальную сущность из первого опроса
 * после перезагрузки страницы новой не считаем — иначе на каждый F5 сыпались
 * бы toast'ы по всей истории.
 *
 * Карта «виденных» не растёт бесконечно: запись хранит время последней
 * встречи (`seenAt`), записи старше `SEEN_TTL_MS` выбрасываются при чтении и
 * при записи, а размер карты ограничен `SEEN_MAX_ENTRIES` (остаются самые
 * свежие). Пустая карта удаляет ключ из localStorage целиком.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useToastOptional } from "@/contexts/ToastContext";

/** Сколько хранить запись о сущности, которую давно не встречали. */
export const SEEN_TTL_MS = 30 * 24 * 60 * 60 * 1000;
/** Верхняя граница размера карты на пользователя. */
export const SEEN_MAX_ENTRIES = 200;
/** Не перезаписываем `seenAt` чаще раза в сутки, чтобы не писать в storage на каждый тик. */
const TOUCH_MS = 24 * 60 * 60 * 1000;

/** Что про каждую сущность нужно помнить между опросами. */
export interface SeenRecord {
  /** Последний статус, в котором мы видели сущность (для детекта перехода). */
  status: string;
  /** Прочитана ли пользователем. */
  read: boolean;
  /** Когда (epoch ms) сущность последний раз попадала в выдачу. */
  seenAt: number;
}

export type SeenMap = Record<string, SeenRecord>;

/** Выбрасывает просроченные записи и режет карту до `SEEN_MAX_ENTRIES` самых свежих. */
export function pruneSeen(seen: SeenMap, now: number = Date.now()): SeenMap {
  const alive = Object.entries(seen).filter(([, rec]) => now - rec.seenAt <= SEEN_TTL_MS);
  if (alive.length > SEEN_MAX_ENTRIES) {
    alive.sort((a, b) => b[1].seenAt - a[1].seenAt);
    alive.length = SEEN_MAX_ENTRIES;
  }
  return alive.length === Object.keys(seen).length ? seen : Object.fromEntries(alive);
}

export function loadSeen(key: string | null, now: number = Date.now()): SeenMap {
  if (!key || typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    const seen: SeenMap = {};
    for (const [id, rec] of Object.entries(parsed as Record<string, Partial<SeenRecord>>)) {
      if (!rec || typeof rec.status !== "string") continue;
      // Записи без seenAt остались от прежнего формата — считаем свежими.
      seen[id] = { status: rec.status, read: rec.read === true, seenAt: rec.seenAt ?? now };
    }
    return pruneSeen(seen, now);
  } catch {
    // битый/чужой JSON — начинаем с чистого листа, не роняем колокол
    return {};
  }
}

export function saveSeen(key: string | null, seen: SeenMap): void {
  if (!key || typeof window === "undefined") return;
  try {
    const kept = pruneSeen(seen);
    if (Object.keys(kept).length === 0) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(kept));
  } catch {
    // localStorage недоступен (private mode / квота) — переживём без персиста
  }
}

export interface TerminalNotificationsOptions<T> {
  /** Текущий пользователь; без него ничего не опрашиваем и не читаем из storage. */
  userId: string | null;
  /** Дополнительный гейт опроса (например, наличие отдела). */
  enabled?: boolean;
  /** Префикс ключа localStorage; к нему дописывается `userId`. */
  storagePrefix: string;
  pollMs: number;
  fetchItems: (userId: string) => Promise<T[]>;
  getId: (item: T) => string;
  getStatus: (item: T) => string;
  isTerminal: (status: string) => boolean;
  /** Текст toast'а по сущности, только что перешедшей в терминал. */
  buildMessage: (item: T) => string;
  /** Какие из полученных сущностей показывать в списке (по умолчанию все). */
  isVisible?: (item: T) => boolean;
}

export interface TerminalNotificationRow<T> {
  item: T;
  read: boolean;
}

export interface TerminalNotifications<T> {
  rows: TerminalNotificationRow<T>[];
  unreadCount: number;
  markAllRead: () => void;
  markRead: (id: string) => void;
}

export function useTerminalNotifications<T>(
  options: TerminalNotificationsOptions<T>,
): TerminalNotifications<T> {
  const { userId, enabled = true, storagePrefix, pollMs } = options;
  const toast = useToastOptional();
  const storageKey = userId ? `${storagePrefix}${userId}` : null;

  // Колбэки источника меняются каждый рендер (инлайн-функции обёрток); читаем
  // их через ref, чтобы не пересоздавать интервал поллинга.
  const optsRef = useRef(options);
  useEffect(() => {
    optsRef.current = options;
  });

  const [items, setItems] = useState<T[]>([]);
  // Зеркало seen-карты в state — чтобы перерисовывать бейдж при mark*.
  const [seen, setSeen] = useState<SeenMap>(() => loadSeen(storageKey));

  // Держим актуальный seen в ref, чтобы tick поллинга читал свежую карту, не
  // пересоздавая интервал на каждый mark.
  const seenRef = useRef<SeenMap>(seen);
  useEffect(() => {
    seenRef.current = seen;
  }, [seen]);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  // Смена пользователя (логин/логаут под другим) — перечитываем его карту и
  // сбрасываем список, чтобы не показать сущности прошлой сессии.
  useEffect(() => {
    const fresh = loadSeen(storageKey);
    seenRef.current = fresh;
    setSeen(fresh);
    setItems([]);
  }, [storageKey]);

  useEffect(() => {
    if (!userId || !enabled) return;
    let stopped = false;

    const tick = () => {
      const o = optsRef.current;
      o.fetchItems(userId)
        .then((rows) => {
          if (stopped || !aliveRef.current) return;

          const now = Date.now();
          const prev = seenRef.current;
          const next: SeenMap = {};
          let changed = false;

          for (const it of rows) {
            const id = o.getId(it);
            const status = o.getStatus(it);
            const before = prev[id];
            const terminal = o.isTerminal(status);
            const isNewTerminal =
              terminal && before !== undefined && !o.isTerminal(before.status);

            if (isNewTerminal) {
              const text = o.buildMessage(it);
              if (status === "failed") toast?.error(text);
              else if (status === "succeeded") toast?.success(text);
              else toast?.info(text);
            }

            const read = isNewTerminal ? false : (before?.read ?? terminal);
            const stale = before !== undefined && now - before.seenAt > TOUCH_MS;
            next[id] = { status, read, seenAt: !before || stale ? now : before.seenAt };
            if (!before || before.status !== status || before.read !== read || stale) {
              changed = true;
            }
          }

          if (Object.keys(prev).length !== Object.keys(next).length) {
            changed = true;
          }

          const isVisible = o.isVisible;
          setItems(isVisible ? rows.filter(isVisible) : rows);
          if (changed) {
            seenRef.current = next;
            setSeen(next);
            saveSeen(storageKey, next);
          }
        })
        .catch(() => {
          // Сетевой сбой / 403 (нет доступа к источнику) — тихо ждём
          // следующий тик, колокол просто не обновится.
        });
    };

    tick();
    const id = window.setInterval(tick, pollMs);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [userId, enabled, storageKey, pollMs, toast]);

  const markRead = useCallback(
    (id: string) => {
      setSeen((prev) => {
        const rec = prev[id];
        if (!rec || rec.read) return prev;
        const next = { ...prev, [id]: { ...rec, read: true } };
        seenRef.current = next;
        saveSeen(storageKey, next);
        return next;
      });
    },
    [storageKey],
  );

  const markAllRead = useCallback(() => {
    setSeen((prev) => {
      let touched = false;
      const next: SeenMap = {};
      for (const [id, rec] of Object.entries(prev)) {
        next[id] = rec.read ? rec : { ...rec, read: true };
        if (!rec.read) touched = true;
      }
      if (!touched) return prev;
      seenRef.current = next;
      saveSeen(storageKey, next);
      return next;
    });
  }, [storageKey]);

  const rows = useMemo<TerminalNotificationRow<T>[]>(
    () =>
      items.map((item) => ({
        item,
        read: seen[optsRef.current.getId(item)]?.read ?? true,
      })),
    [items, seen],
  );

  const unreadCount = useMemo(
    () => rows.reduce((acc, r) => acc + (r.read ? 0 : 1), 0),
    [rows],
  );

  return { rows, unreadCount, markAllRead, markRead };
}
