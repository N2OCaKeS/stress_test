/**
 * Ротация ключей шифрования (`/admin/services.encryption.rotation`).
 *
 * Одна страница, по карточке на сервис с версионируемым keystore
 * (`server_service`, `secret_service`). Каждая карточка показывает активную
 * версию ключа и прогресс перешифрования (`migration_status`), а во время
 * активной миграции поллит статус каждые несколько секунд.
 *
 * «Перевыпустить ключ»: генерим свежий AES-256-GCM-ключ через
 * `auth generateServiceKey()` → сразу шлём в `rotate` сервиса → запускаем
 * поллинг до `migrated_pct === 100` и пустого outbox. После этого на каждую
 * старую версию из `by_version` доступна кнопка «Вывести (retire)»; backend
 * режет retire активной версии и версий с остатком строк (409).
 *
 * Безопасность: одноразовый ключ из generate сразу уходит в rotate и нигде не
 * сохраняется — ни в state, ни в логах, ни в консоли.
 *
 * Гейт страницы — `account_admin` (см. adminCatalog). Backend всё равно
 * перепроверяет (403 ACCOUNT_ADMIN_REQUIRED), но кнопки прячем заранее.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { KeyRound, RotateCw, ShieldCheck, Archive } from "lucide-react";

import { apiErrMsg } from "@/api/client";
import { useMockMode } from "@/api/auth/useQuery";
import { generateServiceKey } from "@/api/auth/serviceKeys";
import * as serverEnc from "@/api/server/encryptionAdmin";
import * as secretEnc from "@/api/secret/encryptionAdmin";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";

// Интервал поллинга прогресса во время активной миграции.
const POLL_MS = 4000;

/** Нормализованный срез статуса миграции — общий для server и secret. */
interface NormalizedStatus {
  activeVersion: number;
  total: number;
  remainingLegacy: number;
  migratedPct: number;
  byVersion: Record<string, number>;
  outboxPending: number;
}

function normalizeServer(s: serverEnc.ServerMigrationStatus): NormalizedStatus {
  return {
    activeVersion: s.active_version,
    total: s.total,
    remainingLegacy: s.remaining_legacy_total,
    migratedPct: s.migrated_pct,
    byVersion: s.by_version,
    outboxPending: s.outbox_pending,
  };
}

function normalizeSecret(s: secretEnc.SecretMigrationStatus): NormalizedStatus {
  return {
    activeVersion: s.active_version,
    total: s.total_rows,
    remainingLegacy: s.remaining_legacy,
    migratedPct: s.migrated_pct,
    byVersion: s.by_version,
    outboxPending: s.outbox_pending_count,
  };
}

/** Адаптер сервиса — прячет различия server/secret за единым интерфейсом. */
interface ServiceAdapter {
  key: "server" | "secret";
  label: string;
  fetchStatus: () => Promise<NormalizedStatus>;
  rotate: (newKeyB64: string) => Promise<{ new_version: number; idempotent: boolean }>;
  retire: (version: number) => Promise<{ retired: boolean; remaining_on_version: number }>;
}

const SERVER_ADAPTER: ServiceAdapter = {
  key: "server",
  label: "server_service",
  fetchStatus: () => serverEnc.getMigrationStatus().then(normalizeServer),
  rotate: (k) => serverEnc.rotate(k),
  retire: (v) => serverEnc.retire(v),
};

const SECRET_ADAPTER: ServiceAdapter = {
  key: "secret",
  label: "secret_service",
  fetchStatus: () => secretEnc.getMigrationStatus().then(normalizeSecret),
  rotate: (k) => secretEnc.rotate(k),
  retire: (v) => secretEnc.retire(v),
};

const ADAPTERS: ServiceAdapter[] = [SERVER_ADAPTER, SECRET_ADAPTER];

function isMigrating(s: NormalizedStatus): boolean {
  return s.migratedPct < 100 || s.outboxPending > 0 || s.remainingLegacy > 0;
}

