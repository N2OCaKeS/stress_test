from time import sleep

from allta import VBox, VBoxManager
from roles.database.db import DatabaseVM
from roles.domain.domain import DomainVM
from roles.load_balancer.load_balancer import LoadBalancer
from roles.task.pre_configure import PreConfigure
from roles.task.test import Test
from roles.vm_info import VERSION_OS, VMS, VMS_DATES


def main():
    vagrant_path = './'
    prepare_path = './prepare/prepare.sh'

    provider = VBox()
    provider.prepare(prepare_path)

    if VERSION_OS == '1.7':
        provider.build(vagrant_path, f'1.7.5.s', '1.7.5', VMS, VMS_DATES)

    elif VERSION_OS == '1.8':
        provider.build(vagrant_path, f'1.8.1.s', '1.8.1.6', VMS, VMS_DATES)

    print('Ожидаем 30 секунд перед началом теста')
    # sleep(30)

    provider.check(VMS, VMS_DATES)

    configure = PreConfigure()  # Проверено работает
    configure.set_hosts()
    configure.apt_install()
    # VBoxManager.create_snapshot(vms=VMS, snapshot_name='apt_and_hosts')

    domain = DomainVM()  # Проверено работает
    domain.settings()

    database = DatabaseVM()  # Проверено работает
    database.settings()
    # VBoxManager.create_snapshot(vms=VMS, snapshot_name='DB')

    # На проверке в случае провала узнать как проверять какие бд в сети
    load_balancer = LoadBalancer()
    load_balancer.load()

    test = Test()  # TODO Настроить скрипт и создать необходимую бд
    test.test()


def balance(rc):
    vagrant_path = './'
    prepare_path = './prepare/prepare.sh'

    provider = VBox()
    provider.prepare(prepare_path)

    if VERSION_OS == '1.7':
        provider.build(vagrant_path, f'1.7.5.s', rc, VMS, VMS_DATES)

    elif VERSION_OS == '1.8':
        provider.build(vagrant_path, f'1.8.1.s', rc, VMS, VMS_DATES)

    print('Ожидаем 30 секунд перед началом теста')
    # sleep(30)

    provider.check(VMS, VMS_DATES)

    configure = PreConfigure()  # Проверено работает
    configure.set_hosts()
    configure.apt_install()
    # VBoxManager.create_snapshot(vms=VMS, snapshot_name='apt_and_hosts')

    domain = DomainVM()  # Проверено работает
    domain.settings()

    database = DatabaseVM()  # Проверено работает
    database.settings()
    # VBoxManager.create_snapshot(vms=VMS, snapshot_name='DB')

    # На проверке в случае провала узнать как проверять какие бд в сети
    load_balancer = LoadBalancer()
    load_balancer.load()

    test = Test()  # TODO Настроить скрипт и создать необходимую бд
    test.test()
    test.get_result()

if __name__ == "__main__":
    main()
