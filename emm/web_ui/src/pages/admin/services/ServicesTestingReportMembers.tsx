/**
 * Сотрудники отдела для отчёта по активностям
 * (`/admin/services.testing.report_members`).
 *
 * CRUD-список `department_report_members` — только сотрудники из этого
 * списка учитываются при генерации HR-отчёта (см. `HrReportCard` на Home).
 *
 * Источник истины — `testing_service`:
 *   GET/POST/PATCH/DELETE /api/testing/v1/departments/{department_id}/report-members
 * Гейтится тем же кругом, что и «Стенды пула»: department_admin своего
 * отдела или носитель `admin` service-роли testing_service.
 */

import { useState } from "react";
import { AlertCircle, Trash2, UserPlus, Users } from "lucide-react";

import { usePersona } from "@/contexts/PersonaContext";
import { personaDeptId } from "@/lib/rbac";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  createDepartmentReportMember,
  deleteDepartmentReportMember,
  listDepartmentReportMembers,
  updateDepartmentReportMember,
} from "@/api/testing/departmentReportMembers";
import type { DepartmentReportMember } from "@/api/testing/types";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Toggle } from "@/components/ui/Toggle";

export function ServicesTestingReportMembers() {
  const { persona } = usePersona();
  const departmentId = personaDeptId(persona);

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Users className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Сотрудники отдела для отчёта по активностям
          </h1>
          <div className="text-xs text-dim">testing_service · настройки отдела</div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5">
        {departmentId ? (
          <ReportMembersPanel departmentId={departmentId} />
        ) : (
          <div className="text-sm text-dim">Нет привязанного отдела.</div>
        )}
      </div>
    </div>
  );
}

function ReportMembersPanel({ departmentId }: { departmentId: string }) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const membersQ = useQuery(
    () => listDepartmentReportMembers(departmentId, { limit: 200 }),
    [departmentId],
  );
  const [modalMember, setModalMember] = useState<DepartmentReportMember | "new" | null>(null);

  const members = membersQ.data?.items ?? [];

  async function handleDelete(member: DepartmentReportMember) {
    if (
      !(await confirm({
        title: "Удалить сотрудника",
        message: `Удалить ${member.display_name} из списка сотрудников для отчёта по активностям?`,
        confirmLabel: "Удалить",
        danger: true,
      }))
    )
      return;
    try {
      await deleteDepartmentReportMember(departmentId, member.id);
      toast.success(`${member.display_name} удалён из отчёта по активностям`);
      membersQ.refetch();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось удалить сотрудника"));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs text-dim">
          Коммиты, комментарии в Jira по спринтам и часы Tempo учитываются только
          для сотрудников из этого списка.
        </div>
        <Button
          variant="primary"
          size="sm"
          type="button"
          className="flex items-center gap-1 shrink-0"
          onClick={() => setModalMember("new")}
        >
          <UserPlus className="w-3.5 h-3.5" /> Добавить
        </Button>
      </div>

      {membersQ.loading && members.length === 0 && (
        <div className="text-xs text-dim py-2">Загрузка…</div>
      )}
      {!membersQ.loading && membersQ.error != null && (
        <div className="alert-danger text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>{apiErrMsg(membersQ.error, "Список не загрузился")}</div>
            <Button variant="ghost" className="mt-2" onClick={() => membersQ.refetch()} type="button">
              Повторить
            </Button>
          </div>
        </div>
      )}
      {!membersQ.loading && membersQ.error == null && members.length === 0 && (
        <div className="text-sm text-dim text-center py-4">Список пуст.</div>
      )}
      {members.length > 0 && (
        <div className="flex flex-col gap-1">
          {members.map((m) => (
            <ReportMemberRow
              key={m.id}
              member={m}
              onEdit={() => setModalMember(m)}
              onDelete={() => handleDelete(m)}
            />
          ))}
        </div>
      )}

      {modalMember != null && (
        <ReportMemberModal
          departmentId={departmentId}
          member={modalMember === "new" ? null : modalMember}
          onClose={() => setModalMember(null)}
          onSaved={() => {
            setModalMember(null);
            membersQ.refetch();
          }}
        />
      )}
    </div>
  );
}

