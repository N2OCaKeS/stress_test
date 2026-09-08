/**
 * Обёртка над `@radix-ui/react-dialog` в стиле уже существующих `.modal-*`
 * классов (см. `ConfirmDialog.tsx`) — заголовок с иконкой/подзаголовком и
 * крестиком закрытия, тело, опциональный футер с кнопками. Даёт готовый
 * портал/фокус-трап/Escape/клик-вне вместо ручной вёрстки `fixed inset-0`
 * под каждую модалку в проекте.
 *
 * `onPointerDownOutside` ниже — обязательный guard, не косметика: наши
 * собственные попапы (`Dropdown`/`HelpTooltip`) рендерятся порталом в
 * `document.body`, то есть формально вне DOM-поддерева `Dialog.Content`.
 * Без этого guard'а Radix считал бы клик по опции дропдауна снаружи модалки
 * и закрывал её раньше, чем срабатывал `onClick` опции — выглядело как
 * «клик по варианту в списке просто закрывает список/модалку, ничего не
 * выбирая». Помечаем такие попапы атрибутом `data-app-portal` и здесь же
 * это уважаем.
 */
import type { ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";

export interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** Ширина контента — по умолчанию узкая модалка (как у ConfirmDialog, 520px). */
  width?: "sm" | "md" | "lg";
  /** Скрыть крестик закрытия в шапке (например, если закрытие только через кнопки футера). */
  hideCloseButton?: boolean;
}

const WIDTH_CLASS: Record<NonNullable<ModalProps["width"]>, string> = {
  sm: "",
  md: "modal-content--md",
  lg: "modal-content--lg",
};

export function Modal({
  open,
  onOpenChange,
  title,
  subtitle,
  icon,
  children,
  footer,
  width = "sm",
  hideCloseButton = false,
}: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className={`modal-content ${WIDTH_CLASS[width]}`.trim()}
          onPointerDownOutside={(e) => {
            if ((e.target as Element | null)?.closest("[data-app-portal]")) {
              e.preventDefault();
            }
          }}
        >
          <div className="modal-header">
            {icon}
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-base font-semibold truncate">{title}</Dialog.Title>
              {subtitle && <div className="text-xs text-dim truncate">{subtitle}</div>}
            </div>
            {!hideCloseButton && (
              <Dialog.Close asChild>
                <button type="button" className="btn btn-ghost btn-sm" aria-label="Закрыть">
                  <X className="w-4 h-4" />
                </button>
              </Dialog.Close>
            )}
          </div>
          <div className="modal-body">{children}</div>
          {footer && <div className="modal-footer">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
