from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------
# Общие константы
# --------------------------------------------------------------------------

DEFAULT_JOURNAL = "/parsec/log/astra/events"
DEFAULT_SERVICE = "astraeventsd"
DEFAULT_CONFIG = "/etc/astraeventsd/config.yaml"


# --------------------------------------------------------------------------
# Вспомогательное: статистика
# --------------------------------------------------------------------------

def percentile(data, p):
    if not data:
        return None
    data = sorted(data)
    if len(data) == 1:
        return data[0]
    k = (len(data) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return data[int(k)]
    return data[f] * (c - k) + data[c] * (k - f)


def summarize(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None,
                "max": None, "p95": None, "p99": None}
    return {
        "count": len(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
    }


# --------------------------------------------------------------------------
# Резолвинг журнала/демона
# --------------------------------------------------------------------------

def detect_journal_path() -> Optional[str]:
    """Пытается вычитать output_file из /etc/astraeventsd/config.yaml."""
    try:
        text = Path(DEFAULT_CONFIG).read_text()
    except OSError:
        return None
    m = re.search(r"output_file:\s*(\S+)", text)
    return m.group(1) if m else None


def resolve_daemon_pid(service: str) -> Optional[int]:
    try:
        out = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", service],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        pid = int(out)
        return pid if pid > 0 else None
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return None


# --------------------------------------------------------------------------
# CPU/RSS сэмплер демона (чистый /proc, без psutil)
# --------------------------------------------------------------------------

class ProcSampler:
    """Сэмплирует CPU% (относительно одного ядра, как top) и RSS процесса pid
    через /proc/<pid>/stat и /proc/<pid>/status на отдельном потоке."""

    def __init__(self, pid: int, interval: float = 0.2):
        self.pid = pid
        self.interval = interval
        self._clk_tck = os.sysconf("SC_CLK_TCK")
        self._samples = []  # (t_rel, cpu_percent, rss_kb, threads)
        self._stop = False
        self._thread = None
        self._alive = True

    def _read_cpu_ticks(self):
        raw = Path(f"/proc/{self.pid}/stat").read_text()
        # comm может содержать пробелы/скобки — берём хвост после последней ')'
        tail = raw.rsplit(")", 1)[1].split()
        utime = int(tail[11])   # поле 14 целиком (индекс 13), минус 2 уже съеденных (pid, comm)
        stime = int(tail[12])   # поле 15
        return utime + stime

    def _read_status(self):
        rss_kb = None
        threads = None
        for line in Path(f"/proc/{self.pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss_kb = int(line.split()[1])
            elif line.startswith("Threads:"):
                threads = int(line.split()[1])
        return rss_kb, threads

    def _run(self):
        t0 = time.monotonic()
        try:
            prev_ticks = self._read_cpu_ticks()
        except OSError:
            self._alive = False
            return
        prev_t = time.monotonic()
        while not self._stop:
            time.sleep(self.interval)
            now = time.monotonic()
            try:
                ticks = self._read_cpu_ticks()
                rss_kb, threads = self._read_status()
            except OSError:
                self._alive = False
                break
            dt = now - prev_t
            dticks = ticks - prev_ticks
            cpu_pct = (dticks / self._clk_tck) / dt * 100.0 if dt > 0 else 0.0
            self._samples.append((now - t0, cpu_pct, rss_kb, threads))
            prev_ticks, prev_t = ticks, now

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=self.interval * 3 + 1)

    def summary(self):
        cpu = [s[1] for s in self._samples]
        rss = [s[2] for s in self._samples if s[2] is not None]
        threads = [s[3] for s in self._samples if s[3] is not None]
        return {
            "alive": self._alive,
            "samples": len(self._samples),
            "cpu_avg_percent": statistics.mean(cpu) if cpu else None,
            "cpu_max_percent": max(cpu) if cpu else None,
            "rss_avg_kb": statistics.mean(rss) if rss else None,
            "rss_max_kb": max(rss) if rss else None,
            "threads_max": max(threads) if threads else None,
        }


# --------------------------------------------------------------------------
# Генератор нагрузки (обвязка над event-generator)
# --------------------------------------------------------------------------

def write_generator_config(path: Path, transport: str, events: list[str], delay_ms: int):
    lines = [f"transport: {transport}", f"delay: {delay_ms}", "", "events:"]
    lines += [f"- {e}" for e in events]
    path.write_text("\n".join(lines) + "\n")


class GeneratorError(RuntimeError):
    pass


def run_generator(gen_bin: str, transport: str, config_path: Path, target_count: int,
                   max_line_multiplier: int = 5, startup_timeout: float = 15.0):
    """Запускает event-generator -g, читает stdout построчно, засекает
    monotonic-время каждой подтверждённой регистрации, останавливает процесс,
    как только набрано target_count подтверждений.

    Возвращает (confirmed_times, failures_count, mono_start, mono_end).
    """
    proc = subprocess.Popen(
        [gen_bin, "-t", transport, "-c", str(config_path), "-g"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    confirmed = []
    failures = 0
    max_lines = target_count * max_line_multiplier
    mono_start = time.monotonic()
    seen_first_line_deadline = mono_start + startup_timeout

    try:
        for line in proc.stdout:
            now = time.monotonic()
            if "successfully registered" in line:
                confirmed.append(now)
                if len(confirmed) >= target_count:
                    break
            elif "failed to register" in line:
                failures += 1
            total_lines = len(confirmed) + failures
            if total_lines == 0 and now > seen_first_line_deadline:
                raise GeneratorError(
                    f"event-generator не выдал ни одной строки за {startup_timeout}s"
                )
            if total_lines >= max_lines:
                raise GeneratorError(
                    f"за {max_lines} строк вывода набрано только "
                    f"{len(confirmed)}/{target_count} подтверждений "
                    f"({failures} ошибок) — похоже, событие не регистрируется"
                )
    finally:
        mono_end = time.monotonic()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    if not confirmed:
        raise GeneratorError("не получено ни одного подтверждения регистрации события")

    return confirmed, failures, mono_start, mono_end


# --------------------------------------------------------------------------
# Учёт журнала: смещения, подсчёт совпадений, ожидание стабилизации
# --------------------------------------------------------------------------

def _count_matches(journal_path: Path, start_offset: int, event_ids: set[str],
                    program_hint: str):
    """Считает строки в журнале начиная с start_offset, у которых PROGRAM
    содержит program_hint (basename бинаря генератора) и message_id входит
    в event_ids. Возвращает (matched, total_new_lines, new_size)."""
    matched = 0
    total = 0
    with journal_path.open("rb") as f:
        f.seek(start_offset)
        data = f.read()
    new_size = start_offset + len(data)
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        total += 1
        if program_hint.encode() not in raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg = obj.get("MSG", {})
        # структура MSG: {"astra-custom": {...}} или {"astra-audit": {...}} и т.п.
        for inner in msg.values():
            if isinstance(inner, dict) and inner.get("message_id") in event_ids:
                matched += 1
                break
    return matched, total, new_size


def poll_until_stable(journal_path: Path, start_offset: int, event_ids: set[str],
                       program_hint: str, target_count: int, mono_ref: float,
                       quiet_seconds: float = 1.0, timeout_seconds: float = 60.0,
                       poll_interval: float = 0.2):
    last_matched = -1
    last_change_mono = time.monotonic()
    matched = 0
    total_new = 0
    timed_out = False

    while True:
        matched, total_new, _ = _count_matches(journal_path, start_offset, event_ids, program_hint)
        now = time.monotonic()
        if matched != last_matched:
            last_matched = matched
            last_change_mono = now
        elapsed = now - mono_ref
        quiet_for = now - last_change_mono
        if elapsed >= timeout_seconds:
            timed_out = True
            break
        if quiet_for >= quiet_seconds and matched >= target_count:
            break
        if quiet_for >= quiet_seconds and matched > 0 and elapsed > 2:
            # журнал перестал расти, но подтверждённых меньше, чем нужно —
            # не имеет смысла ждать дальше таймаута, считаем это потерями
            break
        time.sleep(poll_interval)

    stabilization_duration = last_change_mono - mono_ref
    return {
        "journal_matched": matched,
        "journal_total_new_lines": total_new,
        "stabilization_duration_s": stabilization_duration,
        "timed_out": timed_out,
    }


# --------------------------------------------------------------------------
# Один прогон на заданный объём
# --------------------------------------------------------------------------

@dataclass
class VolumeResult:
    label: str
    volume: int
    repeat_index: int
    transport: str
    events: list
    delay_ms: int
    confirmed_sent: int
    failures: int
    journal_matched: int
    lost: int
    extra: int
    call_duration_s: float
    throughput_eps: float
    inter_call_ms: dict
    stabilization_duration_s: float
    journal_stall_timed_out: bool
    cpu: dict
    started_at: float


def run_volume(gen_bin: str, transport: str, events: list[str], volume: int,
                delay_ms: int, journal_path: Path, daemon_pid: Optional[int],
                tmp_dir: Path, label: str, repeat_index: int,
                stabilize_quiet: float, stabilize_timeout: float,
                poll_interval: float, sample_interval: float,
                program_hint: str) -> VolumeResult:
    cfg_path = tmp_dir / f"gen_{volume}_{repeat_index}_{int(time.time() * 1000)}.yaml"
    write_generator_config(cfg_path, transport, events, delay_ms)

    pre_offset = journal_path.stat().st_size if journal_path.exists() else 0

    sampler = ProcSampler(daemon_pid, sample_interval) if daemon_pid else None
    if sampler:
        sampler.start()

    started_at = time.time()
    try:
        confirmed_times, failures, mono_start, mono_end = run_generator(
            gen_bin, transport, cfg_path, volume
        )
    finally:
        try:
            cfg_path.unlink()
        except FileNotFoundError:
            pass

    poll_result = poll_until_stable(
        journal_path, pre_offset, set(events), program_hint,
        target_count=len(confirmed_times), mono_ref=mono_start,
        quiet_seconds=stabilize_quiet, timeout_seconds=stabilize_timeout,
        poll_interval=poll_interval,
    )

    if sampler:
        sampler.stop()
        cpu_stats = sampler.summary()
    else:
        cpu_stats = {"alive": None, "samples": 0}

    confirmed_sent = len(confirmed_times)
    call_duration = mono_end - mono_start
    throughput = confirmed_sent / call_duration if call_duration > 0 else float("inf")
    intervals_ms = [(t2 - t1) * 1000.0 for t1, t2 in zip(confirmed_times, confirmed_times[1:])]

    journal_matched = poll_result["journal_matched"]
    lost = max(0, confirmed_sent - journal_matched)
    extra = max(0, journal_matched - confirmed_sent)

    return VolumeResult(
        label=label, volume=volume, repeat_index=repeat_index, transport=transport,
        events=events, delay_ms=delay_ms, confirmed_sent=confirmed_sent, failures=failures,
        journal_matched=journal_matched, lost=lost, extra=extra,
        call_duration_s=call_duration, throughput_eps=throughput,
        inter_call_ms=summarize(intervals_ms),
        stabilization_duration_s=poll_result["stabilization_duration_s"],
        journal_stall_timed_out=poll_result["timed_out"],
        cpu=cpu_stats, started_at=started_at,
    )


# --------------------------------------------------------------------------
# Оркестрация серии прогонов
# --------------------------------------------------------------------------

def run_suite(args) -> list[VolumeResult]:
    gen_bin = shutil.which(args.generator_bin) or args.generator_bin
    if not Path(gen_bin).is_file():
        raise SystemExit(
            f"Не найден бинарь генератора событий: {args.generator_bin!r}. "
            f"Соберите samples/event_generator из исходников libastraevents "
            f"(см. make.txt) или передайте путь через --generator-bin."
        )

    journal_path = Path(args.journal or detect_journal_path() or DEFAULT_JOURNAL)
    if not journal_path.exists():
        raise SystemExit(f"Файл журнала не найден: {journal_path}")

    daemon_pid = args.daemon_pid or resolve_daemon_pid(args.service)
    if daemon_pid is None:
        print(f"[warn] не удалось определить PID {args.service} — CPU/RSS сэмплирование отключено",
              file=sys.stderr)

    program_hint = Path(gen_bin).name

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[VolumeResult] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="aeb_load_"))
    try:
        for volume in args.volumes:
            for rep in range(args.repeats):
                print(f"[run] label={args.label} volume={volume} repeat={rep + 1}/{args.repeats} "
                      f"delay_ms={args.delay_ms} events={args.events}", file=sys.stderr)
                res = run_volume(
                    gen_bin=gen_bin, transport=args.transport, events=args.events,
                    volume=volume, delay_ms=args.delay_ms, journal_path=journal_path,
                    daemon_pid=daemon_pid, tmp_dir=tmp_dir, label=args.label,
                    repeat_index=rep, stabilize_quiet=args.stabilize_quiet,
                    stabilize_timeout=args.stabilize_timeout,
                    poll_interval=args.poll_interval, sample_interval=args.sample_interval,
                    program_hint=program_hint,
                )
                results.append(res)
                print(
                    f"    confirmed={res.confirmed_sent} journal={res.journal_matched} "
                    f"lost={res.lost} extra={res.extra} "
                    f"throughput={res.throughput_eps:.0f} eps "
                    f"stabilize={res.stabilization_duration_s:.3f}s "
                    f"cpu_avg={res.cpu.get('cpu_avg_percent')}",
                    file=sys.stderr,
                )
                if args.cooldown > 0:
                    time.sleep(args.cooldown)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    out_path = out_dir / f"{args.label}_{int(time.time())}.json"
    payload = {
        "label": args.label,
        "generated_at": time.time(),
        "journal": str(journal_path),
        "service": args.service,
        "transport": args.transport,
        "runs": [asdict(r) for r in results],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[done] сырые результаты сохранены в {out_path}", file=sys.stderr)

    return results


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--generator-bin", default="./event-generator",
                    help="путь к бинарю event-generator / astra-event-register")
    p.add_argument("--transport", default="astraeventsd", choices=["astraeventsd", "syslog-ng"])
    p.add_argument("--events", default="server_started",
                    help="список ID событий через запятую (должны быть из списка event-generator -l)")
    p.add_argument("--volumes", default="1000,10000,50000,100000,150000",
                    help="список объёмов через запятую")
    p.add_argument("--repeats", type=int, default=1, help="число повторов на каждый объём")
    p.add_argument("--delay-ms", type=int, default=None,
                    help="пауза между вызовами в мс (по умолчанию 0 — максимальная скорость)")
    p.add_argument("--rate", type=float, default=None,
                    help="альтернатива --delay-ms: целевая частота событий/сек")
    p.add_argument("--journal", default=None, help="путь к файлу журнала astraeventsd")
    p.add_argument("--service", default=DEFAULT_SERVICE, help="имя systemd-юнита демона")
    p.add_argument("--daemon-pid", type=int, default=None, help="PID демона (если не через systemd)")
    p.add_argument("--label", default="baseline", help="метка сценария (baseline/mrd/...)")
    p.add_argument("--out-dir", default="results", help="куда сохранять сырые json-результаты")
    p.add_argument("--stabilize-quiet", type=float, default=1.0,
                    help="сколько секунд журнал должен не расти, чтобы считать его стабилизировавшимся")
    p.add_argument("--stabilize-timeout", type=float, default=60.0,
                    help="максимальное время ожидания стабилизации журнала, с")
    p.add_argument("--poll-interval", type=float, default=0.2, help="интервал опроса журнала, с")
    p.add_argument("--sample-interval", type=float, default=0.2, help="интервал сэмплирования CPU/RSS, с")
    p.add_argument("--cooldown", type=float, default=1.0, help="пауза между прогонами, с")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.events = [e.strip() for e in args.events.split(",") if e.strip()]
    args.volumes = [int(v.strip()) for v in args.volumes.split(",") if v.strip()]
    if args.rate and args.delay_ms:
        raise SystemExit("--rate и --delay-ms взаимоисключающие")
    if args.rate:
        args.delay_ms = max(0, round(1000.0 / args.rate))
    elif args.delay_ms is None:
        args.delay_ms = 0
    run_suite(args)


if __name__ == "__main__":
    main()
