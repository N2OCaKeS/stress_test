# От пользователя ceph-amd

# Указать основную
# ssh-copy-id ceph-adm@astra-ceph-admin
sshpass -p "1" ssh-copy-id -o StrictHostKeyChecking=no ceph-adm@testvm1;
for N in $(seq 2 4); do sshpass -p "1" ssh-copy-id -o StrictHostKeyChecking=no ceph-adm@testvm$N; done
ceph-deploy --username ceph-adm install --mon --osd testvm2 testvm3 testvm4
for N in $(seq 2 4); do ssh ceph-adm@testvm$N sudo reboot; done

sleep 300

ceph-deploy --username ceph-adm install --mgr testvm2
ceph-deploy --username ceph-adm new testvm2 testvm3 testvm4

ceph-deploy --username ceph-adm mon create-initial
ceph-deploy --username ceph-adm mgr create testvm2

for i in {2..4}; do
    if ceph-deploy --username ceph-adm osd create --data /dev/sda "testvm${i}"; then
        echo "OSD успешно добавлен на testvm${i}"
    else
        echo "Ошибка при добавлении OSD на testvm${i}"
    fi
    sleep 5
done

# Указать основную
ceph-deploy --username ceph-adm install --cli testvm1
ceph-deploy admin testvm1
sudo chmod 644 /etc/ceph/ceph.client.admin.keyrings

# Далее разделение на Ceph FS и RBD

ceph-deploy --username ceph-adm install --mds testvm2
ceph-deploy --username ceph-adm mds create testvm2

# sudo ceph osd pool create cephfs_data 64s
# sudo ceph osd pool create cephfs_metadata 64
# sudo ceph osd pool application enable cephfs_metadata cephfs
# sudo ceph fs new cephfs cephfs_metadata cephfs_data

# sudo mkdir -p /mnt/cephfs/data_test
# sudo  ceph auth get-or-create client.datatest mon 'allow r' mds 'allow r,allow rw path=/data_test' osd 'allow rw pool=cephfs_data'
# secret_key=$(sudo ceph auth get-key client.datatest)
# echo $secret_key
# mount на другом хосте или вм

create_cephfs(){
    sudo ceph osd pool create cephfs_data 64
    sudo ceph osd pool create cephfs_metadata 64
    sudo ceph fs new cephfs cephfs_metadata cephfs_data
    echo `sudo cat /home/ceph-adm/ceph.client.admin.keyring | grep key | cut -c8-`  > /home/ceph-adm/admin.secret

    # sudo mkdir /mnt/cephfs
    sleep 20
    sudo mount -t ceph testvm2,testvm3,testvm4:/ /mnt -o name=admin,secretfile=/home/ceph-adm/admin.secret
    df -h | grep cephfs
}
create_rbd(){
    sudo ceph osd pool create rbd 128
    sudo rbd pool init rbd
    sudo rbd create testrbd --size 24576 --image-feature layering
    sudo rbd map testrbd --name client.admin

    sudo mkfs.ext4 -m0 /dev/rbd0
    # sudo mkdir /mnt/ceph-device
    sudo mount /dev/rbd0 /mnt
    df -h | grep /mnt

}

if [ "$1" == "cephfs" ]; then
    create_cephfs
elif [ "$1" == "rbd" ]; then
    create_rbd
else
    echo "Неизвестный тип: $1. Допустимые значения: cephfs или rbd"
    exit 1
fi
