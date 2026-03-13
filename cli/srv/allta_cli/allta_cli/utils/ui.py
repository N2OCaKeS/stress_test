from __future__ import annotations
from contextlib import contextmanager

SEP = "─" * 60

def _secho(msg: str, *, fg: str | None = None) -> None:
    try:
        import click
        click.secho(msg, fg=fg)
    except Exception:
        print(msg)

# Базовые принтеры
def echo(msg: str):          _secho(msg, fg=None)           # обычный текст
def ok(msg: str):            _secho(f"✓ {msg}", fg="green") # успешное действие
def err(msg: str):           _secho(f"✗ {msg}", fg="red")   # ошибка
def warn(msg: str):          _secho(f"⚠ {msg}", fg="yellow")# предупреждение
def step(msg: str):          _secho(f"⟶ {msg}", fg="yellow")# шаг сценария
def http(msg: str):          _secho(f"→ {msg}", fg="blue")  # сетевое действие
def cmd(msg: str):           _secho(f"$ {msg}", fg="blue")  # запуск команды
def table(headers: list[str], rows: list[list[object]]):
    """
    Простой вывод таблицы без зависимостей.
    """
    cols = len(headers)
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i in range(min(cols, len(row))):
            widths[i] = max(widths[i], len(str(row[i])))

    def _fmt_line(cells):
        return " | ".join(str(cells[i]).ljust(widths[i]) for i in range(cols))

    header_line = _fmt_line(headers)
    sep_line = "-+-".join("-" * widths[i] for i in range(cols))
    _secho(header_line, fg="magenta")
    print(sep_line)
    for row in rows:
        print(_fmt_line(row))

def header(title: str):
    print(SEP); _secho(title, fg="magenta"); print(SEP)

def footer(title: str | None = None):
    if title: _secho(title, fg="magenta")
    print(SEP)

@contextmanager
def section(title: str, end: str | None = None):
    header(title)
    try:
        yield
    finally:
        footer(end or f"{title} (end)")
