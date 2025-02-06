

std_user = 'u'
std_password = '1'

project_path = '/home/u/folder_git_for_infocollector/stress_test/allta_infocollector'
grafana_name_service = 'grafana_prometheus.service'
exporter_name_service = 'node_exporter.service'
log_file = 'logs/infocollector.log'
collector_name = 'create_collector.sh'
collector_file = f'src/collector/{collector_name}'
server_ip = '10.177.103.10'

fd_chanks_1 = 'http://10.177.103.10:3000/d/rYdddlPWk/node-exporter-full?orgId=1&from=now-5m&to=now&timezone=browser&var-datasource=default&var-job='
fd_chanks_2 = 'node_exporter&var-node={}:9100&var-diskdevices=%5Ba-z%5D%2B%7Cnvme%5B0-9%5D%2Bn%5B0-9%5D%2B%7Cmmcblk%5B0-9%5D%2B&refresh=5s&kiosk'
full_dashboard = fd_chanks_1 + fd_chanks_2

ad_chanks_1 = 'http://10.177.103.10:3000/d/dec64yvju0dtsf/node-exporter-by-allta?var-interval=$__auto&orgId=1&from=now-15m&to=now&timezone=browser&var'
ad_chanks_2 = '-node={}:9100&var-maxmount=%2Fetc%2Fhostname&refresh=5s&kiosk'
allta_dashboard = ad_chanks_1 + ad_chanks_2