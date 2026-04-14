#!/bin/bash
# Prepare server for Apache2 tests.

mylib="apache_prepare.sh"

if [[ $(basename $PWD) != testlink ]]; then
    printf "Executed from wrong directory." >& 2
    exit 1
fi

if [[ -e ./${mylib} ]]; then
    source $mylib
else
    printf "$mylib not found!\n" >& 2
    exit 1
fi

case $1 in
    "pam")
        pam
        ;;
    "kerb")
        kerberos
        ;;
    "ipa16")
        ipa16
        ;;
    "ipa18")
        ipa18
        ;;
    "bm")
        bm
        ;;

    *) usage >& 2; exit 1
esac

exit 0