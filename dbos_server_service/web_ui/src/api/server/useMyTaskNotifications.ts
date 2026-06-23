/**
 * Поллинг «моих» worker-task'ей для колокола уведомлений в TopBar.
 *
 * Опрашивает `GET /tasks?created_by=<me>` раз в ~10 секунд — колокол показывает
 * строго мои задачи при любой роли (фильтр пересекается с role-scope на backend,
 * видимость не расширяет). Все задачи отдела админ смотрит отдельно — в панели
 * на главной.
 *
 * Когда задача впервые приходит в терминальном статусе (succeeded/failed/
 * cancelled), которого мы по ней ещё не видели, показываем toast и помечаем
 * запись непрочитанной. Прочитанность и последний виденный статус храним в
 * localStorage под ключом конкретного пользователя — переживает перезагрузку и
 * не течёт между разными залогиненными.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listTasks } from "@/api/server/misc";
import { isTerminalTaskStatus } from "@/api/server/types";
import type { TaskRead } from "@/api/server/types";
import { useAuthOptional } from "@/contexts/AuthContext";
import { useToastOptional } from "@/contexts/ToastContext";

const POLL_MS = 10_000;
const FETCH_LIMIT = 20;
const STORAGE_PREFIX = "emm.notifications.";

/** Что про каждую задачу нужно помнить между опросами. */
interface SeenRecord {
  /** Последний статус, в котором мы видели задачу (для детекта перехода). */
  status: string;
  /** Прочитана ли пользователем. */
  read: boolean;
}

type SeenMap = Record<string, SeenRecord>;

/** Одна строка в центре уведомлений. */
export interface TaskNotification {
  task: TaskRead;
  read: boolean;
}

export interface MyTaskNotifications {
  notifications: TaskNotification[];
  unreadCount: number;
  markAllRead: () => void;
  markRead: (id: string) => void;
}

function storageKey(userId: string): string {
  return `${STORAGE_PREFIX}${userId}`;
}

function loadSeen(userId: string | null): SeenMap {
  if (!userId || typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object") return parsed as SeenMap;
  } catch {
    // битый/чужой JSON — начинаем с чистого листа, не роняем колокол
  }
  return {};
}

function saveSeen(userId: string | null, seen: SeenMap): void {
  if (!userId || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey(userId), JSON.stringify(seen));
  } catch {
    // localStorage недоступен (private mode / квота) — переживём без персиста
  }
}

/** Подпись задачи для toast'а: «<kind> завершена ...». */
function outcomeMessage(task: TaskRead): string {
  if (task.status === "succeeded") {
    return `Задача ${task.kind} завершена успешно`;
  }
  if (task.status === "failed") {
    return `Задача ${task.kind} завершилась с ошибкой`;
  }
  return `Задача ${task.kind} отменена`;
}

export function useMyTaskNotifications(): MyTaskNotifications {
  const user = useAuthOptional()?.user ?? null;
  const toast = useToastOptional();
  const userId = user?.user_id ?? null;

  const [tasks, setTasks] = useState<TaskRead[]>([]);
  // Зеркало seen-карты в state — чтобы перерисовывать бейдж при mark*.
  const [seen, setSeen] = useState<SeenMap>(() => loadSeen(userId));

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
  // сбрасываем список, чтобы не показать задачи прошлой сессии.
  useEffect(() => {
    const fresh = loadSeen(userId);
    seenRef.current = fresh;
    setSeen(fresh);
    setTasks([]);
  }, [userId]);

  useEffect(() => {
    if (!userId) return;
    let stopped = false;

    const tick = () => {
      listTasks({ limit: FETCH_LIMIT, created_by: userId })
        .then((page) => {
          if (stopped || !aliveRef.current) return;
          const mine = page.items;

          const prev = seenRef.current;
          const next: SeenMap = {};
          let changed = false;

          for (const t of mine) {
            const before = prev[t.id];
            const terminal = isTerminalTaskStatus(t.status);
            // Новый терминал = задачи раньше не было ЛИБО её прошлый статус был
            // нетерминальным. Свежезагруженную сразу-терминальную задачу
            // (первый опрос после перезагрузки) новой не считаем.
            const isNewTerminal =
              terminal &&
              before !== undefined &&
              !isTerminalTaskStatus(before.status);

            if (isNewTerminal) {
              if (t.status === "failed") toast?.error(outcomeMessage(t));
              else if (t.status === "succeeded") toast?.success(outcomeMessage(t));
              else toast?.info(outcomeMessage(t));
            }

            const read = isNewTerminal ? false : (before?.read ?? terminal);
            next[t.id] = { status: t.status, read };
            if (!before || before.status !== t.status || before.read !== read) {
              changed = true;
            }
          }

          if (Object.keys(prev).length !== Object.keys(next).length) {
            changed = true;
          }

          setTasks(mine);
          if (changed) {
            seenRef.current = next;
            setSeen(next);
            saveSeen(userId, next);
          }
        })
        .catch(() => {
          // Сетевой сбой / 403 (роль без доступа к task-зоне) — тихо ждём
          // следующий тик, колокол просто не обновится.
        });
    };

    tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [userId, toast]);

  const markRead = useCallback(
    (id: string) => {
      setSeen((prev) => {
        const rec = prev[id];
        if (!rec || rec.read) return prev;
        const next = { ...prev, [id]: { ...rec, read: true } };
        seenRef.current = next;
        saveSeen(userId, next);
        return next;
      });
    },
    [userId],
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
      saveSeen(userId, next);
      return next;
    });
  }, [userId]);

  const notifications = useMemo<TaskNotification[]>(
    () =>
      tasks.map((task) => ({
        task,
        read: seen[task.id]?.read ?? true,
      })),
    [tasks, seen],
  );

  const unreadCount = useMemo(
    () => notifications.reduce((acc, n) => acc + (n.read ? 0 : 1), 0),
    [notifications],
  );

  return { notifications, unreadCount, markAllRead, markRead };
}
