#!/bin/bash
# ALD functions.

ald_passwd="/tmp/ald_passwd"
opts="--force --pass-file=$ald_passwd"
version=$(grep -o '[0-9]\.[0-9]' /etc/astra_version)

create_ald_passwd() {
    printf "admin/admin:1\n" > $ald_passwd
    if [[ -n $1 ]]; then
        printf "${1}\n" >> $ald_passwd
    fi
    chmod 0600 $ald_passwd
    return $?
}

clear_ald_passwd() {
    rm $ald_passwd
    return $?
}

simplify_policy() {
    create_ald_passwd
    ald-admin policy-mod default --min-classes=1 $opts
    local classes=$?
    ald-admin policy-mod default --min-length=1 $opts
    local length=$?
    if [[ $classes == 0 && $length == 0 ]]; then
        result=0
    else
        result=1
    fi
    clear_ald_passwd
    return $result
}

create_ald_user() {
    simplify_policy
    create_ald_passwd ${1}:1
    created=1
    if [[ -n $1 ]]; then
        ald-admin user-add $1 $opts
	    grep orel /etc/astra_version &> /dev/null
        if [[ $? != 0 ]]; then
        	ald-admin user-mac $1 --max-lev-int=0 $opts
	    fi
	created=0
    fi
    clear_ald_passwd
    return $created
}

allow_hosts() {
    create_ald_passwd
    user=$1
    shift
    host_list=""
    while [[ -n $1 ]]; do
        host_list="${host_list} --host=${1}"
        shift
    done
    ald-admin user-ald-cap $user --add-hosts $host_list $opts
    result=$?
    clear_ald_passwd
    return $result
}

ald_set_max_level_to_user() {
    create_ald_passwd
    ald-admin user-mac $1 --max-lev-int=$2
    result=$?
    clear_ald_passwd
    return $result
}

ald_check_level() {
    create_ald_passwd
    ald-admin maclev-list | egrep "^${1}:.*" &> /dev/null
    result=$? 
    clear_ald_passwd
    return $result
}

check_category_format() {
    result=0
    if [[ ${1::0} != 0x ]]; then
       result=1 
    fi        
    return $result
}

fix_category_format() {
    category=$1
    if ! check_category_format ${category}; then
        fixed_category="0x$category"
    else
        fixed_category=$category
    fi
}

ald_check_category() {
    create_ald_passwd
    fix_category_format $1 
    ald-admin maccat-list | egrep "^${fixed_category}:.*" &> /dev/null
    result=$?
    clear_ald_passwd
    return $result
}

create_ald_level() {
    result=0
    if ! ald_check_level ${1}; then
        create_ald_passwd
        ald-admin maclev-add $1 "Level_${1}" $opts
        result=$?
        clear_ald_passwd
    fi
    return $result
}

create_ald_category() {
    result=0
    if ! ald_check_category ${1}; then
        create_ald_passwd
        ald-admin maccat-add $fixed_category "Category_${1}" $opts
        result=$?
        clear_ald_passwd
    fi
    return $result
}