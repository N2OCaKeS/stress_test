sudo ceph osd pool create cephfs_data 64
sudo ceph osd pool create cephfs_metadata 64
sudo ceph fs new cephfs cephfs_metadata cephfs_data
echo `cat /etc/ceph/ceph.client.admin.keyring | grep key | cut -c8-`  > /home/ceph-adm/admin.secret
# TODO !!!!!!!!!!!!!!!!!!
# sshpass -p 1 scp /home/ceph-adm/admin.secret testvm$i:/home/u/
sudo mkdir /mnt/cephfs
sleep 5
# TODO !!!!!!!!!!!!!!!!!!
sudo mount -t ceph 10.0.5.21,10.0.5.23,10.0.5.22:/ /mnt/cephfs -o name=admin,secretfile=/home/ceph-adm/admin.secret
df -h | grep cephfs