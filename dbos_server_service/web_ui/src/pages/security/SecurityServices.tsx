import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2, Wrench } from "lucide-react";
import { ApiError } from "@/api/client";
import {
  createService,
  deleteService,
  listServices,
} from "@/api/auth/services";
import type { Service } from "@/api/auth/types";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";

export function SecurityServices() {
  const { persona } = usePersona();
  const canEdit = persona.platform_role === "account_admin";
  const [items, setItems] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const toast = useToast();

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadErr(null);
    try {
      setItems(await listServices());
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Не удалось загрузить сервисы";
      setLoadErr(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="flex flex-col gap-4">
      {!canEdit && (
        <div className="readonly-bar">
          <span className="ro-label">read-only</span>
          <span>
            Регистрация и удаление сервисов — только account_admin. Список
            доступен всем admin-ролям.
          </span>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div className="text-sm text-dim">
          Каталог сервисов, известных платформе. На создании появляется
          системная роль `admin`.
        </div>
        {canEdit && (
          <button
            className="btn btn-primary flex items-center gap-1"
            onClick={() => setCreating(true)}
          >
            <Plus className="w-4 h-4" /> Зарегистрировать сервис
          </button>
        )}
      </div>

      {creating && canEdit && (
        <CreateForm
          onCancel={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            void reload();
          }}
        />
      )}

      <div className="card">
        <h3 className="font-semibold flex items-center gap-2 mb-3">
          <Wrench className="w-4 h-4 text-accent" /> Сервисы
        </h3>
        {loading ? (
          <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
        ) : loadErr ? (
          <div className="alert-danger text-xs flex items-center justify-between gap-2">
            <span>{loadErr}</span>
            <button className="btn btn-ghost btn-sm" onClick={() => void reload()}>
              Повторить
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="text-xs text-dim py-4 text-center">Сервисов нет.</div>
        ) : (
          <div className="flex flex-col gap-2">
            {items.map((s) => (
              <ServiceRow
                key={s.service_name}
                svc={s}
                canEdit={canEdit}
                onChange={reload}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ServiceRow({
  svc,
  canEdit,
  onChange,
}: {
  svc: Service;
  canEdit: boolean;
  onChange: () => void | Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const toast = useToast();
  const onDelete = async () => {
    if (
      !window.confirm(
        `Удалить сервис «${svc.service_name}»? Это снимет dept-access и роли.`,
      )
    )
      return;
    setPending(true);
    try {
      await deleteService(svc.service_name);
      toast.success("Сервис удалён");
      await onChange();
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
      else toast.error("Не удалось удалить сервис");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="cred-row flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate mono">
          {svc.service_name}
        </div>
        {svc.description && (
          <div className="text-[11px] text-dim truncate">{svc.description}</div>
        )}
      </div>
      {canEdit && (
        <button
          className="btn btn-danger flex items-center gap-1"
          onClick={onDelete}
          disabled={pending}
        >
          <Trash2 className="w-4 h-4" /> Delete
        </button>
      )}
    </div>
  );
}

function CreateForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: () => void;
}) {
  const [serviceName, setServiceName] = useState("");
  const [description, setDescription] = useState("");
  const [pending, setPending] = useState(false);
  const toast = useToast();

  const submit = async () => {
    if (!serviceName.trim()) {
      toast.warn("service_name обязателен");
      return;
    }
    setPending(true);
    try {
      await createService({
        service_name: serviceName.trim(),
        description: description.trim() || undefined,
      });
      toast.success("Сервис зарегистрирован");
      onCreated();
    } catch (e) {
      if (e instanceof ApiError) toast.error(e.message);
      else toast.error("Не удалось создать сервис");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="card">
      <h3 className="font-semibold mb-3">Новый сервис</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">service_name</span>
          <input
            className="input mono"
            value={serviceName}
            onChange={(e) => setServiceName(e.target.value)}
            placeholder="config_service"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm md:col-span-2">
          <span className="text-dim text-xs">description</span>
          <input
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onCancel} disabled={pending}>
          Отмена
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={pending}>
          Создать
        </button>
      </div>
    </div>
  );
}