function ReportMemberRow({
  member,
  onEdit,
  onDelete,
}: {
  member: DepartmentReportMember;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="surface-2 border border-token rounded px-3 py-2 flex items-center gap-2 flex-wrap">
      <div className="flex-1 min-w-[160px]">
        <div className="text-sm flex items-center gap-2">
          {member.display_name}
          {!member.is_active && <span className="text-xs text-dim">(не учитывается)</span>}
        </div>
        <div className="text-[11px] text-dim mono truncate">
          {member.bitbucket_username ?? "—"} · {member.jira_author_name ?? "—"} ·{" "}
          {member.jira_tempo_worker_key ?? "—"}
        </div>
      </div>
      <Button variant="ghost" size="sm" type="button" onClick={onEdit}>
        Изменить
      </Button>
      <Button variant="danger" size="sm" type="button" className="flex items-center gap-1" onClick={onDelete}>
        <Trash2 className="w-3.5 h-3.5" /> Удалить
      </Button>
    </div>
  );
}

function ReportMemberModal({
  departmentId,
  member,
  onClose,
  onSaved,
}: {
  departmentId: string;
  member: DepartmentReportMember | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [displayName, setDisplayName] = useState(member?.display_name ?? "");
  const [bitbucketUsername, setBitbucketUsername] = useState(member?.bitbucket_username ?? "");
  const [jiraAuthorName, setJiraAuthorName] = useState(member?.jira_author_name ?? "");
  const [tempoWorkerKey, setTempoWorkerKey] = useState(member?.jira_tempo_worker_key ?? "");
  const [isActive, setIsActive] = useState(member?.is_active ?? true);
  const [pending, setPending] = useState(false);

  async function handleSubmit() {
    const name = displayName.trim();
    if (!name) {
      toast.error("Укажите имя сотрудника.");
      return;
    }
    setPending(true);
    try {
      const body = {
        display_name: name,
        bitbucket_username: bitbucketUsername.trim() || null,
        jira_author_name: jiraAuthorName.trim() || null,
        jira_tempo_worker_key: tempoWorkerKey.trim() || null,
        is_active: isActive,
      };
      if (member) {
        await updateDepartmentReportMember(departmentId, member.id, body);
        toast.success(`${name} обновлён`);
      } else {
        await createDepartmentReportMember(departmentId, body);
        toast.success(`${name} добавлен`);
      }
      onSaved();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить сотрудника"));
    } finally {
      setPending(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={member ? "Изменить сотрудника" : "Добавить сотрудника"}
      icon={<Users className="w-5 h-5 text-accent" />}
      footer={
        <>
          <Button variant="default" type="button" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="primary" type="button" onClick={handleSubmit} disabled={pending}>
            {pending ? "Сохраняем…" : "Сохранить"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">ФИО / отображаемое имя</span>
          <input
            className="field-input"
            autoFocus
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Иванов Иван"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Bitbucket username</span>
          <input
            className="field-input mono"
            value={bitbucketUsername}
            onChange={(e) => setBitbucketUsername(e.target.value)}
            placeholder="ivanov"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Jira author name</span>
          <input
            className="field-input mono"
            value={jiraAuthorName}
            onChange={(e) => setJiraAuthorName(e.target.value)}
            placeholder="Ivan Ivanov"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Tempo worker key</span>
          <input
            className="field-input mono"
            value={tempoWorkerKey}
            onChange={(e) => setTempoWorkerKey(e.target.value)}
            placeholder="JIRAUSER10123"
          />
        </label>
        <Toggle
          label="Учитывать в следующем отчёте"
          checked={isActive}
          onChange={(e) => setIsActive(e.target.checked)}
        />
      </div>
    </Modal>
  );
}
