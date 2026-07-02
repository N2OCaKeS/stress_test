import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ShieldCheck, Download, Check, Copy } from "lucide-react";
import { Tabs } from "@/components/ui/Tabs";

/**
 * Помощник по установке корневого сертификата платформы.
 *
 * Прод стоит за TLS, выписанным внутренним CA. Пока устройство не доверяет
 * этому CA (EMM Internal CA), браузер рвёт все запросы к `/api/**` с сетевой
 * ошибкой и UI не работает. Сам сертификат раздаёт nginx web-ui по
 * `/emm-ca.crt` (см. web_ui/nginx.conf) — модалка даёт кнопку скачивания и
 * короткую инструкцию по установке под Windows / Linux / macOS.
 *
 * Открывается двумя путями: вручную ссылкой на экране логина и автоматически,
 * когда первый запрос падает с сетевой ошибкой (см. Login).
 */

const CA_URL = "/emm-ca.crt";

interface CertHelpModalProps {
  open: boolean;
  onClose: () => void;
}

type OsTab = "windows" | "linux" | "macos";

const OS_TABS = [
  { id: "windows", label: "Windows" },
  { id: "linux", label: "Linux" },
  { id: "macos", label: "macOS" },
];

/**
 * Стартовый таб. Клиентская база платформы — Astra Linux, поэтому Linux по
 * умолчанию. Windows/macOS показываем, только если браузер явно на них.
 */
function detectOsTab(): OsTab {
  if (typeof navigator === "undefined") return "linux";
  const src = `${navigator.platform ?? ""} ${navigator.userAgent ?? ""}`.toLowerCase();
  if (src.includes("win")) return "windows";
  if (src.includes("mac") || src.includes("iphone") || src.includes("ipad")) return "macos";
  return "linux";
}

/** Строка с shell-командой и кнопкой копирования. */
function CmdLine({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard недоступен (не-secure origin) — молча, команду видно глазами
    }
  };

  return (
    <div className="flex items-start gap-2 bg-[var(--bg)] border border-token rounded px-2.5 py-1.5">
      <code className="mono text-xs flex-1 break-all whitespace-pre-wrap">
        {cmd}
      </code>
      <button
        type="button"
        className="text-dim hover:text-accent shrink-0 mt-0.5"
        onClick={copy}
        title="Копировать"
      >
        {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
    </div>
  );
}

function WindowsHelp() {
  return (
    <ol className="text-sm text-dim space-y-1.5 list-decimal pl-4">
      <li>
        Двойной клик по скачанному файлу <span className="mono">emm-ca.crt</span>{" "}
        → «Установить сертификат».
      </li>
      <li>Расположение хранилища — «Локальный компьютер».</li>
      <li>
        Поместить в хранилище «Доверенные корневые центры сертификации» → «Готово».
      </li>
      <li>
        Firefox хранит доверие отдельно: Настройки → Приватность и защита →
        Сертификаты → импортировать файл в «Центры».
      </li>
      <li>Перезапустить браузер.</li>
    </ol>
  );
}

function LinuxHelp() {
  return (
    <div className="space-y-3">
      <div className="text-sm text-dim">
        Браузеры на Linux не читают системное хранилище
        (<span className="mono">update-ca-certificates</span>). Chrome, Chromium
        и Yandex доверяют NSS-хранилищу <span className="mono">~/.pki/nssdb</span>,
        Firefox — своему хранилищу в профиле. Сертификат добавляем туда через{" "}
        <span className="mono">certutil</span>.
      </div>

      <div>
        <div className="text-sm text-dim mb-1">1. Установить certutil:</div>
        <CmdLine cmd="sudo apt install -y libnss3-tools" />
      </div>

      <div>
        <div className="text-sm font-medium mb-1">Chrome / Chromium / Yandex</div>
        <div className="text-sm text-dim mb-1">
          Общий NSS-стор для всех Chromium-браузеров:
        </div>
        <CmdLine
          cmd={
            "mkdir -p ~/.pki/nssdb\n" +
            'certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "emm" -i ~/Downloads/emm-ca.crt'
          }
        />
      </div>

      <div>
        <div className="text-sm font-medium mb-1">Firefox</div>
        <div className="text-sm text-dim mb-1">
          По всем профилям сразу:
        </div>
        <CmdLine
          cmd={
            "for p in ~/.mozilla/firefox/*.default*/; do\n" +
            '  certutil -d sql:"$p" -A -t "C,," -n "emm" -i ~/Downloads/emm-ca.crt\n' +
            "done"
          }
        />
        <div className="text-sm text-dim mt-1">
          То же можно вручную: Настройки → Приватность → Сертификаты → Просмотр →
          «Центры» → Импорт → отметить «Доверять при идентификации сайтов».
        </div>
      </div>

      <div className="text-sm text-dim">
        Путь <span className="mono">~/Downloads/emm-ca.crt</span> — это куда файл
        качается кнопкой выше; подставьте свой, если сохранили в другое место.
        После добавления полностью закройте и заново откройте браузеры.
      </div>

      <div>
        <div className="text-sm text-dim mb-1">Проверка:</div>
        <CmdLine cmd="certutil -d sql:$HOME/.pki/nssdb -L | grep -i emm" />
      </div>
    </div>
  );
}

function MacosHelp() {
  return (
    <ol className="text-sm text-dim space-y-1.5 list-decimal pl-4">
      <li>
        Двойной клик по <span className="mono">emm-ca.crt</span> — откроется
        «Связка ключей» (Keychain), выбрать связку «Система».
      </li>
      <li>
        Открыть сертификат, раскрыть раздел «Доверие» и выставить «При
        использовании этого сертификата» → «Всегда доверять».
      </li>
      <li>Закрыть окно (потребуется пароль администратора) и перезапустить браузер.</li>
    </ol>
  );
}

export function CertHelpModal({ open, onClose }: CertHelpModalProps) {
  const [tab, setTab] = useState<OsTab>(detectOsTab);

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content" onOpenAutoFocus={(e) => e.preventDefault()}>
          <div className="modal-header">
            <ShieldCheck className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Установка сертификата
            </Dialog.Title>
          </div>

          <div className="modal-body">
            <Dialog.Description className="text-sm text-dim mb-4">
              Чтобы интерфейс работал без ошибок подключения, установите
              корневой сертификат в доверенные — один раз на устройство. После
              этого браузер начнёт доверять адресу платформы и вход пройдёт
              штатно.
            </Dialog.Description>

            <a
              href={CA_URL}
              download="emm-ca.crt"
              className="btn btn-primary inline-flex items-center gap-2 mb-4"
            >
              <Download className="w-4 h-4" />
              Скачать сертификат (CA)
            </a>

            <Tabs
              tabs={OS_TABS}
              active={tab}
              onChange={(id) => setTab(id as OsTab)}
              className="border-b border-token flex gap-1 mb-3"
            />

            <div className="min-h-[9rem]">
              {tab === "windows" && <WindowsHelp />}
              {tab === "linux" && <LinuxHelp />}
              {tab === "macos" && <MacosHelp />}
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose}>
              Закрыть
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
