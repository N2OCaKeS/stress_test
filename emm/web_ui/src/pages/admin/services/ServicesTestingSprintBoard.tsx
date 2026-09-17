/**
 * Дубль доски активного спринта Jira отдела (`Администрирование → Сервисы →
 * Тестирование → Доска спринта`).
 *
 * Read-only: тянет `GET /department-integration-settings/{department_id}/
 * sprint-board` (группировка issue по статусу уже сделана на backend) и
 * отдельно `GET /department-integration-settings/{department_id}` — только
 * чтобы достать `jira_base_url` и сделать ключи задач кликабельными ссылками
 * на реальную Jira. Никаких write-вызовов в Jira эта страница не делает.
 *
 * Источник истины — `testing_service`:
 *   `src/api/v1/endpoints/department_integration_settings.py`
 *   `src/services/jira_sprint_board.py`
 */

import { AlertTriangle, ExternalLink, Loader2, Rows3, User } from "lucide-react";

import { usePersona } from "@/contexts/PersonaContext";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { personaDeptId } from "@/lib/rbac";
import {
  getDepartmentIntegrationSettings,
  getDepartmentSprintBoard,
} from "@/api/testing/departmentIntegrationSettings";
import type { JiraSprintBoardColumn, JiraSprintBoardIssue } from "@/api/testing/types";

export function ServicesTestingSprintBoard() {
  const { persona } = usePersona();
  const departmentId = personaDeptId(persona);

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Rows3 className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">Доска спринта</h1>
          <div className="text-xs text-dim">
            зеркало активного спринта Jira отдела · только чтение, ничего не пишет в Jira
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5">
        {departmentId ? (
          <SprintBoardPanel departmentId={departmentId} />
        ) : (
          <div className="text-xs text-dim">
            Доска доступна только в рамках отдела — у платформенного администратора нет
            собственного department_id для этого раздела.
          </div>
        )}
      </div>
    </div>
  );
}

function SprintBoardPanel({ departmentId }: { departmentId: string }) {
  const boardQ = useQuery(() => getDepartmentSprintBoard(departmentId), [departmentId]);
  const settingsQ = useQuery(() => getDepartmentIntegrationSettings(departmentId), [departmentId]);
  const jiraBaseUrl = settingsQ.data?.jira_base_url ?? null;

  if (boardQ.loading) {
    return (
      <div className="text-xs text-dim flex items-center gap-2">
        <Loader2 className="w-4 h-4 animate-spin" /> Загрузка…
      </div>
    );
  }
  if (boardQ.error) {
    return (
      <div className="alert alert-danger flex items-start gap-2 text-xs">
        <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
        <div className="flex-1">{apiErrMsg(boardQ.error, "Доска спринта не загрузилась")}</div>
      </div>
    );
  }

  const board = boardQ.data;
  if (!board || !board.configured) {
    return (
      <div className="text-xs text-dim">
        Jira для этого отдела не настроена (jira_base_url/jira_board_id/credential_id) —
        заведите интеграцию в настройках отдела.
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      {board.sprint ? (
        <div className="surface-2 border border-token rounded p-3 flex items-center gap-3 flex-wrap text-xs">
          <span className="font-semibold text-sm">{board.sprint.name}</span>
          <span className="text-dim">
            {board.sprint.start_date ?? "—"} → {board.sprint.end_date ?? "—"}
          </span>
        </div>
      ) : null}

      {board.warning && (
        <div className="alert alert-warn flex items-start gap-2 text-xs">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>{board.warning}</div>
        </div>
      )}

      {board.columns.length > 0 && (
        <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${board.columns.length}, minmax(220px, 1fr))` }}>
          {board.columns.map((column) => (
            <SprintBoardColumnCard key={column.status} column={column} jiraBaseUrl={jiraBaseUrl} />
          ))}
        </div>
      )}
    </div>
  );
}

function SprintBoardColumnCard({
  column,
  jiraBaseUrl,
}: {
  column: JiraSprintBoardColumn;
  jiraBaseUrl: string | null;
}) {
  return (
    <div className="surface border border-token rounded overflow-hidden flex flex-col">
      <div className="px-3 py-2 border-b border-token bg-surface-2 flex items-center justify-between">
        <span className="text-sm font-semibold">{column.status}</span>
        <span className="text-xs text-dim">{column.issues.length}</span>
      </div>
      <div className="p-2 grid gap-2">
        {column.issues.length === 0 ? (
          <div className="text-xs text-dim italic px-1">пусто</div>
        ) : (
          column.issues.map((issue) => <SprintBoardIssueCard key={issue.key} issue={issue} jiraBaseUrl={jiraBaseUrl} />)
        )}
      </div>
    </div>
  );
}

function SprintBoardIssueCard({
  issue,
  jiraBaseUrl,
}: {
  issue: JiraSprintBoardIssue;
  jiraBaseUrl: string | null;
}) {
  const href = jiraBaseUrl ? `${jiraBaseUrl}/browse/${issue.key}` : null;
  return (
    <div className="surface-2 border border-token rounded p-2 text-xs grid gap-1">
      <div className="flex items-center justify-between gap-2">
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="mono text-accent inline-flex items-center gap-1 hover:underline"
          >
            {issue.key}
            <ExternalLink className="w-3 h-3" />
          </a>
        ) : (
          <span className="mono">{issue.key}</span>
        )}
        {issue.issue_type && <span className="text-dim">{issue.issue_type}</span>}
      </div>
      <div className="break-words">{issue.summary}</div>
      <div className="flex items-center gap-1 text-dim">
        <User className="w-3 h-3" />
        {issue.assignee ?? "не назначен"}
      </div>
    </div>
  );
}
