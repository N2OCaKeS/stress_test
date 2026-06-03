#!/usr/bin/env python3
"""
mrd_load_generator.py — Python-аналог load_generator.c

Многопоточный нагрузчик HTTP с МРД-меткой (PARSEC) и Kerberos Negotiate.

  Мьютекс защищает две операции подряд:
    1. gss_init_sec_context() — GSSAPI читает ccache; конкурентный доступ
       нескольких потоков к FILE-ccache небезопасен в ряде версий MIT Kerberos.
    2. pdp_set_pid(new) → socket() → pdp_set_pid(orig) — метка процесса
       глобальна для всего процесса; без мьютекса поток B создаст сокет
       с меткой, установленной потоком A.

  I/O (connect/send/recv) выполняется вне мьютекса — потоки параллельны.

Требования:
  pip install gssapi               # или: apt install python3-gssapi
  apt install libpdp-dev           # libpdp.so
  kinit user@BALANCE.RBT
  sudo execaps -c 0x804 -- python3 mrd_load_generator.py \\
      -H 10.0.2.20 -n web1.balance.rbt -l 2 -u /lev1.html -w 10 -r 1000

Флаги:
  -H/--host     IP-адрес сервера (обязательный; -h занята --help)
  -n/--name     DNS-имя (для Host: и SPN Kerberos)
  -p/--port     TCP-порт (по умолчанию 80)
  -u/--url      Путь запроса (по умолчанию /)
  -l/--level    МРД-уровень 0..3 (по умолчанию 0)
  -w/--workers  Число потоков (по умолчанию 4)
  -r/--requests Всего запросов (по умолчанию 100)
"""

import argparse
import base64
import ctypes
import os
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# gssapi
# ─────────────────────────────────────────────────────────────────────────────

try:
    import gssapi
    _HAS_GSSAPI = True
except ImportError:
    _HAS_GSSAPI = False


# ─────────────────────────────────────────────────────────────────────────────
# libpdp — обёртка через ctypes
# ─────────────────────────────────────────────────────────────────────────────

class PDP:
    """
    Обёртка над libpdp для управления MAC-метками процесса.

    Жизненный цикл метки (счётчик ссылок):
      pdp_get_pid / pdpl_get_new_init_mac  →  счётчик увеличивается
      pdpl_put                             →  уменьшает; при 0 освобождается
    Каждый полученный/созданный указатель нужно освободить через put().
    """

    _PDPL_FMT_TXT = 0

    def __init__(self) -> None:
        lib = None
        for name in ("libpdp.so.2", "libpdp.so.1", "libpdp.so"):
            try:
                lib = ctypes.CDLL(name, use_errno=True)
                break
            except OSError:
                pass
        if lib is None:
            raise RuntimeError(
                "libpdp не найдена.\n"
                "Установите: sudo apt-get install -y libpdp-dev"
            )

        lib.pdp_init.restype               = ctypes.c_int
        lib.pdp_init.argtypes              = []

        lib.pdp_release.restype            = None
        lib.pdp_release.argtypes           = []

        # pdp_get_pid(pid_t pid) → PDPL_T*
        lib.pdp_get_pid.restype            = ctypes.c_void_p
        lib.pdp_get_pid.argtypes           = [ctypes.c_int]

        # pdp_set_pid(pid_t pid, PDPL_T*) → int
        lib.pdp_set_pid.restype            = ctypes.c_int
        lib.pdp_set_pid.argtypes           = [ctypes.c_int, ctypes.c_void_p]

        # pdpl_ilev(PDPL_T*) → PDP_ILEV_T (uint32)
        lib.pdpl_ilev.restype              = ctypes.c_uint32
        lib.pdpl_ilev.argtypes             = [ctypes.c_void_p]

        # pdpl_get_new_init_mac(level, ilev, lin_ilev, cats, type) → PDPL_T*
        lib.pdpl_get_new_init_mac.restype  = ctypes.c_void_p
        lib.pdpl_get_new_init_mac.argtypes = [
            ctypes.c_uint32,   # PDP_LEV_T  level
            ctypes.c_uint32,   # PDP_ILEV_T ilev
            ctypes.c_uint32,   # lin_ilev
            ctypes.c_uint64,   # PDP_CAT_T  cats
            ctypes.c_uint32,   # PDP_TYPE_T type
        ]

        lib.pdpl_put.restype               = None
        lib.pdpl_put.argtypes              = [ctypes.c_void_p]

        lib.pdpl_get_text.restype          = ctypes.c_char_p
        lib.pdpl_get_text.argtypes         = [ctypes.c_void_p, ctypes.c_int]

        self._lib = lib

        if lib.pdp_init() != 0:
            raise RuntimeError(f"pdp_init() ошибка: {os.strerror(ctypes.get_errno())}")

    # --- публичный API, точно соответствующий C-функциям ---

    def get_pid(self) -> int:
        """pdp_get_pid(0) → указатель на метку процесса. Нужно put()."""
        ptr = self._lib.pdp_get_pid(0)
        if not ptr:
            raise RuntimeError(f"pdp_get_pid(0) NULL: {os.strerror(ctypes.get_errno())}")
        return ptr

    def set_pid(self, ptr: int) -> None:
        """pdp_set_pid(0, ptr). Требует PARSEC_CAP_SETMAC."""
        if self._lib.pdp_set_pid(0, ptr) != 0:
            raise PermissionError(
                f"pdp_set_pid() ошибка: {os.strerror(ctypes.get_errno())}\n"
                "Запускайте через: sudo execaps -c 0x804 -- python3 ..."
            )

    def ilev(self, ptr: int) -> int:
        """pdpl_ilev(ptr) — уровень целостности метки."""
        return int(self._lib.pdpl_ilev(ptr))

    def new_mac(self, level: int, ilev: int) -> int:
        """pdpl_get_new_init_mac(level, ilev, 0, 0, 0) → PDPL_T*. Нужно put()."""
        ptr = self._lib.pdpl_get_new_init_mac(level, ilev, 0, 0, 0)
        if not ptr:
            raise RuntimeError(
                f"pdpl_get_new_init_mac() NULL: {os.strerror(ctypes.get_errno())}"
            )
        return ptr

    def put(self, ptr: int) -> None:
        """pdpl_put(ptr) — уменьшить счётчик ссылок."""
        if ptr:
            self._lib.pdpl_put(ptr)

    def label_text(self, ptr: int) -> str:
        """pdpl_get_text(ptr, PDPL_FMT_TXT)."""
        raw = self._lib.pdpl_get_text(ptr, self._PDPL_FMT_TXT)
        return raw.decode("utf-8", errors="replace") if raw else "?"

    def release(self) -> None:
        """pdp_release()."""
        self._lib.pdp_release()


