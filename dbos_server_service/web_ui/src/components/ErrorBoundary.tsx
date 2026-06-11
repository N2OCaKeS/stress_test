import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Ловит исключения из рендера дочернего дерева, чтобы один упавший компонент
 * не схлопывал всё приложение в белый экран. В dev показываем текст ошибки,
 * в prod — только заглушку с предложением перезагрузить.
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Без внешнего sink логировать некуда — пишем в консоль, чтобы стек был
    // виден в devtools и не терялся.
    console.error("ErrorBoundary поймал исключение в рендере:", error, info);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    const showDetails = Boolean(import.meta.env.DEV);

    return (
      <div className="flex-1 flex items-start justify-center p-8">
        <div className="error-card" role="alert">
          <h2 className="text-lg font-semibold mb-2">Что-то сломалось</h2>
          <p className="text-dim text-sm mb-4">
            Страница не отрисовалась из-за ошибки. Попробуйте перезагрузить.
          </p>
          {showDetails && (
            <pre className="surface-2 border border-token rounded p-2 text-xs overflow-auto mb-4">
              {error.message}
              {error.stack ? `\n${error.stack}` : ""}
            </pre>
          )}
          <button
            type="button"
            className="btn btn-primary"
            onClick={this.handleReload}
          >
            Перезагрузить
          </button>
        </div>
      </div>
    );
  }
}
