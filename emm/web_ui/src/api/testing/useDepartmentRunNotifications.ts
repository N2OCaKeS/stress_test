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
 * Механика поллинга и хранения — в `useTerminalNotifications`.
 */
import { useMemo } from "react";
import { listQueueItems } from "@/api/testing/queueItems";
import type { PublicQueueItem } from "@/api/testing/queueItems";
import { isTerminalQueueItemState } from "@/api/testing/types";
import { useTerminalNotifications } from "@/api/useTerminalNotifications";
import { useAuthOptional } from "@/contexts/AuthContext";

const POLL_MS = 15_000;
const FETCH_LIMIT = 50;
const STORAGE_PREFIX = "emm.notifications.testing.";

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

/** Подпись теста для toast'а: «Тест <code> ...». */
function outcomeMessage(item: PublicQueueItem): string {
  const label = item.test_code ?? item.test_name ?? item.test_id;
  if (item.state === "succeeded" && item.verdict === "unknown") return `Тест ${label} завершён, результат не определён`;
  if (item.state === "succeeded") return `Тест ${label} завершён успешно`;
  if (item.state === "failed") return `Тест ${label} завершился с ошибкой`;
  if (item.state === "timed_out") return `Тест ${label} провален по таймауту`;
  return `Тест ${label} пропущен`;
}

export function useDepartmentRunNotifications(): DepartmentRunNotifications {
  const user = useAuthOptional()?.user ?? null;
  const userId = user?.user_id ?? null;
  const departmentId = user?.department_id ?? null;

  const { rows, unreadCount, markAllRead, markRead } =
    useTerminalNotifications<PublicQueueItem>({
      userId,
      // Без отдела опрашивать нечего — выдача сужается по department_id.
      enabled: Boolean(departmentId),
      storagePrefix: STORAGE_PREFIX,
      pollMs: POLL_MS,
      fetchItems: () =>
        listQueueItems({ kind: "all", limit: FETCH_LIMIT, order: "desc" }).then((p) => p.items),
      getId: (it) => it.id,
      getStatus: (it) => it.state,
      isTerminal: isTerminalQueueItemState,
      buildMessage: outcomeMessage,
      isVisible: (it) => isTerminalQueueItemState(it.state),
    });

  const notifications = useMemo<TestRunNotification[]>(
    () => rows.map(({ item, read }) => ({ item, read })),
    [rows],
  );

  return { notifications, unreadCount, markAllRead, markRead };
}
