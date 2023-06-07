

cz_comm = {
    'stand1':{
        '1.7.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-1747 nvme0n1',
        '1.7.3.UU.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-173UU2 nvme0n1'
        },
    'stand4':{
        '1.7.3.UU.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-173UU2 nvme0n1',
        '1.7.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-1747 nvme0n1'
        },
    'stand3':{
        '1.7.3.UU.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-173UU2-new nvme0n1',
        '1.7.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-174-new nvme0n1'
        },
    'stand2':{
        '1.7.3.UU.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-173UU2 sdb'
        }
        
}

#'1.7.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
#                        -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-174 sdb',