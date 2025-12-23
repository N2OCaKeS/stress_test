#!/bin/bash

: ${natiface=$(ip route show to 0/0 |
sed -n '/^default/{s/.* dev \([^ ]*\).*/\1/p;q}')}

attach()
{
    name=${1-vpn}
    if [ $UID -ne 0 ]; then
        exec tmux -L $name attach
    else
        exec ip netns exec $name su -c "exec tmux -L $name new -A" $SUDO_USER
    fi
}

start()
{
    name=${1-vpn}
    addrbase=${2-192.168.99}
    detach=${3-}
    
    ip link add $name.1 type veth peer name $name.2
    ip addr add $addrbase.1 peer $addrbase.2 dev $name.1
    ip link set $name.1 up
    
    iptables -t nat -A POSTROUTING -s $addrbase.2 -o $natiface -j MASQUERADE
    
    mkdir -p /etc/netns/$name
    sed /127.0.0.1/d </etc/resolv.conf >/etc/netns/$name/resolv.conf
    
    ip netns add $name
    ip link set $name.2 netns $name
    
    ip netns exec $name bash -c "
    ip addr add $addrbase.2 peer $addrbase.1 dev $name.2
    ip link set $name.2 up
    ip route add default via $addrbase.1
    ip link set lo up"
    
    if [[ "$detach" != "--no-tmux" && "$detach" != "--detach" ]]; then
        attach "$name"
    fi
}

stop() {
    name=${1:-vpn}
    ns_iface="$name.1"
    addrbase=$(ip addr show "$ns_iface" |
    sed -n 's/.*inet \([0-9]*\.[0-9]*\.[0-9]*\).*/\1/p')
    pids=$(ip netns pids "$name")
    
    if [ -n "$pids" ]; then
        echo "Killing processes in namespace '$name': $pids"
        kill $pids
        sleep 1
        # проверим, остались ли
        still_running=$(ps -o pid= -p $pids | xargs)
        if [ -n "$still_running" ]; then
            echo "Force killing: $still_running"
            kill -9 $still_running
        fi
    fi
    
    # Удалим интерфейс
    if ip link show "$ns_iface" &>/dev/null; then
        ip link del "$ns_iface"
    fi
    
    # Удалим правило NAT
    if [[ -n "$addrbase" && -n "$natiface" ]]; then
        iptables -t nat -D POSTROUTING -s "$addrbase.2" -o "$natiface" -j MASQUERADE 2>/dev/null
    fi
    
    # Удалим namespace
    if ip netns list | grep -q "^$name"; then
        ip netns del "$name"
        echo "Namespace '$name' and related resources successfully deleted."
    fi
}

stop_all() {
    for ns in $(ip netns list | awk '{print $1}' | grep '^vpn'); do
        echo "Processing namespace: $ns"
        
        pids=$(ip netns pids "$ns")
        if [ -n "$pids" ]; then
            echo "Namespace $ns still in use by:"
            ps $pids
            echo "Killing processes: $pids"
            echo "$pids" | xargs -r kill -9
        fi
        
        addrbase=$(ip addr show "$ns.1" 2>/dev/null |
        sed -n 's/.*inet \([0-9]*\.[0-9]*\.[0-9]*\)\..*/\1/p')
        
        ip link del "$ns.1" 2>/dev/null || true
        ip netns del "$ns" 2>/dev/null || true
        
        if [ -n "$addrbase" ]; then
            iptables -t nat -D POSTROUTING -s "$addrbase.2" -j MASQUERADE 2>/dev/null || true
        fi
        
        echo "Namespace '$ns' and related resources deleted."
    done
}


command="$1"
shift

case "$command" in
    start)
        start "$@"
    ;;
    stop)
        stop "$@"
    ;;
    stop_all)
        stop_all
    ;;
    attach)
        attach "$@"
    ;;
    *)
        echo "usage: $0 {start | stop | attach} [vpn-name] [vpn-addr-base] [--no-tmux]"
    ;;
esac


