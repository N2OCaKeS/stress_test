# from allta import PageBuilder, ConfluencePublisher Uncomment to work

import json
from allta import PageBuilder, ConfluencePublisher

from kernel.kernel_conf import IOF_RESULTS, VM_INFONAME, VM_KERNEL, SEGMENTATION_FAULT_RAM, SEGMENTATION_FAULT_VCPU, ITERATIONS

def net_publisher(
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
        {"label": "VCPU", "value": SEGMENTATION_FAULT_VCPU},
        {"label": "RAM", "value": SEGMENTATION_FAULT_RAM},
        {"label": "Test iterations", "value": ITERATIONS},
    ]

    header_table = [
        {
            "label": "VM Astra Version",
            "value": vm_info_av
        },
        {
            "lavel": "VM Kernel",
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
    builder.add_paragraph(text="В тесте производится оценка сетевой производительности с помощью iperf при разных значениях параметра ядра init_on_free.\nСравниваются результаты в базовом состоянии (до изменения параметра) и после установки init_on_free=off.")

    with open(IOF_RESULTS, 'r') as f:
        iof_results_dict = json.load(f)

    builder.add_table({
        "title": "Результаты тестирования",
        "headers": ["init_on_free=on MBytes/sec (mean)", "init_on_free=off MBytes/sec (mean)", "Difference %"],
        "rows": [[iof_results_dict['init_on_free_ON'], iof_results_dict['init_on_free_OFF'], iof_results_dict['difference']]],
    })

    publish_result = reporter.publish_results_from_params(
        conf_space=space,
        conf_parent_page=parent_title,
        conf_new_page_name=title,
        test_cycle_version=test_cycle_version,
        body=builder,
        attachments=[*builder.attachments],
    )
    return builder, preview_path, publish_result

