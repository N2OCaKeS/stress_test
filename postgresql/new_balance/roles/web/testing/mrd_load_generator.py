#!/usr/bin/env python3
"""
Флаги:
  -H/--host     IP-адрес сервера (обязательный; -h занята --help)
  -n/--name     DNS-имя (для Host: и SPN Kerberos)
  -p/--port     TCP-порт (по умолчанию 80)
  -u/--url      Путь запроса (по умолчанию /)
  -l/--level    МРД-уровень 0..3 (по умолчанию 0)
  -w/--workers  Число потоков (по умолчанию 4)
  -r/--requests Всего запросов (по умолчанию 100)

Пример:
  kinit user@BALANCE.RBT
  sudo execaps -c 0x804 -- python3 mrd_load_generator_iter.py \\
      -H 10.0.2.20 -n web1.balance.rbt -l 2 -u /lev1.html -w 10 \\
      --r-start 200 --r-end 1000 --r-step 200 --output-dir results/
"""

import argparse
import base64
import ctypes
import json
import os
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Конфигурация итераций
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_R_START = 100
DEFAULT_R_END   = 500
DEFAULT_R_STEP  = 100
MAX_ITERATIONS  = 5

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
# GSSAPI
# ─────────────────────────────────────────────────────────────────────────────

def get_negotiate_token(hostname: str, verbose: bool = False) -> Optional[str]:
    if not _HAS_GSSAPI:
        return None
    spn = f"HTTP@{hostname}"
    if verbose:
        print(f"[gss]  SPN: {spn}")
    try:
        name  = gssapi.Name(spn, gssapi.NameType.hostbased_service)
        flags = [gssapi.RequirementFlag.mutual_authentication]
        for _seq_name in ("out_of_sequence_detection", "sequence_detection", "sequence"):
            try:
                flags.append(getattr(gssapi.RequirementFlag, _seq_name))
                break
            except AttributeError:
                pass
        ctx         = gssapi.SecurityContext(name=name, flags=flags, usage="initiate")
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
# do_one_request
# ─────────────────────────────────────────────────────────────────────────────

def do_one_request(
    ip: str, port: int, hostname: str,
    url: str, level: int,
    pdp: PDP,
    label_mutex: threading.Lock,
) -> int:
    token = get_negotiate_token(hostname, verbose=False)

    sockfd:   Optional[socket.socket] = None
    orig_ptr: int = 0
    new_ptr:  int = 0

    with label_mutex:
        try:
            orig_ptr = pdp.get_pid()
        except Exception:
            return ERR_LABEL

        try:
            cur_ilev = pdp.ilev(orig_ptr)
            new_ptr  = pdp.new_mac(level, cur_ilev)
        except Exception:
            pdp.put(orig_ptr)
            return ERR_LABEL

        try:
            pdp.set_pid(new_ptr)
        except PermissionError:
            pdp.put(new_ptr)
            pdp.put(orig_ptr)
            return ERR_LABEL

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
        return ERR_LABEL

    if token is None:
        sockfd.close()
        return ERR_AUTH

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
    parts    = response.split(" ", 2)
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return ERR_IO


# ─────────────────────────────────────────────────────────────────────────────
# Статистика
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
# Воркер
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

        t0     = time.monotonic()
        status = do_one_request(ip, port, hostname, url, level, pdp, label_mutex)
        usec   = int((time.monotonic() - t0) * 1_000_000)
        stats.record(status, usec)

        work_queue.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# Одна итерация нагрузки → возвращает dict со статистикой
# ─────────────────────────────────────────────────────────────────────────────

