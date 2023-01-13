# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess

from fsb_conf import STORAGE_NAME, STORAGE_MOUNT_DIR, INODE_COUNT

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('--fs',
                    action='store',
                    choices=['ext2',
                             'ext3',
                             'ext4',
                             'fat',
                             'ntfs',
                             'xfs'],
                    required=True,
                    help='filesystem',
                    dest='FS')
args = parser.parse_args()


def cmd(command,
        good_color='\033[92m',
        mid_color='\033[93m',
        bad_color='\033[91m',
        def_color='\033[0m'):

    code = subprocess.run(command, shell=True, stderr=subprocess.DEVNULL).returncode
    if code == 0:
        print('{}# +++ {}{}'.format(good_color, command, def_color))
        return code
    elif code == 1:
        print('{}# +-+ {}{}'.format(mid_color, command, def_color))
        return code
    else:
        print('{}# --- {}{}'.format(bad_color, command, def_color))
        return code
        exit(2)


# Проверка наличия диска
if cmd('lsblk | grep {device}'.format(device=STORAGE_NAME)) == 0:
    if cmd('lsblk | grep {device}1'.format(device=STORAGE_NAME)) == 0:
        cmd('umount /mnt')
        cmd('parted -s /dev/{device} select && parted -s /dev/{device} rm 1'.format(device=STORAGE_NAME))

cmd('apt install -y libpdp-dev')

if args.FS == 'fat':
    cmd('parted -s /dev/{device} mklabel msdos mkpart primary fat32 0% 100%'.format(device=STORAGE_NAME))
    cmd("mkfs -t {fs} -I /dev/{device}1".format(fs=args.FS, device=STORAGE_NAME))
elif args.FS == 'ntfs':
    cmd('parted -s /dev/{device} mklabel msdos mkpart primary ntfs 0% 100%'.format(device=STORAGE_NAME))
    cmd("mkfs -t {fs} -I /dev/{device}1".format(fs=args.FS, device=STORAGE_NAME))
elif args.FS == 'xfs':
    cmd('parted -s /dev/{device} mklabel msdos mkpart primary xfs 0% 100%'.format(device=STORAGE_NAME))
    cmd("mkfs -t {fs} -f /dev/{device}1".format(fs=args.FS, device=STORAGE_NAME))
else:
    cmd('parted -s /dev/{device} mklabel msdos mkpart primary {fs} 0% 100%'.format(fs=args.FS ,device=STORAGE_NAME))
    cmd("mkfs -t {fs} {ic} -F /dev/{device}1".format(fs=args.FS, device=STORAGE_NAME, ic=INODE_COUNT))

cmd("mount /dev/{device}1 {mount_dir}".format(device=STORAGE_NAME, mount_dir=STORAGE_MOUNT_DIR))