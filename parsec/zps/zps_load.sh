
max=512
prog=/usr/bin/ssh
#prog=ssh.unsigned

x=0
while [ $x -lt $max ] ; do
	x=$(($x + 1))
	echo $x

	#cp $prog /home/x.$x
	ln $prog /home/x.$x
done

x=0
while [ $x -lt $max ] ; do
	x=$(($x + 1))
	/home/x.$x &
done
wait

x=0
while [ $x -lt $max ] ; do
	x=$(($x + 1))
	rm -f /home/x.$x
done