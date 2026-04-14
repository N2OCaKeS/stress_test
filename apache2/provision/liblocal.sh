#!/bin/bash
# Functions for local user, levels, and categories handling.

check_local_level() {
    local db="/etc/parsec/mac_levels"
    egrep "^.*:$1" ${db} &> /dev/null
    return $?
}

create_local_level() {
    local result=0
    if ! check_local_level $1; then
        userlev -a ${1} "Level_${1}"
        result=$?
    fi
    return $result
}

check_local_category() {
    local db="/etc/parsec/mac_categories"
    egrep "^.*:$1$" ${db} &> /dev/null
    return $?
}

create_local_category() {
    local result=0
    if ! check_local_category ${1}; then
        usercat -a ${1} "Category_${1}"
        result=$?
    fi
    return $result
}

create_local_user() {
    useradd -m ${1} -G audio -s /bin/bash
    added=$?
    echo "${1}:1" | chpasswd &> /dev/null
    passwd_given=$?
    if [[ $added == 0 && $passwd_given == 0 ]]; then
        result=0
    else
        result=1
    fi
    return $result
}

install_pack() {
	for package in "${@:1}"; do
		pack=$(dpkg -l | grep " $package ")
		if [[ -n $pack ]]; then
			printf "${green} $package is previously installed.\n${coff}"
		else
			apt -y install $package
			pack=$(dpkg -l | grep $package)
			if [[ -n $pack ]]; then
				printf "${green} $package is installed now.\n${coff}"
			else
				printf "${red}Some problems installing the package $package.\n${coff}"
				exit 1
			fi
		fi
	done
}