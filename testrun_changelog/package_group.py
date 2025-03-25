GROUPS = {
    'Parsec': [],
    'UnixBench': [],
    'Docker/Podman/LXC': [],
    'Apache': ["apache2", "apache2-utils", "apache2-bin", "libapache2-mod-php", "libapache2-mod-security2", "certbot", "python3-certbot-apache"],
    'FreeIPA': ["astra-freeipa-server", "astra-freeipa-client","freeipa-client", "libnss3-tools", "sssd", "sssd-tools", "sssd-ad", "sssd-ipa", "sssd-krb5", "sssd-ldap", "sssd-proxy", "krb5-user"],
    'PostgreSQL': ["postgresql", "postgresql-client", "postgresql-contrib", "postgresql-server-dev-all",  "pgagent", "libpq-dev"],
    'Qemu/KVM/Libvirt': ["qemu", "qemu-kvm", "qemu-utils", "libvirt-daemon-system", "libvirt-clients", "virt-manager", "bridge-utils", "cloud-image-utils", "ovmf"],
    'Системные службы': ['systemd', 'systemd-sysv', 'sysvinit-utils', 'cron', 'anacron', 'logrotate', 'syslog-ng', 'dbus', 'policykit', 'audit'],
    'Файловые системы': ['xfsprogs', 'exfat-utils', 'e2fsprogs', 'dosfstools', 'ntfs-3g']
}