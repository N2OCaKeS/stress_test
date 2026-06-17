/**
 * Пара зависимых селектов «сервис → действие» для формы правила аудита.
 *
 * `match_service` берётся из реестра сервисов (`listServices`), `match_action`
 * — из каталога действий выбранного сервиса (`listServiceEvents`). Пока сервис
 * не выбран («любой»), действие принудительно пусто и селект заблокирован:
 * действия привязаны к сервису, иначе можно собрать невалидную пару.
 *
 * Каталог действий грузится лениво — только когда выбран конкретный сервис.
 * При смене сервиса action сбрасывается, если прежнее значение отсутствует в
 * новом каталоге. Пустая строка наружу = null (правило матчит «любое»).
 *
 * Значения и формат сабмита не меняются: компонент остаётся управляемым через
 * строковые `service`/`action` родителя.
 */
import { useEffect } from "react";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { listServiceEvents, listServices } from "@/api/loging/services";
import { HelpTooltip } from "@/components/ui/HelpTooltip";

export function ServiceActionSelect({
  service,
  action,
  onServiceChange,
  onActionChange,
  selectClassName = "input",
}: {
  service: string;
  action: string;
  onServiceChange: (value: string) => void;
  onActionChange: (value: string) => void;
  /** CSS-класс <select> — у двух форм он отличается. */
  selectClassName?: string;
}) {
  const servicesQ = useQuery(() => listServices(), []);
  const services = servicesQ.data?.items ?? [];

  const svc = service.trim();
  const actionsQ = useQuery(
    () => (svc ? listServiceEvents(svc, { limit: 200 }) : Promise.resolve(null)),
    [svc],
    { enabled: !!svc },
  );
  const actionOptions = actionsQ.data?.items ?? [];
  const catalogLoaded = !!svc && !!actionsQ.data;

  // Сбрасываем action, если он не из каталога нового сервиса: смена сервиса не
  // должна оставлять «висящее» действие, которого там нет. Каталог сбрасываем
  // только когда он непустой — если у сервиса ноль зарегистрированных действий
  // (или сервис пришёл из старого правила и его нет в реестре), сохранённое
  // значение не трогаем. В deps — сам ответ запроса: при смене сервиса данные
  // приходят асинхронно, и эффект должен пересчитаться на свежий каталог.
  useEffect(() => {
    if (!svc) {
      if (action) onActionChange("");
      return;
    }
    if (!actionsQ.data || actionOptions.length === 0) return;
    if (action && !actionOptions.some((a) => a.action === action)) {
      onActionChange("");
    }
    // onActionChange стабилен у вызывающих (useState-сеттер).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [svc, actionsQ.data, action]);

  const actionDisabled = !svc;

  return (
    <>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs flex items-center gap-1">
          match_service
          <HelpTooltip
            text="Сервис-источник события. Пусто — правило применяется к событиям любого сервиса."
            label="Справка: match_service"
          />
        </span>
        <select
          className={selectClassName}
          value={service}
          onChange={(e) => onServiceChange(e.target.value)}
          disabled={servicesQ.loading}
        >
          <option value="">(любой сервис)</option>
          {/* Сервис из правила мог исчезнуть из реестра — добавляем его
              отдельной опцией, чтобы не потерять выбор при редактировании. */}
          {svc && !services.some((s) => s.service === svc) && (
            <option value={svc}>{svc}</option>
          )}
          {services.map((s) => (
            <option key={s.service} value={s.service}>
              {s.service}
            </option>
          ))}
        </select>
        {servicesQ.error && (
          <span className="text-[11px] text-danger">
            {apiErrMsg(servicesQ.error, "Список сервисов не загрузился")}
          </span>
        )}
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs flex items-center gap-1">
          match_action
          <HelpTooltip
            text="Действие события (например user.login). Список зависит от выбранного сервиса. Пусто — любое действие сервиса."
            label="Справка: match_action"
          />
        </span>
        <select
          className={selectClassName}
          value={action}
          onChange={(e) => onActionChange(e.target.value)}
          disabled={actionDisabled || actionsQ.loading}
        >
          <option value="">(любое действие)</option>
          {/* Сохранённое действие, которого нет в каталоге, держим в списке. */}
          {action && !actionOptions.some((a) => a.action === action) && (
            <option value={action}>{action}</option>
          )}
          {actionOptions.map((a) => (
            <option key={a.action} value={a.action}>
              {a.action}
              {a.default_severity ? ` · ${a.default_severity}` : ""}
            </option>
          ))}
        </select>
        {catalogLoaded && actionOptions.length === 0 && (
          <span className="text-[11px] text-dim">(нет зарегистрированных действий)</span>
        )}
        {actionsQ.error && (
          <span className="text-[11px] text-danger">
            {apiErrMsg(actionsQ.error, "Каталог действий не загрузился")}
          </span>
        )}
      </label>
    </>
  );
}
