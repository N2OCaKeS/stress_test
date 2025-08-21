main_ceph_ip=$1

for N in $(seq 1 5); do sshpass -p "1" ssh-copy-id -o StrictHostKeyChecking=no ceph-adm@testvm$N; done

sudo cephadm bootstrap --skip-pull --skip-monitoring-stack --mon-ip $main_ceph_ip --ssh-user ceph-adm --initial-dashboard-user "ceph-adm" --initial-dashboard-password "12345678" --dashboard-password-noupdate

sleep 10

for i in {1..5}; do
    echo "Добавляем ceph.pub на testvm$i..."
    if cat /etc/ceph/ceph.pub | sshpass -p "1" ssh -o StrictHostKeyChecking=no ceph-adm@testvm$i "cat >> ~/.ssh/authorized_keys"; then
        echo "ceph.pub на testvm$i добавлен успешно"
    else
        echo "Ошибка при добавлении ceph.pub на testvm$i..."
    fi
    slep 5
done


for i in {2..5}; do
    echo "Добавляем хост testvm$i..."
    if sudo /usr/sbin/cephadm shell -- ceph orch host add "testvm$i"; then
        echo "testvm$i добавлен успешно"
    else
        echo "Ошибка при добавлении testvm$i..."
    fi
    sleep 5
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
    if sudo /usr/sbin/cephadm shell -- ceph orch daemon add osd "testvm${i}:/dev/sda"; then
        echo "OSD успешно добавлен на testvm${i}:/dev/sda"
    else
        echo "Ошибка при добавлении OSD на testvm${i}:/dev/sda"
    fi
    sleep 5
done

sudo cephadm shell -- ceph osd pool create cephfs_data 128
sudo cephadm shell -- ceph osd pool create cephfs_metadata 128

sudo cephadm shell -- ceph fs new cephfs cephfs_metadata cephfs_data
sudo cephadm shell -- ceph fs ls

# Далее разделение на Ceph FS и RBD
sudo cephadm shell -- ceph orch apply mds cephfs --placement='testvm1'

echo `sudo cat /etc/ceph/ceph.client.admin.keyring | grep key | cut -c8-`  > /home/ceph-adm/admin.secret
sleep 5

create_cephfs(){
    sudo mkdir /mnt/cephfs
    sleep 20
    sudo ceph mds stat
    sudo mount -t ceph testvm1,testvm2,testvm3,testvm4,testvm5:/ /mnt/cephfs -o name=admin,secretfile=/home/ceph-adm/admin.secret
    df -h | grep cephfs
}

create_rbd() {
    sudo cephadm shell -- ceph osd pool create rbdpool 128
    sudo cephadm shell -- ceph osd pool application enable rbdpool rbd
    sudo cephadm shell -- rbd create rbdpool/testrbd --size 4096
    sudo rbd device map rbdpool/testrbd --device-type krbd
    sudo mkfs.ext4 -m0 /dev/rbd0
    sudo mkdir /mnt/ceph-device
    sudo mount /dev/rbd0 /mnt/ceph-device
    sudo df -h | grep /mnt/ceph-device
}

if [ "$2" == "cephfs" ]; then
    create_cephfs
elif [ "$2" == "rbd" ]; then
    create_rbd
else
    echo "Неизвестный тип: $2. Допустимые значения: cephfs или rbd"
    exit 1
fi
