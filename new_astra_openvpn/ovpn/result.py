import re
from typing import Optional, Dict, Any, Set, List

import pandas as pd
import matplotlib.pyplot as plt


TESTER_RE = re.compile(r"^tester(\d+)$")


def _parse_clients_field(s: str) -> Set[str]:
    if not isinstance(s, str) or not s:
        return set()
    return {name for name in s.split("|") if name}


def analyze_result(
    csv_path: str,
    tester_start: int,
    tester_count: int,
    load_end_second: Optional[float] = None, 
    show_plots: bool = True,
    save_prefix: Optional[str] = None,
) -> Dict[str, Any]:

    df = pd.read_csv(csv_path)

    required_cols = {"seconds", "active_clients", "client_names"}
    if not required_cols.issubset(df.columns):
        raise ValueError(f"Ожидаются колонки {required_cols} в CSV")

    df = df.sort_values("seconds").reset_index(drop=True)
    seconds: List[float] = df["seconds"].tolist()

    clients_sets: List[Set[str]] = [_parse_clients_field(s) for s in df["client_names"]]

    expected_testers: Set[str] = {
        f"tester{i}" for i in range(tester_start, tester_start + tester_count)
    }

    ever_connected: Set[str] = set()
    for names in clients_sets:
        ever_connected |= (names & expected_testers)

    active_last: Set[str] = clients_sets[-1] & expected_testers

    disconnected: Set[str] = ever_connected - active_last

    drop_counts: List[int] = [0] * len(df)
    drop_time: Dict[str, float] = {}

    for idx in range(1, len(df)):
        prev_names = clients_sets[idx - 1]
        curr_names = clients_sets[idx]

        prev_testers = prev_names & expected_testers
        curr_testers = curr_names & expected_testers

        dropped_now = prev_testers - curr_testers
        drop_counts[idx] = len(dropped_now)

        for cn in dropped_now:
            if cn not in drop_time:
                drop_time[cn] = seconds[idx]

    df["drops_per_second"] = drop_counts

    first_sec = float(df["seconds"].iloc[0])
    last_sec = float(df["seconds"].iloc[-1])

    if load_end_second is not None:
        ramp_end_second = float(load_end_second)
    else:
        ramp_end_second = (tester_count / 120.0) * 60.0

    if ramp_end_second < first_sec:
        ramp_end_second = first_sec
    if ramp_end_second > last_sec:
        ramp_end_second = last_sec

    expected_clients: List[float] = []
    if ramp_end_second > first_sec:
        denom = ramp_end_second - first_sec
        for t in seconds:
            if t <= ramp_end_second:
                frac = (t - first_sec) / denom
                if frac < 0:
                    frac = 0.0
                if frac > 1:
                    frac = 1.0
                e = tester_count * frac
            else:
                e = float(tester_count)
            expected_clients.append(e)
    else:
        expected_clients = [float(tester_count)] * len(seconds)

    df["expected_clients"] = expected_clients

    # отношение активных к ожидаемым
    ratios: List[float] = []
    for active, expected in zip(df["active_clients"], expected_clients):
        if expected > 0:
            ratios.append(active / expected)
        else:
            ratios.append(0.0)
    df["active_over_expected"] = ratios

    # --------- ВАЖНО: статистику считаем ТОЛЬКО по интервалу нагрузки ---------
    mask_load = df["seconds"] <= ramp_end_second
    # на всякий случай, если что-то пойдёт не так — fallback на весь диапазон
    if not mask_load.any():
        mask_load = pd.Series([True] * len(df))

    df_load = df[mask_load]

    active_series = df_load["active_clients"]
    drops_series = df_load["drops_per_second"]
    ratios_series = df_load["active_over_expected"]

    stats: Dict[str, Any] = {
        "expected_testers_total": tester_count,
        "ever_connected_count": len(ever_connected),
        "active_at_end_count": len(active_last),
        "disconnected_count": len(disconnected),
        # "drop_time_by_tester": drop_time,
        "ramp_end_second": ramp_end_second,
        # коэффициент заполнения (по интервалу нагрузки)
        "ratio_min": float(ratios_series.min()) if not ratios_series.empty else 0.0,
        "ratio_mean": float(ratios_series.mean()) if not ratios_series.empty else 0.0,
        # что просил руководитель (тоже только по интервалу нагрузки)
        "active_median": float(active_series.median()) if not active_series.empty else 0.0,
        "active_mean": float(active_series.mean()) if not active_series.empty else 0.0,
        "active_max": int(active_series.max()) if not active_series.empty else 0,
        "drops_max": int(drops_series.max()) if not drops_series.empty else 0,
        "drops_mean": float(drops_series.mean()) if not drops_series.empty else 0.0,
        "drops_median": float(drops_series.median()) if not drops_series.empty else 0.0,
    }

    # ---------- График 1: активные клиенты + падения + эталонная линия ----------

    fig1, ax = plt.subplots(figsize=(10, 6))

    # активные клиенты
    ax.plot(df["seconds"], df["active_clients"], label="active_clients")

    # падения (сколько клиентов отвалилось за секунду)
    ax.plot(df["seconds"], df["drops_per_second"], label="dropped_per_second")

    # эталонная линия expected_clients (зелёная)
    ax.plot(
        df["seconds"],
        df["expected_clients"],
        label="expected_clients",
        color="green",
    )

    # вертикальная линия конца нагрузки
    ax.axvline(ramp_end_second, color="black", linestyle="--", label="end_of_load")

    ax.set_xlabel("Время, сек с начала теста")
    ax.set_ylabel("Количество клиентов")
    ax.set_title("Активные клиенты и падения по секундам")

    ax.set_ylim(0, tester_count)
    ax.grid(True)
    ax.legend(loc="best")

    fig1.tight_layout()
    if save_prefix is not None:
        fig1.savefig(f"{save_prefix}_active_vs_drops.png", dpi=150, bbox_inches="tight")

    # ---------- График 2: отношение активных к ожидаемым ----------

    fig3, ax3 = plt.subplots(figsize=(10, 6))

    ax3.plot(df["seconds"], df["active_over_expected"], label="active / expected")
    ax3.axhline(1.8, linestyle="--", label="ideal = 1.8")
    ax3.axvline(ramp_end_second, color="black", linestyle="--", label="end_of_load")

    ax3.set_xlabel("Время, сек с начала теста")
    ax3.set_ylabel("Доля от ожидаемого числа клиентов")
    ax3.set_title("Коэффициент заполнения: active / expected")
    ax3.set_ylim(0, 2.5)
    ax3.grid(True)
    ax3.legend(loc="best")

    fig3.tight_layout()
    if save_prefix is not None:
        fig3.savefig(f"{save_prefix}_active_over_expected.png", dpi=150, bbox_inches="tight")

    if show_plots:
        plt.show()
    else:
        plt.close(fig1)
        plt.close(fig3)

    return stats


# if __name__ == "__main__":
#     stats = analyze_result(
#         csv_path="stats.csv",
#         tester_start=0,
#         tester_count=1600,
#         show_plots=False,
#     )

#     print("Итоговая статистика по тестерам:")
#     for k, v in stats.items():
#         print(f"{k}: {v}")

#     print("Итоговая статистика (только интервал нагрузки):")
#     print(f"1) Медиана по активным:         {stats['active_median']:.2f}")
#     print(f"2) Среднее по активным:         {stats['active_mean']:.2f}")
#     print(f"3) Максимум по активным:        {stats['active_max']}")
#     print(f"4) Максимум по ошибкам:         {stats['drops_max']}")
#     print(f"5) Среднее по ошибкам:          {stats['drops_mean']:.2f}")
#     print(f"6) Медиана по ошибкам:          {stats['drops_median']:.2f}")