export function ServicesEncryptionRotation() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  const toast = useToast();
  const confirm = useConfirm();

  const isAccountAdmin = persona.platform_role === "account_admin";

  // Триггерим «Перевыпустить всё»: последовательный rotate по всем адаптерам.
  const [bulkBusy, setBulkBusy] = useState(false);

  const cardRefs = useRef<Record<string, (() => Promise<void>) | null>>({});

  const registerRotate = useCallback(
    (key: string, fn: (() => Promise<void>) | null) => {
      cardRefs.current[key] = fn;
    },
    [],
  );

  async function rotateAll() {
    if (mockMode) {
      toast.warn("Mock-режим — ротация не отправляется на backend.");
      return;
    }
    if (
      !(await confirm.confirm({
        title: "Перевыпустить ключи всех сервисов",
        message:
          "Будет последовательно сгенерирован и активирован новый ключ для " +
          "server_service и secret_service. Старые версии останутся, пока " +
          "перешифрование не дойдёт до 100% — после этого их можно вывести.",
        confirmLabel: "Перевыпустить всё",
      }))
    )
      return;
    setBulkBusy(true);
    try {
      for (const a of ADAPTERS) {
        const fn = cardRefs.current[a.key];
        if (fn) await fn();
      }
    } finally {
      setBulkBusy(false);
    }
  }

  return (
    <div className="space-y-4 max-w-5xl">
      <div className="card">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <h3 className="font-semibold flex items-center gap-2">
            <KeyRound className="w-4 h-4 text-accent" /> Ротация ключей шифрования
          </h3>
          {isAccountAdmin && (
            <button
              className="btn flex items-center gap-1"
              disabled={bulkBusy || mockMode}
              onClick={rotateAll}
              title="Последовательно перевыпустить ключ server и secret"
            >
              <RotateCw className="w-4 h-4" /> Перевыпустить всё
            </button>
          )}
        </div>
        <div className="mt-2 text-xs text-dim">
          Каждый перевыпуск генерирует свежий AES-256-GCM-ключ в auth_service и
          сразу активирует его в выбранном сервисе. Старые строки
          перешифровываются в фоне; вывести старую версию можно только после
          100% и пустого outbox. Гейт — account_admin.
        </div>
      </div>

      {!isAccountAdmin && (
        <div className="card empty-card text-xs">
          Ротация ключей шифрования доступна только account_admin.
        </div>
      )}

      {isAccountAdmin &&
        ADAPTERS.map((a) => (
          <ServiceRotationCard
            key={a.key}
            adapter={a}
            mockMode={mockMode}
            registerRotate={registerRotate}
          />
        ))}
    </div>
  );
}

const MOCK_STATUS: Record<string, NormalizedStatus> = {
  server: {
    activeVersion: 3,
    total: 124,
    remainingLegacy: 0,
    migratedPct: 100,
    byVersion: { "2": 0, "3": 124 },
    outboxPending: 0,
  },
  secret: {
    activeVersion: 2,
    total: 58,
    remainingLegacy: 11,
    migratedPct: 81,
    byVersion: { "1": 11, "2": 47 },
    outboxPending: 3,
  },
};

