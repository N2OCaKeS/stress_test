import json
from pathlib import Path

from allta import PageBuilder, ConfluencePublisher, MathModel

from aeb_conf import VM_INFONAME, VM_KERNEL, VM_RESULTS_PATH, VCPU_MIN, RAM_MIN, VCPU_MAX, RAM_MAX

def _load_report(vm: str) -> dict:
    return json.loads((Path(VM_RESULTS_PATH) / vm / "report.json").read_text())


def _runs_table_spec(title: str, runs: list) -> dict:
    return {
        "title": title,
        "headers": ["Volume", "Throughput, eps", "Stabilization, s", "CPU avg, %", "Lost"],
        "rows": [
            {
                "Volume": r["volume"],
                "Throughput, eps": round(r["throughput_eps"], 1),
                "Stabilization, s": round(r["stabilization_duration_s"], 3),
                "CPU avg, %": round(r["cpu"].get("cpu_avg_percent") or 0, 1),
                "Lost": r["lost"],
            }
            for r in runs
        ],
    }


# Эталон (baseline) нагрузочного теста astraeventsd: testvm1 (2 vCPU/4GB), label=baseline,
REFERENCE_RUNS = {
    1000: {"throughput_eps": 13833.2, "stabilization_duration_s": 0.089},
    10000: {"throughput_eps": 26233.4, "stabilization_duration_s": 0.443},
    50000: {"throughput_eps": 23227.3, "stabilization_duration_s": 2.471},
    100000: {"throughput_eps": 21642.9, "stabilization_duration_s": 5.213},
    150000: {"throughput_eps": 23099.6, "stabilization_duration_s": 7.405},
}


def _throughput_chart_spec(runs_by_vm: dict[str, dict[int, dict]]) -> dict:
    volumes = sorted(set.union(*(set(runs) for runs in runs_by_vm.values())))
    return {
        "title": "Throughput, eps по объёму событий",
        "type": "line",
        "x_key": "volume",
        "series": list(runs_by_vm.keys()),
        "x_label": "Объём событий",
        "y_label": "Throughput",
        "y_unit": "eps",
        "data": [
            {
                "volume": volume,
                **{
                    vm: runs[volume]["throughput_eps"]
                    for vm, runs in runs_by_vm.items()
                    if volume in runs
                },
            }
            for volume in volumes
        ],
    }


def _rate_against_reference(runs: dict[int, dict]) -> tuple[float, dict]:
    volumes = sorted(set(REFERENCE_RUNS) & set(runs))

    model = MathModel(type="ratio")
    model.add_criterion(
        "throughput_eps",
        iterations=volumes,
        values=[runs[v]["throughput_eps"] for v in volumes],
        weight=0.5,
        negative=False,
        reference=[REFERENCE_RUNS[v]["throughput_eps"] for v in volumes],
    )
    model.add_criterion(
        "stabilization_duration_s",
        iterations=volumes,
        values=[runs[v]["stabilization_duration_s"] for v in volumes],
        weight=0.5,
        negative=True,
        reference=[REFERENCE_RUNS[v]["stabilization_duration_s"] for v in volumes],
    )
    return model.total_rating()


def build_rating_report(builder: PageBuilder, result_vms: tuple[str, ...] = ("testvm1", "testvm2")):
    """
    Строит таблицу результатов нагрузочного теста для каждой ВМ из результатов и таблицу total_rating
    """
    builder.add_heading(text="Результаты нагрузочного теста", level=2)

    reports: dict[str, dict] = {}
    ratings: dict[str, tuple[float, dict]] = {}
    runs_by_vm: dict[str, dict[int, dict]] = {}
    for vm in result_vms:
        report = _load_report(vm)
        reports[vm] = report
        runs = {r["volume"]: r for r in report["runs"]}
        runs_by_vm[vm] = runs
        ratings[vm] = _rate_against_reference(runs)

    builder.add_table({
        "title": "Total rating",
        "headers": ["VM", "total_rating"],
        "rows": [
            {"VM": vm, "total_rating": round(total, 2)}
            for vm, (total, _criteria) in ratings.items()
        ],
    })

    for vm in result_vms:
        builder.add_table(_runs_table_spec(f"{vm} (результат)", reports[vm]["runs"]))

    builder.add_chart(_throughput_chart_spec(runs_by_vm))

    return ratings


def astra_events_publisher(
        username,
        token,
        space,
        parent_title,
        title,
        stand_number,
        lead_time="",
        test_cycle_version: str | None = None,
):
    preview_path = "report/confluence_report.html"
    reporter = ConfluencePublisher(
        base_url="https://life.astralinux.ru", username=username, token=token
    )
    builder = PageBuilder(title=title)

    with open(VM_INFONAME) as vm_info_av_file:
        vm_info_av = vm_info_av_file.read()
    with open(VM_KERNEL) as vm_info_kernel_file:
        vm_info_kernel = vm_info_kernel_file.read()

    params = [
        {"label": "VCPU vm1 (low resources)", "value": VCPU_MIN},
        {"label": "RAM vm1 (low resources)", "value": RAM_MIN},
        {"label": "VCPU vm2 (upper resources)", "value": VCPU_MAX},
        {"label": "RAM vm2 (upper resources)", "value": RAM_MAX},
    ]

    header_table = [
        {
            "label": "VM Astra Version",
            "value": vm_info_av
        },
        {
            "label": "VM Kernel",
            "value": vm_info_kernel
        },
        {
            "label": "Params",
            "value": {
                "items": params,
            },
        },
        {
            "label": "ARM",
            "value": {
                "stand_number": f"{stand_number}",
            },
        },
        {
            "label": "Lead time",
            "value": {
                "text": lead_time,
            },
        },
    ]

    builder.add_header_table(rows=header_table)
    builder.add_heading(text="Описание", level=2)
    builder.add_paragraph(text="Нагрузочный тест проводится на двух ВМ с разными ресурсами, чтобы оценить производительность и стабильность системы при различных нагрузках. Результаты теста сравниваются с эталонными значениями для определения рейтинга каждой ВМ относительно эталона.")

    build_rating_report(builder)