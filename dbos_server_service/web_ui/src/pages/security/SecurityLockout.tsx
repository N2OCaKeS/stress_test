import { LockOpen } from "lucide-react";

/**
 * Lockout — успешная попытка авторизации сама снимает блокировку. Явного
 * админского endpoint'а `POST /lockout/reset` в auth_service нет: блокировка
 * пишется в `users.locked_until` / `bot_accounts.locked_until` /
 * `oauth_clients.locked_until` и истекает по времени или сбрасывается на
 * первой успешной аутентификации.
 *
 * Эта страница описывает поведение и объясняет, что доступного UI-действия
 * сейчас нет — на случай инцидента админ либо ждёт окончания окна (15 мин по
 * умолчанию), либо меняет пароль/секрет принципалу через соответствующий
 * endpoint (`POST /users/{id}/reset-password`, `POST /bots/{id}/tokens`,
 * пересоздать OAuth-клиента).
 */
export function SecurityLockout() {
  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <h3 className="font-semibold flex items-center gap-2 mb-3">
          <LockOpen className="w-4 h-4 text-accent" /> Lockout — как сейчас работает
        </h3>
        <div className="text-sm text-dim space-y-2">
          <p>
            Auth_service фиксирует неудачные попытки секретов в счётчиках
            принципалов: <span className="mono">users.failed_login_attempts</span>,{" "}
            <span className="mono">bot_accounts.failed_token_attempts</span>,{" "}
            <span className="mono">oauth_clients.failed_secret_attempts</span>.
          </p>
          <p>
            При достижении порога (по умолчанию <b>5 попыток</b>) выставляется
            поле <span className="mono">locked_until = now + LOCKOUT_MINUTES</span>{" "}
            (по умолчанию <b>15 мин</b>). Дальнейшие попытки получают{" "}
            <span className="mono">429 ACCOUNT_TEMPORARILY_LOCKED</span> с{" "}
            <span className="mono">retry_after_seconds</span> в деталях.
          </p>
          <p>
            Способы снять блокировку:
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li>дождаться истечения окна — следующая попытка автоматически разлочит запись (CAS-release);</li>
            <li>
              успешный логин юзера / выдача bot-токена админом / новый
              `client_credentials` запрос с правильным секретом — сбросит
              счётчик и снимет lockout;
            </li>
            <li>
              сменить секрет принципалу: для юзера — <span className="mono">POST /users/{`{id}`}/reset-password</span>;
              для бота — выпустить новый токен через{" "}
              <span className="mono">POST /bots/{`{id}`}/tokens</span> и удалить старый;
              для OAuth-клиента — удалить (`DELETE /oauth2/clients/{`{id}`}`) и пересоздать.
            </li>
          </ul>
        </div>
      </div>
      <div className="card">
        <h3 className="font-semibold mb-2">Что отсутствует в API</h3>
        <div className="text-sm text-dim">
          В контракте <span className="mono">auth_service/API_ENDPOINTS.md</span>{" "}
          нет явного admin-endpoint'а вида <span className="mono">POST /admin/lockout/reset</span>.
          Если такой будет добавлен — кнопка ручного сброса появится здесь.
        </div>
      </div>
    </div>
  );
}