function ServiceRotationCard({
  adapter,
  mockMode,
  registerRotate,
}: {
  adapter: ServiceAdapter;
  mockMode: boolean;
  registerRotate: (key: string, fn: (() => Promise<void>) | null) => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();

  const [status, setStatus] = useState<NormalizedStatus | null>(
    mockMode ? MOCK_STATUS[adapter.key] : null,
  );
  const [loading, setLoading] = useState(!mockMode);
  const [err, setErr] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);
  const [retiringVersion, setRetiringVersion] = useState<number | null>(null);

  // Поллер живёт только пока миграция активна — гасим интервал на 100%.
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    if (mockMode) return;
    try {
      const s = await adapter.fetchStatus();
      setStatus(s);
      setErr(null);
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setLoading(false);
    }
  }, [adapter, mockMode]);

  const stopPoll = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPoll = useCallback(() => {
    if (mockMode || pollRef.current !== null) return;
    pollRef.current = window.setInterval(() => {
      void refresh();
    }, POLL_MS);
  }, [mockMode, refresh]);

  // Первичная загрузка статуса.
  useEffect(() => {
    void refresh();
    return () => stopPoll();
  }, [refresh, stopPoll]);

  // Запускаем/гасим поллинг в зависимости от того, идёт ли миграция.
  useEffect(() => {
    if (!status) return;
    if (isMigrating(status)) startPoll();
    else stopPoll();
  }, [status, startPoll, stopPoll]);

  const doRotate = useCallback(async () => {
    if (mockMode) {
      toast.warn("Mock-режим — ротация не отправляется на backend.");
      return;
    }
    setRotating(true);
    setErr(null);
    try {
      // Одноразовый ключ: получаем и сразу отдаём в rotate. В переменной
      // живёт ровно до завершения запроса, в state не кладём.
      const generated = await generateServiceKey();
      const res = await adapter.rotate(generated.key_b64);
      if (res.idempotent) {
        toast.info(`Ключ уже активен (v${res.new_version}) — без изменений.`);
      } else {
        toast.success(`Новый ключ активирован: v${res.new_version}.`);
      }
      await refresh();
      startPoll();
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setRotating(false);
    }
  }, [adapter, mockMode, refresh, startPoll, toast]);

  // Регистрируем rotate в родителе для «Перевыпустить всё».
  useEffect(() => {
    registerRotate(adapter.key, doRotate);
    return () => registerRotate(adapter.key, null);
  }, [adapter.key, doRotate, registerRotate]);

  async function onRetire(version: number) {
    if (mockMode) {
      toast.warn("Mock-режим — retire не отправляется на backend.");
      return;
    }
    if (
      !(await confirm.confirm({
        title: `Вывести версию v${version}`,
        message:
          `Вывести ключ v${version} из keystore ${adapter.label}? Это ` +
          `необратимо. Backend откажет (409), если версия активна или на ней ` +
          `ещё остались зашифрованные строки.`,
        danger: true,
        confirmLabel: "Вывести",
      }))
    )
      return;
    setRetiringVersion(version);
    setErr(null);
    try {
      const res = await adapter.retire(version);
      if (res.retired) {
        toast.success(`Версия v${version} выведена.`);
      } else {
        toast.warn(
          `Версия v${version} не выведена — осталось строк: ${res.remaining_on_version}.`,
        );
      }
      await refresh();
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setRetiringVersion(null);
    }
  }

  const migrating = status ? isMigrating(status) : false;
  // Старые версии: всё, кроме активной. retire доступен только когда миграция
  // завершена (на старой версии не должно остаться строк, но окончательно
  // решает backend через 409 KEYSTORE_VERSION_IN_USE).
  const oldVersions = status
    ? Object.keys(status.byVersion)
        .map((v) => Number(v))
        .filter((v) => v !== status.activeVersion)
        .sort((a, b) => a - b)
    : [];
  const canRetire = status ? !migrating : false;
  const busy = rotating || retiringVersion !== null;

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <ShieldCheck className="w-4 h-4 text-accent" /> {adapter.label}
        </h3>
        <div className="flex items-center gap-2">
          {status && (
            <span className="badge" title="активная версия ключа">
              active v{status.activeVersion}
            </span>
          )}
          {migrating ? (
            <span className="badge badge-warn">миграция</span>
          ) : (
            status && <span className="badge badge-ok">в норме</span>
          )}
        </div>
      </div>

      {loading && <div className="text-xs text-dim">Загрузка статуса…</div>}

      {!loading && status && (
        <>
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-dim">
              перешифровано {status.migratedPct}% · всего строк {status.total}
            </span>
            <span className="text-dim">
              legacy: {status.remainingLegacy} · outbox: {status.outboxPending}
            </span>
          </div>
          <div className={`bar ${migrating ? "warn" : "ok"} mb-3`}>
            <span style={{ width: `${Math.min(100, Math.max(0, status.migratedPct))}%` }} />
          </div>

          <div className="space-y-1 mb-3">
            {Object.keys(status.byVersion)
              .map((v) => Number(v))
              .sort((a, b) => a - b)
              .map((v) => {
                const count = status.byVersion[String(v)] ?? 0;
                const isActive = v === status.activeVersion;
                return (
                  <div key={v} className="row-line text-xs">
                    <div className="flex items-center gap-2">
                      <span className="mono">v{v}</span>
                      {isActive && (
                        <span className="badge badge-ok">active</span>
                      )}
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-dim">{count} строк</span>
                      {!isActive && (
                        <button
                          className="btn btn-danger flex items-center gap-1"
                          disabled={busy || !canRetire}
                          title={
                            canRetire
                              ? "Вывести версию из keystore"
                              : "Дождитесь завершения перешифрования (100% и пустой outbox)"
                          }
                          onClick={() => onRetire(v)}
                        >
                          <Archive className="w-3.5 h-3.5" />
                          {retiringVersion === v ? "…" : "Вывести"}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
          </div>

          {oldVersions.length === 0 && (
            <div className="text-[11px] text-dim mb-3">
              Старых версий нет — весь keystore на активном ключе.
            </div>
          )}
        </>
      )}

      {err && <div className="alert-danger mb-3 text-xs">{err}</div>}

      <div className="flex justify-end">
        <button
          className="btn btn-primary flex items-center gap-1"
          disabled={busy || mockMode}
          onClick={() => void doRotate()}
        >
          <RotateCw className="w-4 h-4" />
          {rotating ? "Перевыпуск…" : "Перевыпустить ключ"}
        </button>
      </div>
    </div>
  );
}
