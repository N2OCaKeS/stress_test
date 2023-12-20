#!/bin/bash

set -x


17repo()
{
cat << EOF > /etc/apt/sources.list
deb ftp://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository$1 1.7_x86-64 main contrib non-free
EOF
}

17repo_test()
{
cat << EOF > /etc/apt/sources.list
deb ftp://qa111.devos.astralinux.ru/astra/testing/1.7-testing/base-repository 1.7_x86-64 main contrib non-free
EOF
}

18repo_test()
{
cat << EOF > /etc/apt/sources.list
deb ftp://qa111.devos.astralinux.ru/astra/testing/1.8-testing/installation 1.8_x86-64 main contrib non-free
deb ftp://qa111.devos.astralinux.ru/astra/testing/1.8-testing/devel 1.8_x86-64 main non-free contrib
EOF
}


test "$(grep 1.7.0 /etc/astra_version)" && 17repo
test "$(grep 1.7.1 /etc/astra_version)" && 17repo "-1"
test "$(grep 1.7.2 /etc/astra_version)" && 17repo "-2"
test "$(grep 1.7.3 /etc/astra_version)" && 17repo "-3"
test "$(grep 1.7.4 /etc/astra_version)" && 17repo "-4"
test "$(grep 1.7.5 /etc/astra_version)" && 17repo "-5"
test "$(grep 1.7.6 /etc/astra_version)" && 17repo_test
test "$(grep 1.8.0 /etc/astra_version)" && 18repo_test

sudo apt-get update

