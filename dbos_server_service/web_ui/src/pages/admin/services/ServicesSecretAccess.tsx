import { Link } from "react-router-dom";
import { ShieldCheck, ExternalLink } from "lucide-react";

/**
 * Honest pointer for the admin "secret" section.
 *
 * secret_service не имеет отдельных policies/templates-эндпоинтов: реальная
 * админка — это per-credential Role-ACL и Dept-Grant, и она живёт на штатной
 * странице /secret (SecretLive). Раньше здесь были две backendless-заглушки
 * (политики ротации / шаблоны типов) — их убрали, чтобы не показывать
 * выдуманные сущности.
 */
export function ServicesSecretAccess() {
  return (
    <div className="flex-1 min-h-0 flex items-center justify-center p-8">
      <div className="card max-w-xl flex flex-col gap-3">
        <h3 className="font-semibold flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-accent" /> Управление доступом к секретам
        </h3>
        <div className="text-sm text-dim">
          В secret_service нет глобальных «политик ротации» или «шаблонов типов».
          Доступ настраивается по каждой credential отдельно: Role-ACL (роли
          сервиса) и Dept-Grant (выдачи департаментам). Эти инструменты
          встроены в карточку credential на штатной странице.
        </div>
        <div>
          <Link to="/secret" className="btn btn-primary inline-flex items-center gap-2">
            <ExternalLink className="w-4 h-4" /> Открыть Secret
          </Link>
        </div>
      </div>
    </div>
  );
}
