#!/usr/bin/env python3
"""
Упрощённая версия mrd_load_generator.py: без подмены метки процесса
(libpdp/pdp_set_pid). Работает всегда на уровне 0 — том, что и так
является меткой процесса по умолчанию, поэтому execaps/capabilities
не требуются.

Флаги:
  -H/--host     IP-адрес сервера (обязательный; -h занята --help)
  -n/--name     DNS-имя (для Host: и SPN Kerberos)
  -p/--port     TCP-порт (по умолчанию 80)
  -u/--url      Путь запроса (по умолчанию /)
  -w/--workers  Число потоков (по умолчанию 4)
  -r/--requests Всего запросов (по умолчанию 100)

Пример:
  kinit user@BALANCE.RBT
  python3 orel_load_generator.py \\
      -H 10.0.2.20 -n web1.balance.rbt -u /lev1.html -w 10 \\
      --r-start 200 --r-end 1000 --r-step 200 --output-dir results/
"""

import argparse
import base64
import json
import os
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Конфигурация итераций
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_R_START = 100
DEFAULT_R_END   = 500
DEFAULT_R_STEP  = 100
MAX_ITERATIONS  = 5

# МРД-уровень фиксирован: подмены метки процесса больше нет, запросы всегда
# выполняются на уровне 0 (метка процесса по умолчанию).
MRD_LEVEL = 0

# ─────────────────────────────────────────────────────────────────────────────
# gssapi
# ─────────────────────────────────────────────────────────────────────────────

try:
    import gssapi
    _HAS_GSSAPI = True
except ImportError:
    _HAS_GSSAPI = False


def get_negotiate_token(hostname: str, ccache: Optional[str] = None, verbose: bool = False) -> Optional[str]:
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
        creds = None
        if ccache:
            # Явно грузим тикет из конкретного файла-кэша, а не из общего
            # (default ccache процесса) — так разные воркеры могут работать под
            # разными Kerberos-принципалами вместо одного общего user_level3.
            creds = gssapi.Credentials(usage="initiate", store={"ccache": f"FILE:{ccache}"})
        ctx         = gssapi.SecurityContext(name=name, creds=creds, flags=flags, usage="initiate")
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

ERR_CONNECT = -2
ERR_IO      = -3
ERR_AUTH    = -4


# ─────────────────────────────────────────────────────────────────────────────
# do_one_request
# ─────────────────────────────────────────────────────────────────────────────

def do_one_request(
    ip: str, port: int, hostname: str,
    url: str,
    ccache: Optional[str] = None,
) -> int:
    token = get_negotiate_token(hostname, ccache=ccache, verbose=False)

    try:
        sockfd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return ERR_CONNECT

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
        f"X-MRD-Level: {MRD_LEVEL}\r\n"
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
            elif status == ERR_CONNECT: self.err_conn  += 1
            elif status == ERR_IO:      self.err_io    += 1
            elif status == ERR_AUTH:    self.err_auth  += 1
            else:                       self.other     += 1
            return self.sent


# ─────────────────────────────────────────────────────────────────────────────
# Воркер
# ─────────────────────────────────────────────────────────────────────────────

def worker_thread(
    ip: str, port: int, hostname: str, url: str,
    work_queue: "queue.Queue[int]",
    stats: Stats,
    ccache: Optional[str] = None,
) -> None:
    while True:
        try:
            work_queue.get(block=False)
        except queue.Empty:
            return

        t0     = time.monotonic()
        status = do_one_request(ip, port, hostname, url, ccache=ccache)
        usec   = int((time.monotonic() - t0) * 1_000_000)
        stats.record(status, usec)

        work_queue.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# Одна итерация нагрузки → возвращает dict со статистикой
# ─────────────────────────────────────────────────────────────────────────────