def run_iteration(
    ip: str, port: int, hostname: str, url: str, level: int,
    workers: int, requests: int,
    pdp: PDP, label_mutex: threading.Lock,
    iteration_num: int,
) -> dict:
    print(f"\n{'─'*50}")
    print(f"  Итерация {iteration_num}: -r {requests} запросов, -w {workers} потоков")
    print(f"{'─'*50}")

    stats: Stats = Stats()
    wq: "queue.Queue[int]" = queue.Queue()
    for i in range(requests):
        wq.put(i)

    t_start = time.monotonic()
    threads = []
    for _ in range(workers):
        t = threading.Thread(
            target=worker_thread,
            args=(ip, port, hostname, url, level,
                  pdp, label_mutex, wq, stats),
            daemon=True,
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    wall_sec = time.monotonic() - t_start
    err_total = stats.err_label + stats.err_conn + stats.err_io + stats.err_auth
    rps       = stats.sent / wall_sec if wall_sec > 0 else 0.0
    avg_ms    = (stats.total_usec / stats.sent / 1000) if stats.sent > 0 else 0.0

    print(f"  200 OK: {stats.ok}  403: {stats.forbidden}  401: {stats.unauth}"
          f"  ошибки: {err_total}")
    print(f"  Время: {wall_sec:.2f}с  RPS: {rps:.1f}  avg latency: {avg_ms:.1f}мс")

    return {
        "iteration":      iteration_num,
        "requests":       requests,
        "workers":        workers,
        "wall_sec":       round(wall_sec, 3),
        "rps":            round(rps, 2),
        "avg_latency_ms": round(avg_ms, 2),
        "sent":           stats.sent,
        "ok_200":         stats.ok,
        "forbidden_403":  stats.forbidden,
        "unauth_401":     stats.unauth,
        "other":          stats.other,
        "err_total":      err_total,
        "err_label":      stats.err_label,
        "err_connect":    stats.err_conn,
        "err_io":         stats.err_io,
        "err_auth":       stats.err_auth,
    }


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="нагрузчик HTTP (PARSEC + Kerberos)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Пример:\n"
            "  kinit user@BALANCE.RBT\n"
            "  sudo execaps -c 0x804 -- python3 mrd_load_generator_iter.py \\\n"
            "      -H 10.0.2.20 -n web1.balance.rbt -l 2 -u /lev1.html -w 10 \\\n"
            "      --r-start 200 --r-end 1000 --r-step 200 --output-dir results/"
        ),
    )
    ap.add_argument("-H", "--host",     required=True,         metavar="IP")
    ap.add_argument("-n", "--name",     default="",            metavar="HOSTNAME")
    ap.add_argument("-p", "--port",     default=80,  type=int, metavar="PORT")
    ap.add_argument("-u", "--url",      default="/",           metavar="PATH")
    ap.add_argument("-l", "--level",    default=0,   type=int, metavar="0..3")
    ap.add_argument("-w", "--workers",  default=4,   type=int, metavar="N")

    ap.add_argument("--r-start", default=DEFAULT_R_START, type=int, metavar="N",
                    help=f"Начальное число запросов (по умолчанию {DEFAULT_R_START})")
    ap.add_argument("--r-end",   default=DEFAULT_R_END,   type=int, metavar="N",
                    help=f"Конечное число запросов (по умолчанию {DEFAULT_R_END})")
    ap.add_argument("--r-step",  default=DEFAULT_R_STEP,  type=int, metavar="N",
                    help=f"Шаг (по умолчанию {DEFAULT_R_STEP})")
    ap.add_argument("--output-dir", default=".", metavar="DIR",
                    help="Папка для JSON-файлов результатов (по умолчанию .)")
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
    if args.r_start < 1 or args.r_end < args.r_start or args.r_step < 1:
        sys.exit("Ошибка: требуется r_start >= 1, r_end >= r_start, r_step >= 1")

    # Строим список значений -r (не более MAX_ITERATIONS)
    r_values = list(range(args.r_start, args.r_end + 1, args.r_step))[:MAX_ITERATIONS]
    if not r_values:
        sys.exit("Ошибка: диапазон [r_start..r_end] с шагом r_step пуст")

    os.makedirs(args.output_dir, exist_ok=True)

    print("══════════════════════════════════════════════")
    print("  mrd_load_generator_iter.py: нагрузчик")
    print("══════════════════════════════════════════════")
    print(f"[conf] IP:          {ip}:{args.port}")
    print(f"[conf] Hostname:    {hostname}")
    print(f"[conf] URL:         {args.url}")
    print(f"[conf] МРД уровень: {args.level}")
    print(f"[conf] Потоков:     {args.workers}")
    print(f"[conf] Итерации -r: {r_values}  (шаг {args.r_step})")
    print(f"[conf] Вывод JSON:  {args.output_dir}/")
    print("──────────────────────────────────────────────")

    # Инициализация libpdp
    try:
        pdp = PDP()
    except RuntimeError as exc:
        sys.exit(f"[pdp]  {exc}")

    try:
        self_ptr = pdp.get_pid()
        # print(f"[pdp]  Метка процесса: \"{pdp.label_text(self_ptr)}\"")  # всегда Уровень_0 — вводит в заблуждение, реальный уровень выставляется на сокет запроса, см. do_one_request()
        cur_ilev = pdp.ilev(self_ptr)
    except Exception as exc:
        pdp.release()
        sys.exit(f"[pdp]  {exc}")

    # Preflight
    label_mutex = threading.Lock()
    new_ptr: int = 0
    try:
        new_ptr   = pdp.new_mac(args.level, cur_ilev)
        pdp.set_pid(new_ptr)
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        pdp.set_pid(self_ptr)
        test_sock.close()
        pdp.put(new_ptr); new_ptr = 0
        print("[pdp]  pdp_set_pid() + socket() — OK")
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

    # Kerberos
    if not _HAS_GSSAPI:
        print("[warn] Модуль gssapi не найден. pip install gssapi", file=sys.stderr)
    else:
        probe = get_negotiate_token(hostname, verbose=True)
        if probe:
            print("[gss]  Kerberos OK.")
        else:
            print("[warn] Kerberos-токен не получен (ожидайте 401)", file=sys.stderr)

    # ── Итеративный запуск ───────────────────────────────────────────────────
    results_by_iter: dict = {}
    t_total_start = time.monotonic()

    for idx, r_val in enumerate(r_values, start=1):
        result = run_iteration(
            ip=ip, port=args.port, hostname=hostname,
            url=args.url, level=args.level,
            workers=args.workers, requests=r_val,
            pdp=pdp, label_mutex=label_mutex,
            iteration_num=idx,
        )
        results_by_iter[str(idx)] = result

    total_wall = time.monotonic() - t_total_start

    json_path = os.path.join(args.output_dir, "results.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(results_by_iter, fh, ensure_ascii=False, indent=2)

    print(f"\n{'═'*50}")
    print("  ИТОГО")
    print(f"{'═'*50}")
    print(f"  Итераций выполнено: {len(results_by_iter)}")
    print(f"  Общее время:        {total_wall:.2f}с")
    for key, r in results_by_iter.items():
        print(f"  iter {key:>2}  r={r['requests']:6d}"
              f"  RPS={r['rps']:7.1f}  avg={r['avg_latency_ms']:7.1f}мс"
              f"  ok={r['ok_200']}  err={r['err_total']}")
    print(f"\n  JSON: {json_path}")
    print(f"{'═'*50}")

    pdp.release()


if __name__ == "__main__":
    main()
