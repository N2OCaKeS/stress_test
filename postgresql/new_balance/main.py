from allta import VBox

from roles.task.test import Test
from roles.load_balancer.load_balancer import LoadBalancer
from roles.database.db import DatabaseVM
from roles.domain.domain import DomainVM
from roles.task.pre_configure import PreConfigure

from roles.vm_info import VMS_DATES, VMS, VERSION_OS
from time import sleep


def main():
    vagrant_path = './'
    prepare_path = './prepare/prepare.sh'

    provider = VBox()
    provider.prepare(prepare_path)

    if VERSION_OS == '1.7':
        provider.build(vagrant_path, f'1.7.5.s', '1.7.5', VMS, VMS_DATES)

    elif VERSION_OS == '1.8':
        provider.build(vagrant_path, f'1.8.1.s', '1.8.1.6', VMS, VMS_DATES)

    print('Ожидаем 2 мин перед началом теста')
    sleep(120)

    provider.check(VMS, VMS_DATES)

    configure = PreConfigure()  # Проверено работает
    configure.set_hosts()
    configure.apt_install()

    domain = DomainVM()  # Проверено работает
    domain.settings()

    database = DatabaseVM()  # Проверено работает
    database.settings()

    # На проверке в случае провала узнать как проверять какие бд в сети
    load_balancer = LoadBalancer()
    load_balancer.load()

    test = Test()  # TODO Настроить скрипт и создать необходимую бд
    test.test()


if __name__ == "__main__":
    main()
