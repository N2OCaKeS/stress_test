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
}

check_box() {
    rm -rf tmp_box
    sudo mkdir tmp_box
    tar -xf 1.8.1.o.box -C tmp_box
    cat tmp_box/metadata.json

}

case $1 in 
    delete)
    del_v
    ;;
    cb)
    check_box
    ;;
esac
