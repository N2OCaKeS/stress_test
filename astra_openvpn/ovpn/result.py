import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

import pandas as pd
from allta import Criterion, MathModels

TESTER_RE = re.compile(r"^tester(\d+)$")


@dataclass
class AnalysisResult:
    stats: Dict[str, Any]
    stats_table: List[Dict[str, Any]]
    chart_rows: List[Dict[str, Any]]
    public_chart_rows: List[Dict[str, Any]]
    ramp_end_second: float
    tester_count: int
    # Округлённый целочисленный итоговый рейтинг (в промилле — масштабирован до 1000 единиц)
    total_rating: int


def _parse_clients_field(s: str) -> Set[str]:
    if not isinstance(s, str) or not s:
        return set()
    return {name for name in s.split("|") if name}


def _format_time_label(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def _build_chart_rows(df: pd.DataFrame) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "Время, сек": round(float(row["seconds"]), 2),
                "Активные клиенты": int(row["active_clients"]),
                "Ожидаемые клиенты": round(float(row["expected_clients"]), 2),
                "Падения за секунду": int(row["drops_per_second"]),
                "Коэффициент заполнения": round(float(row["active_over_expected"]), 3),
            }
        )
    return rows


def _build_public_chart_rows(df: pd.DataFrame) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "T": _format_time_label(float(row["seconds"])),
                "Эталон": round(float(row["expected_clients"]), 2),
                "Ошибки": int(row["drops_per_second"]),
                "Активные подключения": int(row["active_clients"]),
            }
        )
    return rows