# ─────────────────────────────────────────────────────────────────────────────
# GSSAPI — одноразовый Negotiate-токен
# ─────────────────────────────────────────────────────────────────────────────

def get_negotiate_token(hostname: str, verbose: bool = False) -> Optional[str]:
    """
    Python-аналог get_negotiate_token_ex() из load_generator.c.

    gss_import_name(HTTP@<hostname>) → gss_init_sec_context() → base64.
    ST берётся из ccache (kinit), без обращения к KDC.
    Токен ОДНОРАЗОВЫЙ — Apache отклоняет повторное использование (replay).
    Вызывать под g_label_mutex: конкурентный доступ к ccache небезопасен.
    """
    if not _HAS_GSSAPI:
        return None
    spn = f"HTTP@{hostname}"
    if verbose:
        print(f"[gss]  SPN: {spn}")
    try:
        name = gssapi.Name(spn, gssapi.NameType.hostbased_service)
        # GSS_C_SEQUENCE_FLAG:
        flags = [gssapi.RequirementFlag.mutual_authentication]
        for _seq_name in ("out_of_sequence_detection", "sequence_detection", "sequence"):
            try:
                flags.append(getattr(gssapi.RequirementFlag, _seq_name))
                break
            except AttributeError:
                pass

        ctx  = gssapi.SecurityContext(
            name=name,
            flags=flags,
            usage="initiate",
        )
        token_bytes = ctx.step()
        if not token_bytes:
            return None
        if verbose:
            print(f"[gss]  Токен получен, размер: {len(token_bytes)} байт")
        return base64.b64encode(token_bytes).decode("ascii")
    except gssapi.exceptions.GSSError as exc:
        if verbose:
            print(f"[gss]  gss_init_sec_context() ошибка: {exc}", file=sys.stderr)
            print("       Выполните: kinit user@BALANCE.RBT", file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Коды возврата do_one_request
# ─────────────────────────────────────────────────────────────────────────────

ERR_LABEL   = -1   # ошибка метки / socket()
ERR_CONNECT = -2   # connect() не удался
ERR_IO      = -3   # send/recv или пустой ответ
ERR_AUTH    = -4   # Kerberos-токен не получен


# ─────────────────────────────────────────────────────────────────────────────
# do_one_request
# ─────────────────────────────────────────────────────────────────────────────

def do_one_request(
    ip: str, port: int, hostname: str,
    url: str, level: int,
    pdp: PDP,
    label_mutex: threading.Lock,
) -> int:
    """
    Выполняет один HTTP GET с МРД-меткой и Kerberos Negotiate.
    Возвращает HTTP-статус или ERR_*.

    Последовательность под мьютексом (аналог load_generator.c):
      1. get_negotiate_token()          — свежий одноразовый токен
      2. pdp_get_pid(0)                 — сохранить метку процесса
      3. pdpl_get_new_init_mac(level,…) — создать метку с нужным МРД-уровнем
      4. pdp_set_pid(0, new_label)      — применить к процессу
      5. socket()                       — сокет наследует метку процесса
      6. pdp_set_pid(0, orig_label)     — немедленно восстановить метку процесса
      7. pdpl_put(new) / pdpl_put(orig) — освободить метки
    I/O — вне мьютекса.
    """
    t0 = time.monotonic()

    # ── Критическая секция ────────────────────────────────────────────────────
    token:    Optional[str] = None
    sockfd:   Optional[socket.socket] = None
    orig_ptr: int = 0
    new_ptr:  int = 0

    with label_mutex:
        # 1. Kerberos-токен под мьютексом
        token = get_negotiate_token(hostname, verbose=False)

        # 2. Читаем текущую метку процесса
        try:
            orig_ptr = pdp.get_pid()
        except Exception:
            return ERR_LABEL

        # 3. Создаём метку с нужным МРД-уровнем
        try:
            cur_ilev = pdp.ilev(orig_ptr)
            new_ptr  = pdp.new_mac(level, cur_ilev)
        except Exception:
            pdp.put(orig_ptr)
            return ERR_LABEL

        # 4. Применяем метку к процессу
        try:
            pdp.set_pid(new_ptr)
        except PermissionError:
            pdp.put(new_ptr)
            pdp.put(orig_ptr)
            return ERR_LABEL

        # 5. Создаём сокет — он наследует метку процесса
        try:
            sockfd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            sockfd = None

        # 6. Немедленно восстанавливаем метку процесса (сокет уже получил свою)
        try:
            pdp.set_pid(orig_ptr)
        except Exception:
            pass

        # 7. Освобождаем метки
        pdp.put(new_ptr);  new_ptr  = 0
        pdp.put(orig_ptr); orig_ptr = 0
    # ── Конец критической секции ──────────────────────────────────────────────

    if sockfd is None:
        return ERR_LABEL

    # Без токена сервер вернёт 401 — не тратим соединение
    if token is None:
        sockfd.close()
        return ERR_AUTH

    # ── I/O вне мьютекса — потоки работают параллельно ───────────────────────

    try:
        sockfd.settimeout(15)
        sockfd.connect((ip, port))
    except OSError:
        sockfd.close()
        return ERR_CONNECT

    req = (
        f"GET {url} HTTP/1.0\r\n"
        f"Host: {hostname}\r\n"
        f"Authorization: Negotiate {token}\r\n"
        f"X-MRD-Level: {level}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("ascii")

    try:
        sockfd.sendall(req)
    except OSError:
        sockfd.close()
        return ERR_IO

    # Читаем до EOF (HTTP/1.0 + Connection: close — сервер закрывает сам)
    try:
        chunks = []
        while True:
            chunk = sockfd.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError:
        sockfd.close()
        return ERR_IO
    finally:
        sockfd.close()

    if not chunks:
        return ERR_IO

    response = b"".join(chunks).decode("latin-1", errors="replace")

    # sscanf(response, "HTTP/%*s %d", &http_status)
    parts = response.split(" ", 2)
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return ERR_IO


# ─────────────────────────────────────────────────────────────────────────────
# Статистика (thread-safe)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Stats:
    _lock:      threading.Lock = field(default_factory=threading.Lock)
    sent:       int = 0
    ok:         int = 0
    forbidden:  int = 0
    unauth:     int = 0
    err_label:  int = 0
    err_conn:   int = 0
    err_io:     int = 0
    err_auth:   int = 0
    other:      int = 0
    total_usec: int = 0

    def record(self, status: int, usec: int) -> int:
        """Записывает результат запроса. Возвращает порядковый номер (sent)."""
        with self._lock:
            self.sent       += 1
            self.total_usec += usec
            if   status == 200:         self.ok        += 1
            elif status == 403:         self.forbidden += 1
            elif status == 401:         self.unauth    += 1
            elif status == ERR_LABEL:   self.err_label += 1
            elif status == ERR_CONNECT: self.err_conn  += 1
            elif status == ERR_IO:      self.err_io    += 1
            elif status == ERR_AUTH:    self.err_auth  += 1
            else:                       self.other     += 1
            return self.sent


# ─────────────────────────────────────────────────────────────────────────────
# Функция потока
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_STR = {
    200:         "200 OK",
    401:         "401 Unauthorized",
    403:         "403 Forbidden",
    ERR_AUTH:    "ERR_AUTH (kinit?)",
    ERR_CONNECT: "ERR_CONNECT",
    ERR_IO:      "ERR_IO",
    ERR_LABEL:   "ERR_LABEL",
}


def worker_thread(
    ip: str, port: int, hostname: str, url: str, level: int,
    pdp: PDP,
    label_mutex: threading.Lock,
    work_queue: "queue.Queue[int]",
    stats: Stats,
) -> None:
    while True:
        try:
            work_queue.get(block=False)
        except queue.Empty:
            return

        t0     = time.monotonic()
        status = do_one_request(ip, port, hostname, url, level, pdp, label_mutex)
        usec   = int((time.monotonic() - t0) * 1_000_000)

        seq = stats.record(status, usec)

        # Первые 20 запросов — построчно (аналог C: if (done <= 20))
        if seq <= 20:
            label = _STATUS_STR.get(status, f"HTTP {status}")
            print(f"[req#{seq:03d}] {label}")

        work_queue.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Нагрузчик HTTP с МРД-меткой (PARSEC) и Kerberos Negotiate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Пример:\n"
            "  kinit user@BALANCE.RBT\n"
            "  sudo execaps -c 0x804 -- python3 mrd_load_generator.py \\\n"
            "      -H 10.0.2.20 -n web1.balance.rbt -l 2 -u /lev1.html -w 10 -r 1000\n\n"
            "Примечание: IP задаётся через -H (не -h: та занята --help)."
        ),
    )
    ap.add_argument("-H", "--host",     required=True,          metavar="IP",
                    help="IP-адрес сервера (для connect)")
    ap.add_argument("-n", "--name",     default="",             metavar="HOSTNAME",
                    help="DNS-имя сервера (для Host: и SPN Kerberos)")
    ap.add_argument("-p", "--port",     default=80,  type=int,  metavar="PORT",
                    help="TCP-порт (по умолчанию 80)")
    ap.add_argument("-u", "--url",      default="/",            metavar="PATH",
                    help="Путь запроса (по умолчанию /)")
    ap.add_argument("-l", "--level",    default=0,   type=int,  metavar="0..3",
                    help="МРД-уровень конфиденциальности (по умолчанию 0)")
    ap.add_argument("-w", "--workers",  default=4,   type=int,  metavar="N",
                    help="Число параллельных потоков (по умолчанию 4)")
    ap.add_argument("-r", "--requests", default=100, type=int,  metavar="N",
                    help="Общее число запросов (по умолчанию 100)")
    args = ap.parse_args()

    ip       = args.host
    hostname = args.name or args.host
    if not args.name:
        print(
            "[warn] -n/--name не задан, используем IP как hostname.\n"
            "       Kerberos работает только с DNS-именем: -n web1.balance.rbt",
            file=sys.stderr,
        )

    if not (0 <= args.level <= 3):
        sys.exit("Ошибка: уровень МРД (-l) должен быть 0..3")
    if args.workers < 1:
        sys.exit("Ошибка: -w должен быть >= 1")
    if args.requests < 1:
        sys.exit("Ошибка: -r должен быть >= 1")

    print("══════════════════════════════════════════════")
    print("  mrd_load_generator.py: МРД нагрузчик")
    print("══════════════════════════════════════════════")
    print(f"[conf] IP:          {ip}:{args.port}")
    print(f"[conf] Hostname:    {hostname}")
    print(f"[conf] URL:         {args.url}")
    print(f"[conf] МРД уровень: {args.level}")
    print(f"[conf] Потоков:     {args.workers}")
    print(f"[conf] Запросов:    {args.requests}")
    print("──────────────────────────────────────────────")

    # 1. Инициализация libpdp
    try:
        pdp = PDP()
    except RuntimeError as exc:
        sys.exit(f"[pdp]  {exc}")

    # Метка процесса при старте (аналог C: pdp_get_pid → pdpl_get_text)
    try:
        self_ptr = pdp.get_pid()
        print(f"[pdp]  Метка процесса (начальная): \"{pdp.label_text(self_ptr)}\"")
        cur_ilev = pdp.ilev(self_ptr)
    except Exception as exc:
        pdp.release()
        sys.exit(f"[pdp]  {exc}")

    # Preflight: проверяем pdp_set_pid + наследование метки сокетом
    label_mutex = threading.Lock()
    new_ptr: int = 0
    try:
        new_ptr = pdp.new_mac(args.level, cur_ilev)
        pdp.set_pid(new_ptr)
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        pdp.set_pid(self_ptr)       # немедленно восстановить
        test_sock.close()
        pdp.put(new_ptr); new_ptr = 0
        print("[pdp]  pdp_set_pid() + socket() — OK\n")
    except PermissionError as exc:
        pdp.put(self_ptr)
        if new_ptr:
            pdp.put(new_ptr)
        pdp.release()
        sys.exit(f"[pdp]  {exc}")
    finally:
        pdp.put(self_ptr)
        if new_ptr:
            pdp.put(new_ptr)

    # 2. Проверяем Kerberos (аналог C: один пробный вызов при старте)
    if not _HAS_GSSAPI:
        print(
            "[warn] Модуль gssapi не найден. Установите: pip install gssapi\n"
            "       Запросы пойдут без аутентификации (ожидайте 401).",
            file=sys.stderr,
        )
    else:
        probe = get_negotiate_token(hostname, verbose=True)
        if probe:
            print("[gss]  Kerberos OK. Каждый запрос получит свой токен.\n")
        else:
            print(
                "[warn] Kerberos-токен не получен — запросы пойдут без аутентификации\n"
                "       (сервер вернёт 401)\n",
                file=sys.stderr,
            )

    print(f"[load] Запускаю {args.workers} потоков, всего {args.requests} запросов...")

    # 3. Заполняем очередь задач и запускаем воркеры
    stats: Stats = Stats()
    wq: "queue.Queue[int]" = queue.Queue()
    for i in range(args.requests):
        wq.put(i)

    t_wall_start = time.monotonic()
    threads = []
    for _ in range(args.workers):
        t = threading.Thread(
            target=worker_thread,
            args=(ip, args.port, hostname, args.url, args.level,
                  pdp, label_mutex, wq, stats),
            daemon=True,
        )
        t.start()
        threads.append(t)

    # 4. Ждём завершения всех потоков
    for t in threads:
        t.join()

    wall_sec = time.monotonic() - t_wall_start

    # 5. Итоговая статистика
    err_total = stats.err_label + stats.err_conn + stats.err_io + stats.err_auth
    rps    = stats.sent / wall_sec if wall_sec > 0 else 0.0
    avg_ms = (stats.total_usec / stats.sent / 1000) if stats.sent > 0 else 0.0

    print()
    print("══════════════════════════════════════════════")
    print("  РЕЗУЛЬТАТЫ")
    print("══════════════════════════════════════════════")
    print(f"[stat] Всего:             {stats.sent}")
    print(f"[stat] 200 OK:            {stats.ok}")
    print(f"[stat] 403 Forbidden:     {stats.forbidden}")
    print(f"[stat] 401 Unauthorized:  {stats.unauth}")
    print(f"[stat] Другие статусы:    {stats.other}")
    if err_total > 0:
        print(f"[stat] Ошибки итого:      {err_total}")
        auth_hint = "  ← выполните kinit заново!" if stats.err_auth > 0 else ""
        print(f"[stat]   Kerberos-токен:  {stats.err_auth}{auth_hint}")
        print(f"[stat]   метка/сокет:     {stats.err_label}")
        print(f"[stat]   connect():       {stats.err_conn}")
        print(f"[stat]   send/recv:       {stats.err_io}")
    print("──────────────────────────────────────────────")
    print(f"[stat] Время выполнения:  {wall_sec:.2f} сек")
    print(f"[stat] Скорость:          {rps:.1f} req/s")
    print(f"[stat] Средняя latency:   {avg_ms:.1f} мс")
    print("══════════════════════════════════════════════")

    pdp.release()


if __name__ == "__main__":
    main()
