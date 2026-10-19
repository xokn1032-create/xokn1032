# Copy host DNS settings into the Alpine rootfs
cp /etc/resolv.conf ./alpine-rootfs/etc/resolv.conf

./proot -b /dev -r ./alpine-rootfs -w /root /bin/sh
