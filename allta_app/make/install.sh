#!/bin/bash

sudo apt install nginx -y
sudo apt install vsftpd -y

sudo apt install drbl
sudo /opt/drbl/sbin/drblsrv -i
sudo /opt/drbl/sbin/drblpush -i


