

cz_comm = {
    'stand1':{
        '1.7.5.9':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-175rc9 nvme0n1',
        '1.7.5.7':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-175rc7 nvme0n1',
        'altlinux-5.10':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-alt nvme0n1',
        'debian11-6.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-debian11-6.1 nvme0n1',
        'debian10':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-debian10 nvme0n1',
        'debian10-5.15':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-debian10-5.15.10 nvme0n1',
        '1.7.5':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-175 nvme0n1',
        '1.7.5.6':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-175rc6 nvme0n1',
        '1.7.5.5':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-175rc6 nvme0n1',
        '1.7.5.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-175rc3 nvme0n1',
        '1.7.4.UU.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-174UU1 nvme0n1',
        '1.7.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-174 nvme0n1',
        '1.7.3.UU.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-173UU2 nvme0n1',
        '1.7.3.UU.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-173UU1 nvme0n1',
        '1.7.3':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-173 nvme0n1',
        '1.7.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-172 nvme0n1',
        '1.7.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.201" \
                -l ru_RU.UTF-8 startdisk restore stand-1-qa-team-13-171 nvme0n1'
        },
    'stand3':{
        '1.7.5.9':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-175rc9 nvme0n1',
        '1.7.5.7':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-175rc7 nvme0n1',
        '1.7.3.UU.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-173UU2 nvme0n1',
        '1.7.3.UU.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-173UU1 nvme0n1',
        '1.7.3':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-173 nvme0n1',
        '1.7.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-174 nvme0n1',
        '1.7.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-172 nvme0n1',
        '1.7.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-171 nvme0n1',
        '1.7.4.UU.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-174UU1 nvme0n1',
        '1.7.5':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-175 nvme0n1',
        '1.7.5.UU.1.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-175UU1rc1 nvme0n1',
        '1.7.5.UU.1.7':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore stand-4-qa-team-13-175UU1rc7 nvme0n1',
        '1.7.6.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore LowServer-176rc1 nvme0n1',
        '1.7.6.3':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore LowServer-176rc3 nvme0n1',
        '1.7.6.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore LowServer-176rc3-flavour nvme0n1',
        '1.8.0.14':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore LowServer-180rc14 nvme0n1',
        '1.8.0.15':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.204" \
                -l ru_RU.UTF-8 startdisk restore LowServer-180-6.6 nvme0n1'
        },
    'stand4':{
        '1.7.5':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-175 nvme0n1',
        '1.7.5.9':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-175rc9 nvme0n1',
        '1.7.5.7':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-175rc7 nvme0n1',
        '1.7.3.UU.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-173UU2 nvme0n1',
        '1.7.3.UU.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-173UU1 nvme0n1',
        '1.7.3':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-173 nvme0n1',
        '1.7.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-174 nvme0n1',
        '1.7.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-172 nvme0n1',
        '1.7.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-171 nvme0n1',
        '1.7.4.UU.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-174UU1 nvme0n1',
        '1.7.5.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-175rc4 nvme0n1',
        '1.7.5.5':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-175rc5 nvme0n1',
        '1.7.5.UU.1.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-175UU1rc1 nvme0n1',
        '1.7.5.UU.1.7':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-175UU1rc7 nvme0n1',
        '1.7.6.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore MiddleServer-176rc1 nvme0n1',
        '1.7.6.3':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore MiddleServer-176rc3 nvme0n1',
        '1.8.0.14':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "10.177.103.203" \
                -l ru_RU.UTF-8 startdisk restore MiddleServer-180rc14 nvme0n1'
        },
    'stand2':{
        '1.7.5':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-175 nvme0n1',
        '1.7.5.9':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-175rc9 nvme0n1',
        '1.7.5.7':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-175rc7 nvme0n1',
        '1.7.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-171 nvme0n1',
        '1.7.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-172 nvme0n1',
        '1.7.3':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-173 nvme0n1',
        '1.7.3.UU.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-173UU1 nvme0n1',
        '1.7.3.UU.2':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-173UU2 nvme0n1',
        '1.7.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-174 nvme0n1',
        '1.7.4.UU.1':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.202" \
                -l ru_RU.UTF-8 startdisk restore stand-2-qa-team-13-174UU1 nvme0n1'
        }
        
}

#'1.7.4':'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h " 10.177.103.203" \
#                        -l ru_RU.UTF-8 startdisk restore stand-3-qa-team-13-174 sdb',