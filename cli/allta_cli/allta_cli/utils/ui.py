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
