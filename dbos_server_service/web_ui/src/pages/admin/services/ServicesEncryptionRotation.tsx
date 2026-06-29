/**
 * Ротация ключей шифрования (`/admin/services.encryption.rotation`).
 *
 * Одна страница, по карточке на сервис с версионируемым keystore
 * (`server_service`, `secret_service`). Каждая карточка показывает активную
 * версию ключа и прогресс перешифрования (`migration_status`), а во время
 * активной миграции поллит статус каждые несколько секунд.
 *
 * «Перевыпустить ключ»: генерим свежий AES-256-GCM-ключ через
 * `auth generateServiceKey()` → сразу шлём в `rotate` сервиса. Ответ несёт
 * `previous_version → new_version`, и карточка показывает этот переход явно.
 *
 * Дальше идёт фоновая перешифровка старых строк под новый ключ. Прогресс
 * (`migrated_pct`, `remaining_legacy`, `by_version`, outbox) поллится, но сама
 * дошифровка — это worker-таска, которая в dev может быть выключена. Поэтому
 * поллинг не крутится вечно на месте: если несколько тиков подряд прогресс не
 * двигается, карточка переходит в «idle» и показывает, что дошифровка идёт в
 * фоне / сейчас не активна, с кнопкой «Обновить» для ручной проверки.
 *
 * Старые версии backend выводит автоматически сразу после того, как на них не
 * остаётся строк, — карточка отражает это как завершение. Ручная кнопка
 * «Вывести (retire)» оставлена как fallback; backend режет retire активной
 * версии и версий с остатком строк (409).
 *
 * Безопасность: одноразовый ключ из generate сразу уходит в rotate и нигде не
 * сохраняется — ни в state, ни в логах, ни в консоли.
 *
 * Гейт страницы — `account_admin` (см. adminCatalog). Backend всё равно
 * перепроверяет (403 ACCOUNT_ADMIN_REQUIRED), но кнопки прячем заранее.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  KeyRound,
  RotateCw,
  ShieldCheck,
  Archive,
  ArrowRight,
  RefreshCw,
  CheckCircle2,
} from "lucide-react";

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

// Сколько тиков подряд без движения прогресса считаем «застоем»: после этого
// гасим поллинг и показываем, что дошифровка идёт в фоне / не активна, вместо
// вечного спиннера на одном проценте.
const STALL_TICKS = 3;

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
  rotate: (
    newKeyB64: string,
  ) => Promise<{ new_version: number; previous_version: number; idempotent: boolean }>;
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

  // Результат последнего перевыпуска: переход previous → new, который показываем
  // прямо в карточке, а не только тостом.
  const [lastRotation, setLastRotation] = useState<{ from: number; to: number } | null>(null);
  // Прогресс перешифровки замер (несколько тиков без движения) — переходим в
  // «idle» и перестаём поллить.
  const [stalled, setStalled] = useState(false);

  // Поллер живёт только пока миграция активна и прогресс двигается.
  const pollRef = useRef<number | null>(null);
  // Последнее замеченное состояние прогресса для детекта застоя.
  const progressRef = useRef<{ pct: number; legacy: number; ticks: number }>({
    pct: -1,
    legacy: -1,
    ticks: 0,
  });

  const stopPoll = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const refresh = useCallback(async () => {
    if (mockMode) return;
    try {
      const s = await adapter.fetchStatus();
      // Сравниваем с прошлым замером: если процент и остаток legacy не сдвинулись
      // несколько раз подряд при незавершённой миграции — считаем дошифровку
      // неактивной и гасим поллинг.
      const prev = progressRef.current;
      if (s.migratedPct === prev.pct && s.remainingLegacy === prev.legacy) {
        progressRef.current = {
          pct: s.migratedPct,
          legacy: s.remainingLegacy,
          ticks: prev.ticks + 1,
        };
      } else {
        progressRef.current = {
          pct: s.migratedPct,
          legacy: s.remainingLegacy,
          ticks: 0,
        };
        setStalled(false);
      }
      if (isMigrating(s) && progressRef.current.ticks >= STALL_TICKS) {
        setStalled(true);
        stopPoll();
      }
      setStatus(s);
      setErr(null);
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setLoading(false);
    }
  }, [adapter, mockMode, stopPoll]);

  const startPoll = useCallback(() => {
    if (mockMode || pollRef.current !== null) return;
    pollRef.current = window.setInterval(() => {
      void refresh();
    }, POLL_MS);
  }, [mockMode, refresh]);

  // Сброс трекинга прогресса — после ручного «Обновить» и после перевыпуска,
  // чтобы дать дошифровке свежее окно наблюдения.
  const resetProgressTracking = useCallback(() => {
    progressRef.current = { pct: -1, legacy: -1, ticks: 0 };
    setStalled(false);
  }, []);

  // Первичная загрузка статуса.
  useEffect(() => {
    void refresh();
    return () => stopPoll();
  }, [refresh, stopPoll]);

  // Запускаем/гасим поллинг: только пока миграция активна и не зафиксирован
  // застой. При застое effect получит stalled=true и погасит интервал.
  useEffect(() => {
    if (!status) return;
    if (isMigrating(status) && !stalled) startPoll();
    else stopPoll();
  }, [status, stalled, startPoll, stopPoll]);

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
        setLastRotation({ from: res.previous_version, to: res.new_version });
        toast.success(`Новый ключ активирован: v${res.previous_version} → v${res.new_version}.`);
      }
      // Свежий ключ — даём дошифровке новое окно наблюдения и снова поллим.
      resetProgressTracking();
      await refresh();
      startPoll();
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setRotating(false);
    }
  }, [adapter, mockMode, refresh, resetProgressTracking, startPoll, toast]);

  // Ручная проверка прогресса из «idle»: сбрасываем застой и поллим заново.
  const manualRefresh = useCallback(() => {
    resetProgressTracking();
    void refresh();
  }, [refresh, resetProgressTracking]);

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
  // Застой имеет смысл только при незавершённой миграции.
  const stale = migrating && stalled;
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
  // Старые версии выведены автоматически: после перевыпуска в keystore не
  // осталось ничего, кроме активного ключа.
  const autoRetired =
    !!lastRotation && !!status && oldVersions.length === 0;

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
            stale ? (
              <span className="badge" title="прогресс не двигается — дошифровка идёт в фоне">
                в фоне
              </span>
            ) : (
              <span className="badge badge-warn">миграция</span>
            )
          ) : (
            status && <span className="badge badge-ok">в норме</span>
          )}
        </div>
      </div>

      {lastRotation && (
        <div className="alert-success mb-3 text-xs">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span className="flex items-center gap-1">
            Активная версия ключа: v{lastRotation.from}
            <ArrowRight className="w-3.5 h-3.5" />
            <span className="mono">v{lastRotation.to}</span>
          </span>
        </div>
      )}

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

          {/* Осмысленный статус дошифровки вместо вечного спиннера на 0%. */}
          {migrating && !stale && (
            <div className="text-[11px] text-dim mb-3">
              Перешифровка идёт в фоне (lazy): осталось legacy{" "}
              {status.remainingLegacy}, в outbox {status.outboxPending}.
              Старые версии выведутся автоматически после дошифровки.
            </div>
          )}
          {stale && (
            <div className="alert-warn mb-3 text-xs">
              <div className="flex-1">
                Фоновая перешифровка сейчас не активна — прогресс не двигается
                ({status.migratedPct}%, осталось legacy {status.remainingLegacy}).
                Это нормально: оставшиеся строки дошифруются фоновой задачей, после
                чего старые версии выведутся автоматически.
                <button
                  className="btn btn-ghost flex items-center gap-1 mt-2"
                  disabled={busy}
                  onClick={manualRefresh}
                  title="Проверить прогресс заново"
                >
                  <RefreshCw className="w-3.5 h-3.5" /> Обновить
                </button>
              </div>
            </div>
          )}
          {autoRetired && (
            <div className="text-[11px] text-ok mb-3 flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
              Старые версии выведены автоматически — весь keystore на активном
              ключе v{status.activeVersion}.
            </div>
          )}

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

          {oldVersions.length === 0 && !autoRetired && (
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
