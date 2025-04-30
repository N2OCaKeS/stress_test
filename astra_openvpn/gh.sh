del_v() {
    # убить
    sudo virsh destroy astra_openvpn_vpn1
    sudo virsh destroy astra_openvpn_vpn2
    sudo virsh destroy astra_openvpn_balancer
    sudo virsh destroy astra_openvpn_pooler
    echo "убиты"
    # удалить
    sudo virsh undefine astra_openvpn_vpn1
    sudo virsh undefine astra_openvpn_vpn2
    sudo virsh undefine astra_openvpn_balancer
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
    virsh net-undefine vpn-net
    virsh net-undefine pooler-net

    # Удалить интерфейсы
    sudo ip link delete virbr6
    sudo ip link delete virbr7
    # Или
    virsh --connect qemu:///session net-undefine pooler-net
    virsh --connect qemu:///session net-undefine vpn-net
    sudo virsh net-destroy vpn-net
    sudo virsh net-destroy pooler-net
    sudo systemctl restart libvirtd
    sudo virsh net-info vpn-net
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

    sudo virsh net-define lv-nets/vpn-net.xml
    sudo virsh net-define lv-nets/pooler-net.xml

    sudo virsh net-start vpn-net
    sudo virsh net-autostart vpn-net

    sudo virsh net-start pooler-net
    sudo virsh net-autostart pooler-net
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
esac
