RESTORE_DISK_COMMAND = 'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "{ip_address}" -l ru_RU.UTF-8 startdisk restore {snapshot_name} {stand_disk}'
SAVE_DISK_COMMAND = 'sudo drbl-ocs -b -q2 -j2 -fsck-y -p reboot -z9p -i 10000000 -h "{ip_address}" -l ru_RU.UTF-8 startdisk save {snapshot_name} {stand_disk}'
LOCALBOOT = 'sudo dcs -h "{ip_address}" local'