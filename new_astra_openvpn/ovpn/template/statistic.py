#!/usr/bin/env python3
import argparse
import socket
import time
import sys
import csv
from typing import Set


def get_client_names(host: str, port: int, timeout: float = 5.0) -> Set[str]:
    """
    Подключается к management-интерфейсу OpenVPN, отправляет 'status 2',
    возвращает множество имён клиентов (Common Name).
    """
    names: Set[str] = set()

    with socket.create_connection((host, port), timeout=timeout) as sock:
        rfile = sock.makefile("r", encoding="utf-8", errors="ignore", newline="\n")
        sock.sendall(b"status 2\n")

        for line in rfile:
            line = line.strip()
            if not line:
                continue
            if line == "END":
                break
            if line.startswith("CLIENT_LIST,"):
                parts = line.split(",")
                if len(parts) >= 2:
                    names.add(parts[1])

    return names


def main():
    parser = argparse.ArgumentParser(
        description="Сбор снапшотов OpenVPN management в CSV"
    )
    parser.add_argument("--host", default="127.0.0.1", help="адрес management (по умолчанию 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7505, help="порт management (по умолчанию 7505)")
    parser.add_argument("--interval", type=float, default=1.0, help="интервал опроса в секундах (по умолчанию 1)")
    parser.add_argument("--duration", type=int, default=0, help="длительность в секундах (0 — бесконечно)")
    parser.add_argument(
        "--output",
        "-o",
        default="clients_snapshots.csv",
        help="выходной CSV-файл (по умолчанию clients_snapshots.csv)",
    )
    args = parser.parse_args()

    start_time = time.time()
    sample_index = 0

    with open(args.output, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["seconds", "active_clients", "client_names"])

        try:
            while True:
                now = time.time()
                elapsed = int(now - start_time)

                if args.duration > 0 and elapsed >= args.duration:
                    break

                try:
                    names = get_client_names(args.host, args.port)
                    active_clients = len(names)
                    # сохраняем имена в виде tester0|tester1|...
                    client_names_str = "|".join(sorted(names))
                except Exception as e:
                    print(f"Ошибка при опросе management: {e}", file=sys.stderr)
                    active_clients = -1
                    client_names_str = ""

                writer.writerow([elapsed, active_clients, client_names_str])
                f.flush()

                sample_index += 1
                next_time = start_time + sample_index * args.interval
                sleep_time = next_time - time.time()
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            print("\nОстановлено пользователем", file=sys.stderr)


if __name__ == "__main__":
    main()
