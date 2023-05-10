# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================
import sys
from os import mkdir, chdir, popen
from fsb_conf import STORAGE_MOUNT_DIR
import subprocess


def vd_capacity():
    raw_data = int(str(popen("lsblk | grep sdb1 | awk '{print $4}'").read().strip())[:-1])
    return raw_data * 1024 * 1024 * 1024


def cmd(command):
    return subprocess.run(command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

########################################################################################################################


'''
    Заполнение сущностями
'''


# Создание файлов. Занять файлами % процентов доступного объёма диска
def create_file(count: int, occupied_volume_percent: int, mount_dir=STORAGE_MOUNT_DIR):
    occupied_volume_bait = int(vd_capacity() * (occupied_volume_percent / 100))
    file_size = int(occupied_volume_bait // count)
    try:
        mkdir('{}/files'.format(mount_dir))
    except FileExistsError:
        cmd('rm -rf {}/files/*'.format(mount_dir))
    for index in range(count):
        cmd('fallocate -l {size} {dir}/files/{file}'.format(size=file_size, dir=mount_dir, file=index))


# Создание симв.ссылок. Занять файлами % процентов доступного объёма диска
def create_symlink(count: int, occupied_volume_percent: int, mount_dir=STORAGE_MOUNT_DIR):
    occupied_volume_bait = int(vd_capacity() * (occupied_volume_percent / 100))
    file_size = int(occupied_volume_bait // count)
    try:
        mkdir('{}/symlinks'.format(mount_dir))
    except FileExistsError:
        cmd('rm -rf {}/symlinks/*'.format(mount_dir))
    for index in range(count):
        cmd('fallocate -l {size} {dir}/symlinks/{file}'.format(size=file_size, dir=mount_dir, file=index))
        cmd('ln -s {dir}/symlinks/{file} {dir}/symlinks/symlink_{file}'.format(dir=mount_dir, file=index))


# Создание хардлинков. Занять файлами % процентов доступного объёма диска
def create_hardlink(count: int, occupied_volume_percent: int, mount_dir=STORAGE_MOUNT_DIR):
    occupied_volume_bait = int(vd_capacity() * (occupied_volume_percent / 100)) / 2
    file_size = int(occupied_volume_bait // count)
    try:
        mkdir('{}/hardlinks'.format(mount_dir))
    except FileExistsError:
        cmd('rm -rf {}/hardlinks/*'.format(mount_dir))
    for index in range(count):
        cmd('fallocate -l {size} {dir}/hardlinks/{file}'.format(size=file_size, dir=mount_dir, file=index))
        cmd('ln {dir}/hardlinks/{file} {dir}/hardlinks/hardlink_{file}'.format(dir=mount_dir, file=index))


# Создание архивов. Занять архивами % процентов доступного объёма диска
def create_arch(count: int, occupied_volume_percent: int, mount_dir=STORAGE_MOUNT_DIR):
    occupied_volume_bait = int(vd_capacity() * (occupied_volume_percent / 100))
    file_size = int(occupied_volume_bait // count)
    try:
        mkdir('{}/archs'.format(mount_dir))
    except FileExistsError:
        cmd('rm -rf {}/archs/*'.format(mount_dir))
    chdir('{}/archs'.format(mount_dir))
    for index in range(count):
        cmd('mkdir {dir}'.format(dir=index))
        cmd('fallocate -l {size} {dir}/{file}'.format(size=file_size, dir=index, file=index))
        cmd('tar -cf {file}.tar {file}'.format(file=index))
        cmd('rm -rf {file}'.format(file=index))


# Создание iso. Занять исошками % процентов доступного объёма диска
def create_iso(count: int, occupied_volume_percent: int, mount_dir=STORAGE_MOUNT_DIR):
    occupied_volume_bait = int(vd_capacity() * (occupied_volume_percent / 100))
    file_size = int(occupied_volume_bait // count)
    try:
        mkdir('{}/isos'.format(mount_dir))
    except FileExistsError:
        cmd('rm -rf {}/isos/*'.format(mount_dir))
    chdir('{}/isos'.format(mount_dir))
    for index in range(count):
        cmd('mkdir {dir}'.format(dir=index))
        cmd('fallocate -l {size} {dir}/{file}'.format(size=file_size, dir=index, file=index))
        cmd('genisoimage -o {file}.iso {file}'.format(file=index))
        cmd('rm -rf {file}'.format(file=index))


'''
    Создание больших файлов
'''


# Создание большого файла. % процентов доступного объёма диска - один файл
def create_big_file(occupied_volume_percent: int, mount_dir=STORAGE_MOUNT_DIR):
    occupied_volume_bait = int(vd_capacity() * (occupied_volume_percent / 100)) / 3
    file_size = int(occupied_volume_bait)
    try:
        mkdir('{}/big_files'.format(mount_dir))
    except FileExistsError:
        cmd('rm -rf {}/big_files/*'.format(mount_dir))
    cmd('fallocate -l {size} {dir}/big_files/big_file'.format(size=file_size, dir=mount_dir))


# Создание большого архива. % процентов доступного объёма диска - один архив
def create_big_arch(occupied_volume_percent: int, mount_dir=STORAGE_MOUNT_DIR):
    occupied_volume_bait = int(vd_capacity() * (occupied_volume_percent / 100)) / 3
    file_size = int(occupied_volume_bait)
    try:
        mkdir('{}/big_archs'.format(mount_dir))
    except FileExistsError:
        cmd('rm -rf {}/big_archs/*'.format(mount_dir))
    chdir('{}/big_archs'.format(mount_dir))
    cmd('mkdir big')
    cmd('fallocate -l {size} big/file'.format(size=file_size))
    cmd('tar -cf big_arch.tar big')
    cmd('rm -rf big')


# Создание большого iso. % процентов доступного объёма диска - один iso
def create_big_iso(occupied_volume_percent: int, mount_dir=STORAGE_MOUNT_DIR):
    occupied_volume_bait = int(vd_capacity() * (occupied_volume_percent / 100)) / 3
    file_size = int(occupied_volume_bait)
    try:
        mkdir('{}/big_isos'.format(mount_dir))
    except FileExistsError:
        cmd('rm -rf {}/big_isos/*'.format(mount_dir))
    chdir('{}/big_isos'.format(mount_dir))
    cmd('mkdir big')
    cmd('fallocate -l {size} big/file'.format(size=file_size))
    cmd('genisoimage -o big_iso.iso big')
    cmd('rm -rf big')


########################################################################################################################
'''
    Удаление сущностей
'''


# Удаление файлов.
def del_file(mount_dir=STORAGE_MOUNT_DIR):
    cmd('rm -rf {}/files/*'.format(mount_dir))
    cmd('rm -rf {}/big_files/*'.format(mount_dir))


# Удаление симв.ссылок.
def del_symlink(mount_dir=STORAGE_MOUNT_DIR):
    cmd('rm -rf {}/symlinks/*'.format(mount_dir))


# Удаление хардлинков.
def del_hardlink(mount_dir=STORAGE_MOUNT_DIR):
    cmd('rm -rf {}/hardlinks/*'.format(mount_dir))


# Удаление архивов.
def del_arch(mount_dir=STORAGE_MOUNT_DIR):
    cmd('rm -rf {}/archs/*'.format(mount_dir))
    cmd('rm -rf {}/big_archs/*'.format(mount_dir))


# Удаление iso.
def del_iso(mount_dir=STORAGE_MOUNT_DIR):
    cmd('rm -rf {}/isos/*'.format(mount_dir))
    cmd('rm -rf {}/big_isos/*'.format(mount_dir))