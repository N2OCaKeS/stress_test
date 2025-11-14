#!/usr/bin/env python3
import argparse
import socket
import time
import sys
import csv


def get_client_count(host: str, port: int, timeout: float = 5.0) -> int:
    """
    Подключаемся к management-интерфейсу, отправляем 'status 2',
    считаем строки CLIENT_LIST.
    """
    client_count = 0

    # создаём TCP-соединение
    with socket.create_connection((host, port), timeout=timeout) as sock:
        # файлоподобный объект для удобного чтения строк
        rfile = sock.makefile("r", encoding="utf-8", errors="ignore", newline="\n")

        # шлём команду status 2
        sock.sendall(b"status 2\n")

        # читаем до строки END
        for line in rfile:
            line = line.strip()
            if not line:
                continue
            if line == "END":
                break
            # строки с клиентами имеют вид: CLIENT_LIST,CommonName,RealAddr,...
            if line.startswith("CLIENT_LIST,"):
                client_count += 1

    return client_count


def main():
    parser = argparse.ArgumentParser(
        description="Ежесекундная запись количества клиентов OpenVPN в CSV"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="адрес management-интерфейса (по умолчанию 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7505,
        help="порт management-интерфейса (по умолчанию 7505)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="интервал опроса в секундах (по умолчанию 1.0)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="длительность в секундах (0 — бесконечно)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="clients_stats.csv",
        help="имя CSV-файла (по умолчанию clients_stats.csv)",
    )
    args = parser.parse_args()

    # открываем CSV
    # newline='' важно, чтобы csv не добавлял лишние пустые строки
    with open(args.output, "w", newline="") as f:
        writer = csv.writer(f)
        # заголовок: кол-во секунд, кол-во клиентов
        writer.writerow(["seconds", "clients"])

        start_time = time.time()
        sample_index = 0

        try:
            while True:
                now = time.time()
                elapsed = int(now - start_time)

                # если задана duration и время вышло — выходим
                if args.duration > 0 and elapsed >= args.duration:
                    break

                try:
                    clients = get_client_count(args.host, args.port)
                except Exception as e:
                    # при ошибке пишем -1 как маркер ошибки
                    clients = -1
                    print(f"Ошибка при опросе management: {e}", file=sys.stderr)

                writer.writerow([elapsed, clients])
                f.flush()  # сразу пишем на диск

                sample_index += 1

                # аккуратно выдерживаем интервал
                next_time = start_time + sample_index * args.interval
                sleep_time = next_time - time.time()
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            print("\nОстановлено пользователем", file=sys.stderr)


if __name__ == "__main__":
    main()
