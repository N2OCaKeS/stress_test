main_ceph_ip=$1
sudo cephadm bootstrap --skip-pull --skip-monitoring-stack --mon-ip $main_ceph_ip --ssh-user ceph-adm --initial-dashboard-user "ceph-adm" --initial-dashboard-password "12345678" --dashboard-password-noupdate

cat /etc/ceph/ceph.pub | ssh ceph-adm@$host 'cat >> .ssh/authorized_keys'

for i in {2..5}; do
    echo "Добавляем хост testvm$i..."
    if sudo /usr/sbin/cephadm shell -- ceph orch host add "testvm$i"; then
        echo "testvm$i добавлен успешно"
    else
        echo "Ошибка при добавлении testvm$i..."
    fi
    slep 5
done

for i in {2..5}; do
    if sudo /usr/sbin/cephadm shell -- ceph orch host label add "testvm$i" _admin; then
        echo "Метка _admin добавлена на testvm$i"
    else
        echo "Ошибка при добавлении метки на testvm$i..."
    fi
    sleep 5
done

for i in {1..5}; do
    if sudo /usr/sbin/cephadm shell -- ceph orch daemon add osd "testvm${i}:/dev/sdb"; then
        echo "OSD успешно добавлен на testvm${i}:/dev/sdb"
    else
        echo "Ошибка при добавлении OSD на testvm${i}:/dev/sdb"
    fi
    sleep 5
done

sudo cephadm shell -- ceph osd pool create cephfs_data 128
sudo cephadm shell -- ceph osd pool create cephfs_metadata 128

sudo cephadm shell -- ceph fs new cephfs cephfs_metadata cephfs_data
sudo cephadm shell -- ceph fs ls

# Далее разделение на Ceph FS и RBD
sudo cephadm shell -- ceph orch apply mds cephfs --placement='testvm1'
# TODO