/**
 * Поллинг исхода диспатченной worker-задачи.
 *
 * Вкладки сервера, которые дёргают prepare / inventory / users-inventory и
 * т.п., получают от backend'а только `{task_id, status}` (HTTP 202) — сама
 * работа уходит в worker. Раньше такие вкладки тостили «запущено» и забывали
 * про задачу: если worker закрывал её FAILED (битые креды, недоступный BMC),
 * причина была видна только в `/worker`. Хук опрашивает `GET /tasks/{id}` до
 * терминального статуса и отдаёт исход (включая `last_error` при failed), как
 * это уже сделано в `tabs/packages.tsx`.
 *
 * Останавливается на любом терминале (succeeded/failed/cancelled), чистит
 * таймер на unmount и при смене задачи. Вечного спиннера нет.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getTask } from "@/api/server/misc";
import { apiErrMsg, ApiError } from "@/api/client";
import type { TaskRead } from "@/api/server/types";
import { isTerminalTaskStatus } from "@/api/server/types";

export { isTerminalTaskStatus };

const DEFAULT_POLL_MS = 3_000;

/** Что показывает вкладка по одной отслеживаемой задаче. */
export interface TrackedTask {
  /** Какую операцию запустили — для подписи («prepare», «inventory_sync»). */
  label: string;
  taskId: string;
  status: string;
  /** true, пока задача в работе и мы опрашиваем её статус. */
  polling: boolean;
  /** Человекочитаемая ошибка при failed (или сбой самого поллинга). */
  error: string | null;
  /** Финальный `task.result`, когда задача завершилась. */
  result: TaskRead["result"];
}

export interface TaskOutcomePoll {
  tracked: TrackedTask | null;
  /** Начать отслеживать только что задиспатченную задачу. */
  track: (label: string, taskId: string, status?: string) => void;
  /** Скинуть текущий трек (например, перед новым диспатчем). */
  reset: () => void;
}

/**
 * @param pollMs интервал опроса; дефолт 3с. installed-packages-проба и
 *               lifecycle-задачи живут десятки секунд — чаще опрашивать смысла
 *               нет.
 */
export function useTaskOutcome(pollMs: number = DEFAULT_POLL_MS): TaskOutcomePoll {
  const [tracked, setTracked] = useState<TrackedTask | null>(null);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const track = useCallback(
    (label: string, taskId: string, status = "queued") => {
      setTracked({
        label,
        taskId,
        status,
        polling: true,
        error: null,
        result: null,
      });
    },
    [],
  );

  const reset = useCallback(() => setTracked(null), []);

  const taskId = tracked?.taskId ?? null;
  const polling = tracked?.polling ?? false;

  useEffect(() => {
    if (!taskId || !polling) return;
    let stopped = false;
    const tick = () => {
      getTask(taskId)
        .then((t) => {
          if (stopped || !aliveRef.current) return;
          setTracked((prev) => {
            if (!prev || prev.taskId !== t.id) return prev;
            const done = isTerminalTaskStatus(t.status);
            return {
              ...prev,
              status: t.status,
              polling: !done,
              result: done ? t.result : prev.result,
              error:
                t.status === "failed"
                  ? (t.last_error ?? "Задача завершилась ошибкой")
                  : prev.error,
            };
          });
        })
        .catch((e: unknown) => {
          if (stopped || !aliveRef.current) return;
          // Транзиентный сбой при опросе — обрыв сети или шлюз (502/503/504,
          // напр. во время раската сервиса) — это не терминал задачи: не роняем
          // поллинг, пропускаем тик и пробуем снова. Реальная ошибка (4xx,
          // задача не найдена / нет прав) — останавливаемся и показываем.
          const transient = !(e instanceof ApiError) || e.status >= 500;
          if (transient) return;
          setTracked((prev) =>
            prev && prev.taskId === taskId
              ? {
                  ...prev,
                  polling: false,
                  error: apiErrMsg(e, "Не удалось прочитать результат задачи"),
                }
              : prev,
          );
        });
    };
    tick();
    const id = window.setInterval(tick, pollMs);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [taskId, polling, pollMs]);

  return { tracked, track, reset };
}
