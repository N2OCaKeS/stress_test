#!/usr/bin/env python3
"""
mrd_test_load_generator.py — версия с выводом тела ответа

Аналог mrd_load_generator.py, но do_one_request() возвращает также
полный HTTP-ответ, и воркер выводит заголовки + тело (первые 512 байт)
для первых 20 запросов — чтобы убедиться, что сервер отвечает корректно.

Запуск:
  kinit user@BALANCE.RBT
  sudo execaps -c 0x804 -- python3 mrd_test_load_generator.py \\
      -H 10.0.2.20 -n web1.balance.rbt -l 2 -u /lev1.html -w 10 -r 1000
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
from typing import Optional, Tuple

try:
    import gssapi
    _HAS_GSSAPI = True
except ImportError:
    _HAS_GSSAPI = False


# ─────────────────────────────────────────────────────────────────────────────
# libpdp — обёртка через ctypes
# ─────────────────────────────────────────────────────────────────────────────

class PDP:
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
        lib.pdp_get_pid.restype            = ctypes.c_void_p
        lib.pdp_get_pid.argtypes           = [ctypes.c_int]
        lib.pdp_set_pid.restype            = ctypes.c_int
        lib.pdp_set_pid.argtypes           = [ctypes.c_int, ctypes.c_void_p]
        lib.pdpl_ilev.restype              = ctypes.c_uint32
        lib.pdpl_ilev.argtypes             = [ctypes.c_void_p]
        lib.pdpl_get_new_init_mac.restype  = ctypes.c_void_p
        lib.pdpl_get_new_init_mac.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint64, ctypes.c_uint32,
        ]
        lib.pdpl_put.restype               = None
        lib.pdpl_put.argtypes              = [ctypes.c_void_p]
        lib.pdpl_get_text.restype          = ctypes.c_char_p
        lib.pdpl_get_text.argtypes         = [ctypes.c_void_p, ctypes.c_int]

        self._lib = lib
        if lib.pdp_init() != 0:
            raise RuntimeError(f"pdp_init() ошибка: {os.strerror(ctypes.get_errno())}")

    def get_pid(self) -> int:
        ptr = self._lib.pdp_get_pid(0)
        if not ptr:
            raise RuntimeError(f"pdp_get_pid(0) NULL: {os.strerror(ctypes.get_errno())}")
        return ptr

    def set_pid(self, ptr: int) -> None:
        if self._lib.pdp_set_pid(0, ptr) != 0:
            raise PermissionError(
                f"pdp_set_pid() ошибка: {os.strerror(ctypes.get_errno())}\n"
                "Запускайте через: sudo execaps -c 0x804 -- python3 ..."
            )

    def ilev(self, ptr: int) -> int:
        return int(self._lib.pdpl_ilev(ptr))

    def new_mac(self, level: int, ilev: int) -> int:
        ptr = self._lib.pdpl_get_new_init_mac(level, ilev, 0, 0, 0)
        if not ptr:
            raise RuntimeError(
                f"pdpl_get_new_init_mac() NULL: {os.strerror(ctypes.get_errno())}"
            )
        return ptr

    def put(self, ptr: int) -> None:
        if ptr:
            self._lib.pdpl_put(ptr)

    def label_text(self, ptr: int) -> str:
        raw = self._lib.pdpl_get_text(ptr, self._PDPL_FMT_TXT)
        return raw.decode("utf-8", errors="replace") if raw else "?"

    def release(self) -> None:
        self._lib.pdp_release()


# ─────────────────────────────────────────────────────────────────────────────
# GSSAPI — одноразовый Negotiate-токен
# ─────────────────────────────────────────────────────────────────────────────

def get_negotiate_token(hostname: str, verbose: bool = False) -> Optional[str]:
    if not _HAS_GSSAPI:
        return None
    spn = f"HTTP@{hostname}"
    if verbose:
        print(f"[gss]  SPN: {spn}")
    try:
        name = gssapi.Name(spn, gssapi.NameType.hostbased_service)
        flags = [gssapi.RequirementFlag.mutual_authentication]
        for _seq_name in ("out_of_sequence_detection", "sequence_detection", "sequence"):
            try:
                flags.append(getattr(gssapi.RequirementFlag, _seq_name))
                break
            except AttributeError:
                pass
        ctx = gssapi.SecurityContext(name=name, flags=flags, usage="initiate")
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
# Коды возврата
# ─────────────────────────────────────────────────────────────────────────────

ERR_LABEL   = -1
ERR_CONNECT = -2
ERR_IO      = -3
ERR_AUTH    = -4


# ─────────────────────────────────────────────────────────────────────────────
# do_one_request — возвращает (status, response_text)
# ─────────────────────────────────────────────────────────────────────────────

def do_one_request(
    ip: str, port: int, hostname: str,
    url: str, level: int,
    pdp: PDP,
    label_mutex: threading.Lock,
) -> Tuple[int, str]:
    """
    Выполняет один HTTP GET с МРД-меткой и Kerberos Negotiate.

    Возвращает кортеж (status, response_text):
      status        — HTTP-статус или ERR_* (отрицательный)
      response_text — полный HTTP-ответ как строка; "" при сетевой ошибке
    """
    token:    Optional[str]    = None
    sockfd:   Optional[socket.socket] = None
    orig_ptr: int = 0
    new_ptr:  int = 0

    with label_mutex:
        token = get_negotiate_token(hostname, verbose=False)

        try:
            orig_ptr = pdp.get_pid()
        except Exception:
            return ERR_LABEL, ""

        try:
            cur_ilev = pdp.ilev(orig_ptr)
            new_ptr  = pdp.new_mac(level, cur_ilev)
        except Exception:
            pdp.put(orig_ptr)
            return ERR_LABEL, ""

        try:
            pdp.set_pid(new_ptr)
        except PermissionError:
            pdp.put(new_ptr)
            pdp.put(orig_ptr)
            return ERR_LABEL, ""

        try:
            sockfd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            sockfd = None

        try:
            pdp.set_pid(orig_ptr)
        except Exception:
            pass

        pdp.put(new_ptr);  new_ptr  = 0
        pdp.put(orig_ptr); orig_ptr = 0

    if sockfd is None:
        return ERR_LABEL, ""

    if token is None:
        sockfd.close()
        return ERR_AUTH, ""

    try:
        sockfd.settimeout(15)
        sockfd.connect((ip, port))
    except OSError:
        sockfd.close()
        return ERR_CONNECT, ""

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
        return ERR_IO, ""

    try:
        chunks = []
        while True:
            chunk = sockfd.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError:
        sockfd.close()
        return ERR_IO, ""
    finally:
        sockfd.close()

    if not chunks:
        return ERR_IO, ""

    response = b"".join(chunks).decode("latin-1", errors="replace")

    parts = response.split(" ", 2)
    try:
        status = int(parts[1])
    except (IndexError, ValueError):
        return ERR_IO, response

    return status, response


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
# Вывод HTTP-ответа (заголовки + тело)
# ─────────────────────────────────────────────────────────────────────────────

_BODY_PREVIEW = 512   # сколько байт тела показывать
_PRINT_LOCK   = threading.Lock()


def _print_response(seq: int, status: int, response: str) -> None:
    """Выводит статус, заголовки и первые _BODY_PREVIEW байт тела."""
    status_str = {
        200: "200 OK",
        401: "401 Unauthorized",
        403: "403 Forbidden",
    }.get(status, f"HTTP {status}" if status > 0 else f"ERR {status}")

    # Разделяем заголовки и тело по \r\n\r\n
    if "\r\n\r\n" in response:
        headers_part, body_part = response.split("\r\n\r\n", 1)
    elif "\n\n" in response:
        headers_part, body_part = response.split("\n\n", 1)
    else:
        headers_part = response
        body_part    = ""

    body_preview = body_part[:_BODY_PREVIEW]

    with _PRINT_LOCK:
        print(f"\n[req#{seq:03d}] ── Статус: {status_str} {'─' * 30}")
        print(headers_part.strip())
        if body_preview.strip():
            print(f"── Тело (первые {_BODY_PREVIEW} байт) {'─' * 20}")
            print(body_preview.rstrip())
        print("─" * 54)


# ─────────────────────────────────────────────────────────────────────────────
# Функция потока
# ─────────────────────────────────────────────────────────────────────────────

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

        t0               = time.monotonic()
        status, response = do_one_request(ip, port, hostname, url, level, pdp, label_mutex)
        usec             = int((time.monotonic() - t0) * 1_000_000)

        seq = stats.record(status, usec)
        _print_response(seq, status, response)

        work_queue.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Нагрузчик HTTP с МРД-меткой (PARSEC) и Kerberos Negotiate (тест-версия с выводом ответа)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Пример:\n"
            "  kinit user@BALANCE.RBT\n"
            "  sudo execaps -c 0x804 -- python3 mrd_test_load_generator.py \\\n"
            "      -H 10.0.2.20 -n web1.balance.rbt -l 2 -u /lev1.html -w 1 -r 5\n\n"
            "Примечание: IP задаётся через -H (не -h: та занята --help)."
        ),
    )
    ap.add_argument("-H", "--host",     required=True,          metavar="IP")
    ap.add_argument("-n", "--name",     default="",             metavar="HOSTNAME")
    ap.add_argument("-p", "--port",     default=80,  type=int,  metavar="PORT")
    ap.add_argument("-u", "--url",      default="/",            metavar="PATH")
    ap.add_argument("-l", "--level",    default=0,   type=int,  metavar="0..3")
    ap.add_argument("-w", "--workers",  default=4,   type=int,  metavar="N")
    ap.add_argument("-r", "--requests", default=100, type=int,  metavar="N")
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
    print("  mrd_test_load_generator.py: МРД тест с выводом ответа")
    print("══════════════════════════════════════════════")
    print(f"[conf] IP:          {ip}:{args.port}")
    print(f"[conf] Hostname:    {hostname}")
    print(f"[conf] URL:         {args.url}")
    print(f"[conf] МРД уровень: {args.level}")
    print(f"[conf] Потоков:     {args.workers}")
    print(f"[conf] Запросов:    {args.requests}")
    print("──────────────────────────────────────────────")

    try:
        pdp = PDP()
    except RuntimeError as exc:
        sys.exit(f"[pdp]  {exc}")

    try:
        self_ptr = pdp.get_pid()
        print(f"[pdp]  Метка процесса (начальная): \"{pdp.label_text(self_ptr)}\"")
        cur_ilev = pdp.ilev(self_ptr)
    except Exception as exc:
        pdp.release()
        sys.exit(f"[pdp]  {exc}")

    label_mutex = threading.Lock()
    new_ptr: int = 0
    try:
        new_ptr = pdp.new_mac(args.level, cur_ilev)
        pdp.set_pid(new_ptr)
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        pdp.set_pid(self_ptr)
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

    for t in threads:
        t.join()

    wall_sec = time.monotonic() - t_wall_start

    err_total = stats.err_label + stats.err_conn + stats.err_io + stats.err_auth
    rps       = stats.sent / wall_sec if wall_sec > 0 else 0.0
    avg_ms    = (stats.total_usec / stats.sent / 1000) if stats.sent > 0 else 0.0

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
