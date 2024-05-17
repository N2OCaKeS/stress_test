# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess
import os

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
                             'xfs',
                             'exfat'],
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

def check_output_command(command, out=None):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    output, errors = result.communicate()
    output = os.linesep.join([s for s in output.splitlines() if s])
    errors = os.linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    elif out != None:
        return errors + output
    else:
        return errors

fact_storage_name = check_output_command("lsblk | awk 'NR==2' | awk '{print $1;}'")
if STORAGE_NAME != fact_storage_name:
    STORAGE_NAME = fact_storage_name
    print(f'Actual storage name - {fact_storage_name}')
    print(f'Used storage changed to - {STORAGE_NAME}')
else: print(f'Used storage - {STORAGE_NAME}')

cmd('apt install -y libpdp-dev parted')

# Проверка наличия диска
if cmd('lsblk | grep {device}'.format(device=STORAGE_NAME)) == 0:
    if cmd('lsblk | grep {device}1'.format(device=STORAGE_NAME)) == 0:
        cmd('umount /mnt')
        cmd('parted -s /dev/{device} select && parted -s /dev/{device} rm 1'.format(device=STORAGE_NAME))

if args.FS == 'fat':
    cmd('parted -s /dev/{device} mklabel gpt mkpart primary fat32 0% 100%'.format(device=STORAGE_NAME))
    cmd("mkfs -t {fs} -I /dev/{device}1".format(fs=args.FS, device=STORAGE_NAME))
elif args.FS == 'ntfs':
    cmd('strace -o strace_ntfs_parted.log parted -s /dev/{device} mklabel gpt mkpart primary ntfs 0% 100%'.format(device=STORAGE_NAME))
    cmd("strace -o strace_ntfs_mkfs.log mkfs -t {fs} -I /dev/{device}1".format(fs=args.FS, device=STORAGE_NAME))
elif args.FS == 'xfs':
    cmd('parted -s /dev/{device} mklabel gpt mkpart primary xfs 0% 100%'.format(device=STORAGE_NAME))
    cmd("mkfs -t {fs} -f /dev/{device}1".format(fs=args.FS, device=STORAGE_NAME))
elif args.FS == 'exfat':
    cmd('parted -s /dev/{device} mklabel gpt mkpart primary 0% 100%'.format(device=STORAGE_NAME))
    cmd("mkfs -t exfat /dev/{device}1".format(fs=args.FS, device=STORAGE_NAME))
else:
    cmd('parted -s /dev/{device} mklabel gpt mkpart primary {fs} 0% 100%'.format(fs=args.FS ,device=STORAGE_NAME))
    cmd("mkfs -t {fs} {ic} -F /dev/{device}1".format(fs=args.FS, device=STORAGE_NAME, ic=INODE_COUNT))

cmd("mount /dev/{device}1 {mount_dir}".format(device=STORAGE_NAME, mount_dir=STORAGE_MOUNT_DIR))




#To manual nvme test
#dd if=/dev/zero of=test_dir bs=1M count=1000
#sudo mkfs.xfs test_dir
#mkdir /tmp/test_dir
#sudo mount -o loop test_dir /tmp/test_dir
#/home/u/git/stress_test/file_systems/fs_mark-3.3/fs_mark -d /tmp/test_dir -s 1024 -n 10000 -v
#/home/u/git/stress_test/file_systems/fs_mark-3.3/fs_mark -d /tmp/test_dir -s 1024 -n 50000 -v
#/home/u/git/stress_test/file_systems/fs_mark-3.3/fs_mark -d /tmp/test_dir -s 1024 -n 90000 -v