def run_iteration(
    ip: str, port: int, hostname: str, url: str,
    workers: int, requests: int,
    iteration_num: int,
    ccaches: Optional[List[str]] = None,
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
    for worker_idx in range(workers):
        # Каждому воркеру — свой ccache по кругу, если задан
        # список: разные потоки аутентифицируются разными Kerberos-пользователями
        # вместо одного общего.
        ccache = ccaches[worker_idx % len(ccaches)] if ccaches else None
        t = threading.Thread(
            target=worker_thread,
            args=(ip, port, hostname, url, wq, stats),
            kwargs={"ccache": ccache},
            daemon=True,
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    wall_sec = time.monotonic() - t_start
    err_total = stats.err_conn + stats.err_io + stats.err_auth
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
        "err_connect":    stats.err_conn,
        "err_io":         stats.err_io,
        "err_auth":       stats.err_auth,
    }


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="нагрузчик HTTP (Kerberos), только уровень МРД 0 — без подмены метки процесса",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Пример:\n"
            "  kinit user@BALANCE.RBT\n"
            "  python3 orel_load_generator.py \\\n"
            "      -H 10.0.2.20 -n web1.balance.rbt -u /lev1.html -w 10 \\\n"
            "      --r-start 200 --r-end 1000 --r-step 200 --output-dir results/"
        ),
    )
    ap.add_argument("-H", "--host",     required=True,         metavar="IP")
    ap.add_argument("-n", "--name",     default="",            metavar="HOSTNAME")
    ap.add_argument("-p", "--port",     default=80,  type=int, metavar="PORT")
    ap.add_argument("-u", "--url",      default="/",           metavar="PATH")
    ap.add_argument("-w", "--workers",  default=4,   type=int, metavar="N")

    ap.add_argument("--r-start", default=DEFAULT_R_START, type=int, metavar="N",
                    help=f"Начальное число запросов (по умолчанию {DEFAULT_R_START})")
    ap.add_argument("--r-end",   default=DEFAULT_R_END,   type=int, metavar="N",
                    help=f"Конечное число запросов (по умолчанию {DEFAULT_R_END})")
    ap.add_argument("--r-step",  default=DEFAULT_R_STEP,  type=int, metavar="N",
                    help=f"Шаг (по умолчанию {DEFAULT_R_STEP})")
    ap.add_argument("--output-dir", default=".", metavar="DIR",
                    help="Папка для JSON-файлов результатов (по умолчанию .)")
    ap.add_argument("--ccache-list", default="", metavar="PATH1,PATH2,...",
                    help="Через запятую пути к файлам Kerberos ccache — воркеры "
                         "разбираются по кругу, каждый под своим принципалом "
                         "(по умолчанию все используют ccache процесса)")
    args = ap.parse_args()

    ip       = args.host
    hostname = args.name or args.host
    ccaches: Optional[List[str]] = (
        [c.strip() for c in args.ccache_list.split(",") if c.strip()]
        if args.ccache_list else None
    )
    if not args.name:
        print(
            "[warn] -n/--name не задан, используем IP как hostname.\n"
            "       Kerberos работает только с DNS-именем: -n web1.balance.rbt",
            file=sys.stderr,
        )

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
    print("  orel_load_generator.py: нагрузчик (МРД уровень 0, без подмены метки)")
    print("══════════════════════════════════════════════")
    print(f"[conf] IP:          {ip}:{args.port}")
    print(f"[conf] Hostname:    {hostname}")
    print(f"[conf] URL:         {args.url}")
    print(f"[conf] МРД уровень: {MRD_LEVEL} (фиксирован)")
    print(f"[conf] Потоков:     {args.workers}")
    print(f"[conf] Итерации -r: {r_values}  (шаг {args.r_step})")
    print(f"[conf] Вывод JSON:  {args.output_dir}/")
    print("──────────────────────────────────────────────")

    # Kerberos
    if not _HAS_GSSAPI:
        print("[warn] Модуль gssapi не найден. pip install gssapi", file=sys.stderr)
    else:
        probe_ccache = ccaches[0] if ccaches else None
        probe = get_negotiate_token(hostname, ccache=probe_ccache, verbose=True)
        if probe:
            print("[gss]  Kerberos OK.")
        else:
            print("[warn] Kerberos-токен не получен (ожидайте 401)", file=sys.stderr)

    if ccaches:
        print(f"[gss]  Ccache-файлов: {len(ccaches)} (round-robin по воркерам)")

    # ── Итеративный запуск ───────────────────────────────────────────────────
    results_by_iter: dict = {}
    t_total_start = time.monotonic()

    for idx, r_val in enumerate(r_values, start=1):
        result = run_iteration(
            ip=ip, port=args.port, hostname=hostname,
            url=args.url,
            workers=args.workers, requests=r_val,
            iteration_num=idx,
            ccaches=ccaches,
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


if __name__ == "__main__":
    main()
