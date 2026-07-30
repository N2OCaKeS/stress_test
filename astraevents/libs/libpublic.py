import json
import shutil
from pathlib import Path

from allta import PageBuilder, ConfluencePublisher, MathModel

from aeb_conf import VM_INFONAME, VM_KERNEL, VM_RESULTS_PATH, VCPU_MIN, RAM_MIN, VCPU_MAX, RAM_MAX

def _report_path(vm: str) -> Path:
    return Path(VM_RESULTS_PATH) / vm / "report.json"


def _load_report(vm: str) -> dict:
    return json.loads(_report_path(vm).read_text())


def _attachment_path(vm: str) -> Path:
    # Оба report.json называются одинаково — Confluence различает вложения на
    # странице по имени файла, а не по полному пути, поэтому для вложения нужна
    # копия с именем, включающим ВМ, иначе второй файл перезапишет первый.
    destination = Path(VM_RESULTS_PATH) / f"{vm}_report.json"
    shutil.copyfile(_report_path(vm), destination)
    return destination


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


REFERENCE_RUNS = {
    "testvm1": {
        1000: {"throughput_eps": 13718.4, "stabilization_duration_s": 0.088},
        10000: {"throughput_eps": 28297.3, "stabilization_duration_s": 0.425},
        50000: {"throughput_eps": 35627.1, "stabilization_duration_s": 1.762},
        100000: {"throughput_eps": 26506.1, "stabilization_duration_s": 4.460},
        150000: {"throughput_eps": 22352.8, "stabilization_duration_s": 7.759},
    },
    "testvm2": {
        1000: {"throughput_eps": 13477.6, "stabilization_duration_s": 0.090},
        10000: {"throughput_eps": 27874.8, "stabilization_duration_s": 0.444},
        50000: {"throughput_eps": 28247.4, "stabilization_duration_s": 2.132},
        100000: {"throughput_eps": 30631.8, "stabilization_duration_s": 3.993},
        150000: {"throughput_eps": 30031.4, "stabilization_duration_s": 6.048},
    },
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


def _rate_against_reference(runs_by_vm: dict[str, dict[int, dict]]) -> tuple[float, dict]:
    """
    Один total_rating по обеим ВМ: для каждой ВМ throughput_eps и stabilization_duration_s
    добавляются в MathModel как отдельные критерии (per-VM), вес поровну на всех.
    Итог — одно число, но baseline/result/ratio каждой ВМ по-прежнему видны отдельно
    в criteria — расхождение между ВМ не теряется внутри total_rating.
    """
    model = MathModel(type="ratio")
    weight = 1.0 / (len(runs_by_vm) * 2)
    for vm, runs in runs_by_vm.items():
        reference = REFERENCE_RUNS[vm]
        volumes = sorted(set(reference) & set(runs))
        model.add_criterion(
            f"throughput_eps_{vm}",
            iterations=volumes,
            values=[runs[v]["throughput_eps"] for v in volumes],
            weight=weight,
            negative=False,
            reference=[reference[v]["throughput_eps"] for v in volumes],
        )
        model.add_criterion(
            f"stabilization_duration_s_{vm}",
            iterations=volumes,
            values=[runs[v]["stabilization_duration_s"] for v in volumes],
            weight=weight,
            negative=True,
            reference=[reference[v]["stabilization_duration_s"] for v in volumes],
        )
    return model.total_rating()


def build_rating_report(builder: PageBuilder, result_vms: tuple[str, ...] = ("testvm1", "testvm2")):
    """
    Строит таблицу результатов нагрузочного теста для каждой ВМ из результатов и таблицу total_rating
    """
    builder.add_heading(text="Результаты нагрузочного теста", level=2)

    reports: dict[str, dict] = {}
    runs_by_vm: dict[str, dict[int, dict]] = {}
    for vm in result_vms:
        report = _load_report(vm)
        reports[vm] = report
        runs_by_vm[vm] = {r["volume"]: r for r in report["runs"]}

    total, criteria = _rate_against_reference(runs_by_vm)
    builder.add_paragraph(text=f"Total rating: {round(total)}")

    for vm in result_vms:
        builder.add_table(_runs_table_spec(f"{vm} (результат)", reports[vm]["runs"]))
        builder.add_attachment(_attachment_path(vm), title=f"{vm} report.json")

    builder.add_chart(_throughput_chart_spec(runs_by_vm))

    return total, criteria


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

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result