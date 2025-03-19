from .Allta.vm_controller.VBox import VBox

pro = VBox

vms = ['database1', 'database2', 'database3',
       'lbdb1', 'lbdb2', 'lbdb3', 'dcfreeipa']

vms_dates = {
    'database1': {
        "host-port": "22",
        "ip_bridge": "10.177.103.111",
        "cpus": "4",
        "memory": "32768",
        "disk":"40960"
    },
    'database2': {
        "host-port": "22",
        "ip_bridge": "10.177.103.112",
        "cpus": "4",
        "memory": "32768",
        "disk":"40960"
    },
    'database3': {
        "host-port": "22",
        "ip_bridge": "10.177.103.113",
        "cpus": "8",
        "memory": "32768",
        "disk":"40960"
    },
    'lbdb1': {
        "host-port": "22",
        "ip_bridge": "10.177.103.141",
        "cpus": "8",
        "memory": "32768",
        "disk":"40960"
    },
    'lbdb2': {
        "host-port": "22",
        "ip_bridge": "10.177.103.142",
        "cpus": "8",
        "memory": "32768",
        "disk":"40960"
    },
    'lbdb3': {
        "host-port": "22",
        "ip_bridge": "10.177.103.143",
        "cpus": "8",
        "memory": "32768",
        "disk":"40960"
    },
    'dcfreeipa': {
        "host-port": "22",
        "ip_bridge": "10.177.103.110",
        "cpus": "8",
        "memory": "32768",
        "disk":"40960"
    }
}


pro.build('./', '1.8.0.s', '1.8.0', vms, vms_dates, './prov.sh')

pro = VBox
pro.hosts()