def _build_stats_table_rows(stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    def fmt(value: Any, digits: int = 2) -> str:
        if isinstance(value, float):
            return f"{value:.{digits}f}"
        return str(value)

    return [
        {
            "Метрика": "Ожидаемое число клиентов",
            "Значение": fmt(stats["expected_testers_total"], 0),
        },
        {
            "Метрика": "Подключались за тест",
            "Значение": fmt(stats["ever_connected_count"], 0),
        },
        {
            "Метрика": "Ни разу не подключились",
            "Значение": fmt(stats["not_connected_count"], 0),
        },        
        {
            "Метрика": "Активны в конце теста",
            "Значение": fmt(stats["active_at_end_count"], 0),
        },
        {
            "Метрика": "Отключились в ходе теста",
            "Значение": fmt(stats["disconnected_count"], 0),
        },
        {
            "Метрика": "Конец набора нагрузки, сек",
            "Значение": fmt(stats["ramp_end_second"], 2),
        },
        {
            "Метрика": "Минимальный коэффициент active/expected",
            "Значение": fmt(stats["ratio_min"], 3),
        },
        {
            "Метрика": "Средний коэффициент active/expected",
            "Значение": fmt(stats["ratio_mean"], 3),
        },
        {
            "Метрика": "Медиана активных клиентов",
            "Значение": fmt(stats["active_median"], 0),
        },
        {
            "Метрика": "Среднее активных клиентов",
            "Значение": fmt(stats["active_mean"], 2),
        },
        {
            "Метрика": "Максимум активных клиентов",
            "Значение": fmt(stats["active_max"], 0),
        },
        {
            "Метрика": "Макс. отключений в секунду",
            "Значение": fmt(stats["drops_max"], 0),
        },
        {
            "Метрика": "Среднее отключений в секунду",
            "Значение": fmt(stats["drops_mean"], 2),
        },
        {
            "Метрика": "Медиана отключений в секунду",
            "Значение": fmt(stats["drops_median"], 0),
        },
    ]


def analyze_result(
    csv_path: str,
    tester_start: int,
    tester_count: int,
    load_end_second: Optional[float] = None,
):
    df = pd.read_csv(csv_path)

    required_cols = {"seconds", "active_clients", "client_names"}
    if not required_cols.issubset(df.columns):
        raise ValueError(f"Ожидаются колонки {required_cols} в CSV")

    df = df.sort_values("seconds").reset_index(drop=True)

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

    expected_testers: Set[str] = {
        f"tester{i}" for i in range(tester_start, tester_start + tester_count)
    }

    # Полный набор данных для графиков.
    seconds: List[float] = df["seconds"].tolist()
    clients_sets_full: List[Set[str]] = [_parse_clients_field(s) for s in df["client_names"]]

    drop_counts: List[int] = [0] * len(df)
    for idx in range(1, len(df)):
        prev_names = clients_sets_full[idx - 1]
        curr_names = clients_sets_full[idx]

        prev_testers = prev_names & expected_testers
        curr_testers = curr_names & expected_testers

        dropped_now = prev_testers - curr_testers
        drop_counts[idx] = len(dropped_now)

    df["drops_per_second"] = drop_counts

    # Ожидаемые клиенты и коэффициенты считаем на всём интервале для графиков.
    expected_clients: List[float] = []
    if ramp_end_second > first_sec:
        denom = ramp_end_second - first_sec
        for t in seconds:
            if t <= ramp_end_second:
                frac = (t - first_sec) / denom
                frac = min(max(frac, 0.0), 1.0)
                e = tester_count * frac
            else:
                e = float(tester_count)
            expected_clients.append(e)
    else:
        expected_clients = [float(tester_count)] * len(seconds)

    df["expected_clients"] = expected_clients

    ratios: List[float] = []
    for active, expected in zip(df["active_clients"], expected_clients):
        if expected > 0:
            ratios.append(active / expected)
        else:
            ratios.append(0.0)
    df["active_over_expected"] = ratios

    # Для графиков используем полный набор данных (всё время снятия).
    df_charts = df.copy()

    # Окно нагрузки для метрик.
    mask_test_window = df["seconds"] <= ramp_end_second
    if not mask_test_window.any():
        mask_test_window = pd.Series([True] * len(df))
    df_stats = df[mask_test_window].reset_index(drop=True)
    clients_sets_stats: List[Set[str]] = [
        _parse_clients_field(s) for s in df_stats["client_names"]
    ]

    ever_connected: Set[str] = set()
    for names in clients_sets_stats:
        ever_connected |= names & expected_testers

    active_last: Set[str] = clients_sets_stats[-1] & expected_testers

    disconnected: Set[str] = ever_connected - active_last

    active_series = df_stats["active_clients"]
    drops_series = df_stats["drops_per_second"]
    ratios_series = df_stats["active_over_expected"]

    stats: Dict[str, Any] = {
        "expected_testers_total": tester_count,  # Сколько клиентов *должно* быть создано (плановое общее число)
        "ever_connected_count": len(ever_connected),  # Сколько уникальных клиентов хоть раз успешно подключились ОСТАВЛЯЕМ + 0,4
        "active_at_end_count": len(active_last),  # Сколько клиентов были активны в последний момент теста
        "disconnected_count": len(disconnected),  # Сколько клиентов отвалились к концу теста (были, но уже не активны) ОСТАВЛЯЕМ - 0,4
        "not_connected_count": tester_count - len(ever_connected),  # Сколько клиентов так и не подключились
        "ramp_end_second": ramp_end_second,  # Время (секунда), когда закончился этап разгона (создания новых подключений)
        "ratio_min": (float(ratios_series.min()) if not ratios_series.empty else 0.0),  # Минимальное значение ratio (активные / ожидаемые) за время теста
        "ratio_mean": (float(ratios_series.mean()) if not ratios_series.empty else 0.0),  # Среднее значение ratio (активные / ожидаемые)
        "active_median": (float(active_series.median()) if not active_series.empty else 0.0),  # Медиана числа активных клиентов по времени
        "active_mean": (float(active_series.mean()) if not active_series.empty else 0.0),  # Среднее число активных клиентов по времени
        "active_max": (int(active_series.max()) if not active_series.empty else 0),  # Максимальное число одновременно активных клиентов
        "drops_max": (int(drops_series.max()) if not drops_series.empty else 0),  # Максимум ошибок/дропов за единицу времени ОСТАВЛЯЕМ - 0,2
        "drops_mean": (float(drops_series.mean()) if not drops_series.empty else 0.0),  # Среднее количество ошибок/дропов за единицу времени
        "drops_median": (float(drops_series.median()) if not drops_series.empty else 0.0),  # Медиана количества ошибок/дропов за единицу времени
    }

    chart_rows = _build_chart_rows(df_charts)
    public_chart_rows = _build_public_chart_rows(df_charts)
    stats_table = _build_stats_table_rows(stats)
    if stats["ever_connected_count"] == 0:
            criterions = [Criterion(name="ever_connected_count", values=[1], weight=0.4, sign=1, lower_bound=0, upper_bound=tester_count),
                  Criterion(name="disconnected_count", values=[tester_count], weight=0.4, sign=-1, lower_bound=0, upper_bound=tester_count),
                  Criterion(name="drops_max", values=[tester_count], weight=0.2, sign=-1, lower_bound=0, upper_bound=tester_count)]
    else:
        criterions = [Criterion(name="ever_connected_count", values=[stats["ever_connected_count"]], weight=0.4, sign=1, lower_bound=0, upper_bound=tester_count),
                    Criterion(name="disconnected_count", values=[stats["disconnected_count"]], weight=0.4, sign=-1, lower_bound=0, upper_bound=tester_count),
                    Criterion(name="drops_max", values=[stats["drops_max"]], weight=0.2, sign=-1, lower_bound=0, upper_bound=tester_count)]

    total_rating, s = MathModels.total_rating(criteria=criterions)
    total_rating = int(round(total_rating))
    return AnalysisResult(
        stats=stats,
        stats_table=stats_table,
        chart_rows=chart_rows,
        public_chart_rows=public_chart_rows,
        ramp_end_second=ramp_end_second,
        tester_count=tester_count,
        total_rating=total_rating,
    )


# if __name__ == "__main__":
#     analysis = analyze_result(
#         csv_path="test_stats.csv",
#         tester_start=0,
#         tester_count=1600,
#     )
#     print("Итоговая статистика по тестерам:")
#     for k, v in analysis.stats.items():
#         print(f"{k}: {v}")
