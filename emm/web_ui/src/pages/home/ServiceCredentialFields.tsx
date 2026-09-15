import { Link } from "react-router-dom";
import { useQuery } from "@/api/auth/useQuery";
import { listCredentials } from "@/api/secret/credentials";
import type { Credential } from "@/api/secret/types";
import { apiErrMsg } from "@/api/client";
import { Dropdown } from "@/components/ui/Dropdown";
import { Button } from "@/components/ui/Button";

const FIELDS = [
  { key: "credential_id", label: "Учётные данные Jira / Zephyr / Confluence / Tempo" },
  { key: "bitbucket_credential_id", label: "Учётные данные Git / Bitbucket" },
];

function unavailableReason(cred: Credential) {
  if (cred.status !== "active") return "заблокированы";
  if (cred.valid_from && new Date(cred.valid_from).getTime() > Date.now()) return "ещё не действуют";
  if (cred.valid_to && new Date(cred.valid_to).getTime() < Date.now()) return "срок истёк";
  return "";
}

export function ServiceCredentialFields({ departmentId, values, onChange, disabled }: {
  departmentId: string; values: Record<string, string>;
  onChange: (key: string, value: string) => void; disabled?: boolean;
}) {
  const credentialsQ = useQuery(async () => {
    const items: Credential[] = [];
    const seen = new Set<string>();
    let cursor: string | undefined;
    do {
      const page = await listCredentials({ scope: "service", limit: 100, cursor });
      for (const item of page.items) {
        if ("owner_dept_id" in item && item.owner_dept_id === departmentId) items.push(item);
      }
      cursor = page.next_cursor ?? undefined;
      if (cursor && seen.has(cursor)) throw new Error("Не удалось получить полный список учётных данных");
      if (cursor) seen.add(cursor);
    } while (cursor);
    return items;
  }, [departmentId]);
  const credentials = credentialsQ.data ?? [];

  return <>
    {FIELDS.map(({ key, label }) => {
      const selected = values[key] ?? "";
      const current = credentials.find((cred) => cred.id === selected);
      const missing = selected && !current;
      const reason = current ? unavailableReason(current) : "";
      return <div key={key} className="flex flex-col gap-1 text-sm">
        <label className="flex flex-col gap-1">
          <span className="field-label">{label}</span>
          <Dropdown mode="single" label={label} value={selected} disabled={disabled || credentialsQ.isFetching}
            placeholder="Не выбраны" onChange={(next) => onChange(key, next)}
            options={[
              ...(missing ? [{ value: selected, label: "Текущая запись недоступна в списке", disabled: true }] : []),
              ...credentials.map((cred) => {
                const unavailable = unavailableReason(cred);
                return { value: cred.id, label: `${cred.name} · ${cred.service}${unavailable ? ` (${unavailable})` : ""}`, disabled: !!unavailable };
              }),
            ]} />
        </label>
        {current && <Link to={`/secret/service?id=${encodeURIComponent(current.id)}`} target="_blank" rel="noopener noreferrer" className="text-xs text-accent">Открыть учётные данные</Link>}
        {reason && <span className="text-xs text-danger">Учётные данные {reason}. Обновите их в сервисе секретов.</span>}
        {missing && !credentialsQ.isFetching && <span className="text-xs text-dim">Проверьте доступ, владельца и область хранения записи. Сохранённая привязка остаётся до вашего изменения.</span>}
      </div>;
    })}
    <div className="sm:col-span-2 flex flex-wrap items-center gap-3 text-xs">
      <Link to="/secret/service?action=new" target="_blank" rel="noopener noreferrer" className="text-accent">Создать сервисные учётные данные</Link>
      <Button size="sm" disabled={credentialsQ.isFetching} onClick={credentialsQ.refetch}>Обновить список учётных данных</Button>
      {credentialsQ.loading && <span>Загрузка учётных данных…</span>}
      {credentialsQ.error && <span role="alert" className="text-danger">{apiErrMsg(credentialsQ.error, "Не удалось загрузить сервисные учётные данные")}</span>}
      {!credentialsQ.isFetching && !credentialsQ.error && credentials.length === 0 && <span className="text-dim">В вашем отделе пока нет доступных сервисных учётных данных.</span>}
    </div>
  </>;
}
