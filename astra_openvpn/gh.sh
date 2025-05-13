del_v() {
    # убить
    sudo virsh destroy astra_openvpn_pooler
    echo "убиты"
    # удалить
    sudo virsh undefine astra_openvpn_pooler
    echo "удалены"

    sudo rm -rf ~/.local/share/libvirt/images/*
    sudo rm -rf /home/askeladd/.vagrant.d/boxes/
    sudo rm -rf /var/log/libvirt/*
    sudo rm -rf /var/lib/libvirt/images/*
    sudo rm -rf /tmp/vagrant*
    sudo rm -rf ~/.vagrant.d/tmp/

    sudo du -sh /var/lib/libvirt/images/*
    sudo du -sh ~/.vagrant.d/*
    sudo du -sh ~/.local/share/libvirt/images/*


    sudo vagrant destroy -f
    sudo vagrant box remove -f


    # Переопределить сетку
    virsh net-undefine pooler-net

    # Удалить интерфейсы
    sudo ip link delete virbr6
    sudo ip link delete virbr7
    # Или
    virsh --connect qemu:///session net-undefine pooler-net
    sudo virsh net-destroy pooler-net
    sudo systemctl restart libvirtd
    sudo virsh net-info pooler-net
}

check_box() {
    rm -rf tmp_box
    sudo mkdir tmp_box
    tar -xf 1.8.1.o.box -C tmp_box
    cat tmp_box/metadata.json

}

start_nets() {
    export LIBVIRT_DEFAULT_URI="qemu:///system"

    # пересоздание пулла default
    sudo mkdir -p /var/lib/libvirt/images
    sudo virsh pool-define-as default dir - - - - "/var/lib/libvirt/images"
    sudo virsh pool-build default
    sudo virsh pool-start default
    sudo virsh pool-autostart default
    sudo chown -R root:libvirt /var/lib/libvirt/images
    sudo chmod -R 775 /var/lib/libvirt/images


    # 2 сетки для теста
    sudo virsh net-define lv-nets/vpn-net.xml
    sudo virsh net-define lv-nets/pooler-net.xml

    sudo virsh net-start vpn-net
    sudo virsh net-autostart vpn-net

    sudo virsh net-start pooler-net
    sudo virsh net-autostart pooler-net
}

complex() {
    vagrant destroy -f
    rm -rf .vagrant
    rm -rf /var/lib/libvirt/images/testvm2.img
    rm -rf /root/.vagrant.d/boxes/orel-vanilla-gui-VAGRANTSLASH-1.7.5
    virsh net-destroy test
    virsh net-destroy vagrant-libvirt
    virsh net-undefine test
    virsh net-undefine vagrant-libvirt
    virsh net-define lv-nets/test.xml
    virsh net-start test
    virsh net-autostart test

    # Удаляем домены
    sudo virsh destroy astra_openvpn_vpn1
    sudo virsh destroy astra_openvpn_pooler
    sudo virsh undefine astra_openvpn_vpn1
    sudo virsh undefine astra_openvpn_pooler

    # Удаляем все зависшие интерфейсы
    sudo systemctl restart libvirtd
    sudo rm -f /var/lib/libvirt/dnsmasq/*

    vagrant destroy -f
    rm -rf ~/.vagrant.d/tmp/*
    rm -rf .vagrant/

    sudo rm -rf /var/lib/libvirt/images/astra_*
    sudo rm -f /var/lib/libvirt/images/astra_openvpn_pooler*.qcow2
    sudo rm -rf ~/.vagrant.d/tmp/*
    sudo rm -rf /root/.vagrant.d/boxes/1.7*

    sudo rm /var/lib/libvirt/images/testvm1.*
    sudo vagrant destroy -f testvm1 testvm2
    sudo vagrant global-status --prune
    sudo virsh pool-refresh default
    sudo virsh vol-delete testvm1.img --pool default || true
    sudo virsh vol-delete testvm2.img --pool default || true

    # пересоздание пулла default
    # sudo mkdir -p /var/lib/libvirt/images
    # sudo virsh pool-define-as default dir - - - - "/var/lib/libvirt/images"
    # sudo virsh pool-build default
    # sudo virsh pool-start default
    # sudo virsh pool-autostart default
    # sudo chown -R root:libvirt /var/lib/libvirt/images
    # sudo chmod -R 775 /var/lib/libvirt/images

    virsh list --all
    virsh net-list --all
    virsh pool-list --all


}

info() {
    sudo virsh net-list --all
    sudo vagrant status
    sudo virsh list --all
    sudo virsh pool-list --all
}

clear_vagrant() {
    sudo rm -rf ~/.vagrant.d/boxes/astra_*
    sudo rm -rf /var/lib/libvirt/images/astra_*
    sudo rm -f /var/lib/libvirt/images/astra_openvpn_pooler*.qcow2
    rm -rf ~/.vagrant.d/tmp/*
    vagrant destroy -f
}

case $1 in 
    delete)
    del_v
    ;;
    cb)
    check_box
    ;;
    ss)
    start_nets
    ;;
    i)
    info
    ;;
    cv)
    clear_vagrant
    ;;
    co)
    complex
    ;;
esac
