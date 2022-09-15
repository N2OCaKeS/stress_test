#!/bin/bash
set -x
ips=(10.0.0.25 10.0.0.23 10.0.0.21)

com_scp() {
com="
set timeout -1
spawn $1
expect {
    \"Are you sure*\" {send -- \"yes\r\"}
        \"*100%*\"
}
expect eof
"
        expect -c "$com"
}

#Generate ssh-keys
gen_key() {
com="
set timout -1
spawn $1
expect \"Enter file in which to save the key*\"
send \"\r\"
expect \"Enter passphrase*\"
send \"\r\"
expect \"Enter same passphrase again*\"
send \"\r\"
expect \"The key's randomart image is*\"
expect eof
"
expect -c "$com"
sleep 1
}

gen_key ssh-keygen

#Send ssh-keys u
for host in ${ips[*]}; do
com_ssh="
set timeout -1
spawn ssh-copy-id $host
expect {
    \"Are you sure*\" {send -- \"yes\r\"}
        \"*password*\" {send -- \"1\r\"}
}
expect {
    \"Are you sure*\" {send -- \"yes\r\"}
        \"*password*\" {send -- \"1\r\"}
}
expect eof
"
        expect -c "$com_ssh" > /dev/null 2>&1
done

#root keys
gen_key "sudo ssh-keygen"
for host in ${ips[*]}; do
	ssh $host "sudo mkdir -m 700 /root/.ssh 2> /dev/null"
done

KEY=$(sudo cat /root/.ssh/id_rsa.pub)
for host in ${ips[*]}; do
	ssh $host "sudo bash -c \"echo $KEY >> /root/.ssh/authorized_keys\""
done

#check root keys
for host in ${ips[*]}; do
	com_scp "sudo ssh $host 'hostname'"
done