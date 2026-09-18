/**
 * Поллинг терминальных элементов очереди своего отдела для колокола
 * уведомлений в TopBar — расширение того же паттерна, что и
 * `useMyTaskNotifications` (`@/api/server/useMyTaskNotifications`), но
 * источник другой (`testing_service`, а не worker-задачи `server_service`) и
 * область другая: весь отдел текущего пользователя, а не только его
 * собственные запуски. `GET /queue-items` уже сам сужает выдачу до отдела
 * вызывающего через identity (см. `testing_service/src/repositories/queue_item.py::list_for_department`),
 * никакого отдельного backend-эндпоинта для этого заводить не пришлось.
 *
 * Опрашиваем без фильтра по `state`, чтобы видеть переход item'а из
 * нетерминального состояния в терминальное — если запрашивать сразу только
 * `succeeded/failed/skipped`, элемент, который был `running` на прошлом
 * опросе, в выдаче вообще не появится, и переход останется незамеченным.
 * В панель попадают только уже терминальные элементы — очередь отдела и так
 * может быть длинной, а колокол про исходы, не про то, что сейчас крутится.
 *
 * Хранение «виденных» — тот же приём через localStorage, что и у
 * `useMyTaskNotifications`, но под своим ключом (`emm.notifications.testing.*`),
 * чтобы не путать статусы worker-задач и тестовых прогонов одного юзера.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listQueueItems } from "@/api/testing/queueItems";
import type { PublicQueueItem } from "@/api/testing/queueItems";
import { isTerminalQueueItemState } from "@/api/testing/types";
import { useAuthOptional } from "@/contexts/AuthContext";
import { useToastOptional } from "@/contexts/ToastContext";

const POLL_MS = 15_000;
const FETCH_LIMIT = 50;
const STORAGE_PREFIX = "emm.notifications.testing.";

interface SeenRecord {
  status: string;
  read: boolean;
}

type SeenMap = Record<string, SeenRecord>;

export interface TestRunNotification {
  item: PublicQueueItem;
  read: boolean;
}

export interface DepartmentRunNotifications {
  notifications: TestRunNotification[];
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

/** Подпись теста для toast'а: «Тест <code> ...». */
function outcomeMessage(item: PublicQueueItem): string {
  const label = item.test_code ?? item.test_name ?? item.test_id;
  if (item.state === "succeeded") return `Тест ${label} завершён успешно`;
  if (item.state === "failed") return `Тест ${label} завершился с ошибкой`;
  return `Тест ${label} пропущен`;
}

export function useDepartmentRunNotifications(): DepartmentRunNotifications {
  const user = useAuthOptional()?.user ?? null;
  const toast = useToastOptional();
  const userId = user?.user_id ?? null;
  const departmentId = user?.department_id ?? null;

  const [items, setItems] = useState<PublicQueueItem[]>([]);
  const [seen, setSeen] = useState<SeenMap>(() => loadSeen(userId));

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

  useEffect(() => {
    const fresh = loadSeen(userId);
    seenRef.current = fresh;
    setSeen(fresh);
    setItems([]);
  }, [userId]);

  useEffect(() => {
    if (!userId || !departmentId) return;
    let stopped = false;

    const tick = () => {
      listQueueItems({ kind: "all", limit: FETCH_LIMIT, order: "desc" })
        .then((page) => {
          if (stopped || !aliveRef.current) return;
          const rows = page.items;

          const prev = seenRef.current;
          const next: SeenMap = {};
          let changed = false;

          for (const it of rows) {
            const before = prev[it.id];
            const terminal = isTerminalQueueItemState(it.state);
            // Та же логика, что у useMyTaskNotifications: новым терминалом
            // считаем только реальный переход, а не свежезагруженный уже
            // завершённый item (иначе после каждой перезагрузки страницы
            // сыпались бы тосты по всей истории отдела).
            const isNewTerminal =
              terminal && before !== undefined && !isTerminalQueueItemState(before.status);

            if (isNewTerminal) {
              if (it.state === "failed") toast?.error(outcomeMessage(it));
              else if (it.state === "succeeded") toast?.success(outcomeMessage(it));
              else toast?.info(outcomeMessage(it));
            }

            const read = isNewTerminal ? false : (before?.read ?? terminal);
            next[it.id] = { status: it.state, read };
            if (!before || before.status !== it.state || before.read !== read) {
              changed = true;
            }
          }

          if (Object.keys(prev).length !== Object.keys(next).length) {
            changed = true;
          }

          setItems(rows.filter((it) => isTerminalQueueItemState(it.state)));
          if (changed) {
            seenRef.current = next;
            setSeen(next);
            saveSeen(userId, next);
          }
        })
        .catch(() => {
          // Сетевой сбой / отдел без доступа к testing_service — тихо ждём
          // следующий тик.
        });
    };

    tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [userId, departmentId, toast]);

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

  const notifications = useMemo<TestRunNotification[]>(
    () =>
      items.map((item) => ({
        item,
        read: seen[item.id]?.read ?? true,
      })),
    [items, seen],
  );

  const unreadCount = useMemo(
    () => notifications.reduce((acc, n) => acc + (n.read ? 0 : 1), 0),
    [notifications],
  );

  return { notifications, unreadCount, markAllRead, markRead };
}
