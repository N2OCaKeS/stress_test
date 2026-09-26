/**
 * Прогресс многоступенчатого теста в очереди: «шаг 2/4».
 *
 * Показывается только у item'а, который сейчас занимает стенд
 * (`preparing`/`ready`/`running`): у одношагового теста и у ещё не начатого
 * item'а — пусто.
 */

const STEP_PROGRESS_STATES = new Set(["preparing", "ready", "running"]);

export function stepProgress(item: {
  state: string;
  current_step_index?: number;
  step_count?: number | null;
}): string {
  const count = item.step_count ?? 0;
  if (count <= 1 || !STEP_PROGRESS_STATES.has(item.state)) return "";
  const index = Math.min(Math.max(item.current_step_index ?? 0, 0), count - 1);
  return `шаг ${index + 1}/${count}`;
}

/** Заметка о состоянии с прогрессом по шагам, если он есть. */
export function withStepProgress(note: string, item: Parameters<typeof stepProgress>[0]): string {
  const progress = stepProgress(item);
  return progress ? `${note} · ${progress}` : note;
}
