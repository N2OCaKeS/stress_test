import { useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Link } from "react-router-dom";
import {
  Grid3x3,
  ArrowLeft,
  Bell,
  CheckCircle,
  AlertTriangle,
  XOctagon,
  Info,
  X,
  LayoutTemplate,
  Plus,
  UserMinus,
  RefreshCw,
  Inbox,
  LockKeyhole,
  FileText,
  CloudOff,
  RotateCw,
  Loader,
  ShieldAlert,
  Trash2,
  PlusCircle,
  Check,
} from "lucide-react";
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher";

/**
 * Port of patterns.html — UI primitives reference page.
 * Modals use Radix Dialog. Toasts/loaders are visual only.
 */
export function Patterns() {
  const [confirmText, setConfirmText] = useState("");

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <header className="surface border-b border-token h-12 px-3 flex items-center gap-3 shrink-0">
        <Link
          to="/home"
          className="text-sm font-semibold flex items-center gap-2 px-2 hover-bg rounded h-8"
        >
          <Grid3x3 className="w-4 h-4 text-accent" /> DBOS
        </Link>
        <span className="text-xs text-dim">/ ui-паттерны (демо)</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-dim mr-1">Темы:</span>
          <ThemeSwitcher />
          <Link to="/home" className="btn flex items-center gap-1">
            <ArrowLeft className="w-4 h-4" /> На главную
          </Link>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="px-8 py-6 border-b border-token surface">
          <h1 className="text-2xl font-bold mb-1">UI-паттерны</h1>
          <p className="text-sm text-dim">
            Демо-страница «общих кусков» интерфейса: тосты, модалки, empty /
            loading / confirm. Работает во всех 4-х темах. Используй как
            справочник для других страниц.
          </p>
        </div>

        <section className="demo-section">
          <div className="section-title">
            <Bell className="w-5 h-5 text-accent" /> Тосты
          </div>
          <div className="section-sub">
            4 варианта: success / warning / error / info. Появляются в правом
            нижнем углу, автоматически прячутся через 5 сек.
          </div>

          <div className="grid gap-3 md:grid-cols-2 max-w-3xl">
            <ToastDemo
              kind="success"
              icon={<CheckCircle className="w-5 h-5 text-ok shrink-0 mt-0.5" />}
              title="Credential обновлён"
              meta="cred_8f2a · last_rotated_at записан в audit"
            />
            <ToastDemo
              kind="warn"
              icon={
                <AlertTriangle className="w-5 h-5 text-warn shrink-0 mt-0.5" />
              }
              title="Drift зафиксирован"
              meta="srv-node-17 · уровень доступа изменён вручную, БД не обновлена"
            />
            <ToastDemo
              kind="error"
              icon={
                <XOctagon className="w-5 h-5 text-danger shrink-0 mt-0.5" />
              }
              title="Ошибка ротации"
              meta="cred_a791 · BMC unreachable (ipmitool timeout 30s)"
            />
            <ToastDemo
              kind="info"
              icon={<Info className="w-5 h-5 text-accent shrink-0 mt-0.5" />}
              title="Worker подключился"
              meta="wrk-04 на host build-04 · heartbeat активен"
            />
          </div>
        </section>

        <section className="demo-section">
          <div className="section-title">
            <LayoutTemplate className="w-5 h-5 text-accent" /> Модальные окна
          </div>
          <div className="section-sub">
            Клик по кнопке открывает мок-модалку. Без логики — только видимая
            вёрстка.
          </div>

          <div className="flex flex-wrap gap-3">
            <CreateCredentialModal />
            <DeleteUserModal />
            <RotateCredentialModal />
          </div>
        </section>

        <section className="demo-section">
          <div className="section-title">
            <Inbox className="w-5 h-5 text-accent" /> Пустые состояния
          </div>
          <div className="section-sub">
            3 варианта: «можно создать», «просто пусто», «сервис недоступен».
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <div className="empty-card">
              <LockKeyhole className="w-10 h-10 mx-auto text-dim mb-3" />
              <div className="text-sm font-medium mb-1">Credential'ов нет</div>
              <div className="text-xs text-dim mb-4">
                В этом депе пока ничего не создано.
              </div>
              <button className="btn btn-primary inline-flex items-center gap-1">
                <Plus className="w-4 h-4" /> Создать
              </button>
            </div>

            <div className="empty-card">
              <FileText className="w-10 h-10 mx-auto text-dim mb-3" />
              <div className="text-sm font-medium mb-1">Audit-событий нет</div>
              <div className="text-xs text-dim">
                Под текущие фильтры (дата + тип + actor) ничего не нашлось.
                <br />
                Попробуй расширить диапазон.
              </div>
            </div>

            <div className="empty-card danger">
              <CloudOff className="w-10 h-10 mx-auto text-danger mb-3" />
              <div className="text-sm font-medium mb-1 text-danger">
                Сервис недоступен
              </div>
              <div className="text-xs text-dim mb-4">
                server_service не отвечает уже 42 секунды. Скорее всего,
                рестарт.
              </div>
              <button className="btn btn-danger inline-flex items-center gap-1">
                <RotateCw className="w-4 h-4" /> Повторить
              </button>
            </div>
          </div>
        </section>

        <section className="demo-section">
          <div className="section-title">
            <Loader className="w-5 h-5 text-accent" /> Состояния загрузки
          </div>
          <div className="section-sub">
            3 варианта: spinner в карточке, skeleton-список, inline-spinner в
            кнопке.
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <div
              className="demo-card flex flex-col items-center justify-center"
              style={{ minHeight: 180 }}
            >
              <span className="spinner big mb-3" />
              <div className="text-sm font-medium">
                Загружаем credentials...
              </div>
              <div className="text-xs text-dim mt-1">
                это занимает обычно 1-2 секунды
              </div>
            </div>

            <div className="demo-card">
              <div className="text-xs text-dim mb-3 uppercase tracking-wider">
                Серверы (загрузка)
              </div>
              <div className="flex flex-col gap-3">
                {[
                  { w1: "70%", w2: "40%" },
                  { w1: "60%", w2: "35%" },
                  { w1: "80%", w2: "50%" },
                  { w1: "65%", w2: "30%" },
                ].map((r, i) => (
                  <div key={i} className="flex items-center gap-3">
                    <div
                      className="skeleton"
                      style={{
                        width: 32,
                        height: 32,
                        borderRadius: 6,
                      }}
                    />
                    <div className="flex-1">
                      <div
                        className="skeleton"
                        style={{
                          height: 12,
                          width: r.w1,
                          marginBottom: 6,
                        }}
                      />
                      <div
                        className="skeleton"
                        style={{ height: 10, width: r.w2 }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="demo-card flex flex-col gap-3 items-start">
              <div className="text-xs text-dim uppercase tracking-wider">
                Спиннер в кнопке
              </div>
              <button
                className="btn btn-primary flex items-center gap-2"
                disabled
                style={{ opacity: 0.85 }}
              >
                <span
                  className="spinner"
                  style={{ width: 14, height: 14, borderWidth: 2 }}
                />
                Сохранение...
              </button>
              <button
                className="btn flex items-center gap-2"
                disabled
                style={{ opacity: 0.85 }}
              >
                <span
                  className="spinner"
                  style={{ width: 14, height: 14, borderWidth: 2 }}
                />
                Проверяю BMC...
              </button>
              <button
                className="btn btn-danger flex items-center gap-2"
                disabled
                style={{ opacity: 0.85 }}
              >
                <span
                  className="spinner"
                  style={{ width: 14, height: 14, borderWidth: 2 }}
                />
                Сброс...
              </button>
            </div>
          </div>
        </section>

        <section className="demo-section">
          <div className="section-title">
            <ShieldAlert className="w-5 h-5 text-danger" /> Паттерны
            подтверждения (опасные)
          </div>
          <div className="section-sub">
            kubectl-style: чтобы подтвердить опасное действие — введи имя
            ресурса буква-в-букву.
          </div>

          <div className="demo-card max-w-xl">
            <div className="flex items-start gap-3 mb-4">
              <AlertTriangle className="w-6 h-6 text-danger shrink-0" />
              <div>
                <div className="text-base font-semibold text-danger mb-1">
                  Удалить департамент «Разработка»?
                </div>
                <div className="text-xs text-dim">
                  Действие <b>необратимое</b>. Будут удалены:
                  <ul className="list-disc list-inside mt-2 space-y-0.5">
                    <li>
                      12 пользователей (или перемещены на default dept — на
                      выбор)
                    </li>
                    <li>32 сервера (или перемещены)</li>
                    <li>48 credential'ов (безвозвратно)</li>
                    <li>Все 3 бота этого депа (токены инвалидируются)</li>
                  </ul>
                </div>
              </div>
            </div>

            <div className="alert-block mb-4 text-xs">
              <b className="text-danger">Внимание:</b> credential'ы удаляются{" "}
              <b>безвозвратно</b>. Если на серверах стоят пароли, выданные этим
              деп'ом — после удаления вы потеряете к ним доступ.
            </div>

            <label className="field-label">
              Чтобы подтвердить, введи имя депа дословно:{" "}
              <span className="mono text-danger">Разработка</span>
            </label>
            <input
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              className="field-input mono mb-4"
              placeholder="Разработка"
            />

            <div className="flex items-center gap-2 justify-end">
              <button className="btn">Отмена</button>
              <button
                className="btn btn-danger-solid flex items-center gap-1"
                disabled={confirmText !== "Разработка"}
                style={{ opacity: confirmText === "Разработка" ? 1 : 0.5 }}
              >
                <Trash2 className="w-4 h-4" /> Удалить депaртамент
              </button>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

interface ToastDemoProps {
  kind: "success" | "warn" | "error" | "info";
  icon: ReactNode;
  title: string;
  meta: string;
}

function ToastDemo({ kind, icon, title, meta }: ToastDemoProps) {
  return (
    <div className={`toast toast-${kind}`}>
      {icon}
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium">{title}</div>
        <div className="text-xs text-dim mt-0.5">{meta}</div>
      </div>
      <button
        className="text-dim hover-bg rounded p-1"
        aria-label="скрыть"
        type="button"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

function CreateCredentialModal() {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button className="btn btn-primary flex items-center gap-1">
          <Plus className="w-4 h-4" /> Создать credential
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content">
          <div className="modal-header">
            <PlusCircle className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Создать credential
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                className="ml-auto text-dim hover-bg rounded p-1"
                aria-label="закрыть"
              >
                <X className="w-4 h-4" />
              </button>
            </Dialog.Close>
          </div>
          <div className="modal-body">
            <div className="grid gap-3">
              <div>
                <label className="field-label">Имя credential'а</label>
                <input className="field-input mono" placeholder="ipmi-srv-node-17" />
              </div>
              <div>
                <label className="field-label">Тип</label>
                <select className="field-input">
                  <option>ipmi</option>
                  <option>ssh-password</option>
                  <option>ssh-key</option>
                  <option>db-postgres</option>
                </select>
              </div>
              <div>
                <label className="field-label">Сервер</label>
                <input className="field-input mono" placeholder="srv-node-17" />
              </div>
              <div>
                <label className="field-label">Пароль / ключ</label>
                <input
                  className="field-input mono"
                  type="password"
                  defaultValue="********"
                />
              </div>
              <div>
                <label className="field-label">Срок ротации</label>
                <select className="field-input">
                  <option>30 дней</option>
                  <option>90 дней</option>
                  <option>180 дней</option>
                  <option>не ротировать</option>
                </select>
              </div>
            </div>
          </div>
          <div className="modal-footer">
            <Dialog.Close asChild>
              <button className="btn">Отмена</button>
            </Dialog.Close>
            <button className="btn btn-primary flex items-center gap-1">
              <Check className="w-4 h-4" /> Создать
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function DeleteUserModal() {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button className="btn btn-danger flex items-center gap-1">
          <UserMinus className="w-4 h-4" /> Удалить пользователя (подтверждение)
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content">
          <div className="modal-header">
            <AlertTriangle className="w-5 h-5 text-danger" />
            <Dialog.Title className="text-base font-semibold">
              Удалить пользователя alice?
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                className="ml-auto text-dim hover-bg rounded p-1"
                aria-label="закрыть"
              >
                <X className="w-4 h-4" />
              </button>
            </Dialog.Close>
          </div>
          <div className="modal-body">
            <p className="text-sm mb-3">
              Пользователь <b className="mono">alice@dbos.local</b> будет
              деактивирован. Это:
            </p>
            <ul className="list-disc list-inside text-sm text-dim space-y-1 mb-4">
              <li>отзовёт все активные сессии (3 шт)</li>
              <li>
                не удалит credentials, созданные ей (она была{" "}
                <span className="mono">created_by</span>)
              </li>
              <li>не отзовёт ботов, выпущенных от её имени</li>
              <li>запись в audit будет сохранена 90 дней по retention-политике</li>
            </ul>
            <div className="alert-block text-xs">
              Восстановить можно в течение 30 дней через{" "}
              <span className="mono">cli users restore alice</span>.
            </div>
          </div>
          <div className="modal-footer">
            <Dialog.Close asChild>
              <button className="btn">Отмена</button>
            </Dialog.Close>
            <button className="btn btn-danger-solid flex items-center gap-1">
              <UserMinus className="w-4 h-4" /> Деактивировать
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function RotateCredentialModal() {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button className="btn flex items-center gap-1">
          <RefreshCw className="w-4 h-4" /> Ротировать сейчас
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content">
          <div className="modal-header">
            <RefreshCw className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Ротация credential cred_8f2a
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                className="ml-auto text-dim hover-bg rounded p-1"
                aria-label="закрыть"
              >
                <X className="w-4 h-4" />
              </button>
            </Dialog.Close>
          </div>
          <div className="modal-body">
            <p className="text-sm mb-3">
              Будет сгенерирован <b>новый пароль</b> и применён на сервере{" "}
              <b className="mono">srv-node-17</b>.
            </p>
            <div className="grid gap-3">
              <div>
                <label className="field-label">Генерация</label>
                <select className="field-input">
                  <option>случайные 32 символа</option>
                  <option>случайные 24 символа</option>
                  <option>задать вручную</option>
                </select>
              </div>
              <div>
                <label className="field-label">Применить на BMC</label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" defaultChecked /> сразу применить и
                  проверить через login probe
                </label>
              </div>
              <div>
                <label className="field-label">
                  Сохранить старый как «previous»
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" defaultChecked /> на 24 часа, для
                  rollback
                </label>
              </div>
            </div>
          </div>
          <div className="modal-footer">
            <Dialog.Close asChild>
              <button className="btn">Отмена</button>
            </Dialog.Close>
            <button className="btn btn-primary flex items-center gap-1">
              <RefreshCw className="w-4 h-4" /> Ротировать сейчас
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
