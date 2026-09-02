import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
  copied: boolean;
}

/**
 * Ловит исключения из рендера дочернего дерева, чтобы один упавший компонент
 * не схлопывал всё приложение в белый экран. Текст ошибки (`message`) виден
 * всегда — и в dev, и в prod: без него пользователь не может сказать, что
 * именно сломалось. Полный стек показываем только в dev, чтобы не пугать
 * длинным трейсом; в prod его можно забрать кнопкой «Скопировать ошибку».
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { error: null, copied: false };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error, copied: false };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Без внешнего sink логировать некуда — пишем в консоль, чтобы стек был
    // виден в devtools и не терялся.
    console.error("ErrorBoundary поймал исключение в рендере:", error, info);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  private handleCopy = async (): Promise<void> => {
    const { error } = this.state;
    if (!error) return;
    const text = error.stack
      ? `${error.message}\n\n${error.stack}`
      : error.message;
    try {
      await navigator.clipboard.writeText(text);
      this.setState({ copied: true });
      window.setTimeout(() => this.setState({ copied: false }), 2000);
    } catch {
      // clipboard может быть недоступен (нет https / отказ прав) — молча
      // игнорируем, текст всё равно виден в блоке ниже и его можно выделить.
    }
  };

  render(): ReactNode {
    const { error, copied } = this.state;
    if (!error) return this.props.children;

    const showStack = Boolean(import.meta.env.DEV) && Boolean(error.stack);
    const message = error.message || "Неизвестная ошибка без описания.";

    return (
      <div className="flex-1 flex items-start justify-center p-8">
        <div className="error-card" role="alert">
          <h2 className="text-lg font-semibold mb-2">
            Страница не отрисовалась
          </h2>
          <p className="text-dim text-sm mb-3">
            Компонент упал с ошибкой. Текст ниже поможет понять причину —
            приложите его, если будете сообщать о проблеме.
          </p>
          <pre className="surface-2 border border-token rounded p-2 text-xs overflow-auto mb-4 whitespace-pre-wrap break-words">
            {message}
            {showStack ? `\n\n${error.stack}` : ""}
          </pre>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn btn-primary"
              onClick={this.handleReload}
            >
              Перезагрузить
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => {
                void this.handleCopy();
              }}
            >
              {copied ? "Скопировано" : "Скопировать ошибку"}
            </button>
          </div>
        </div>
      </div>
    );
  }
}
