curl -LO https://proot.gitlab.io/proot/bin/proot
chmod +x ./proot
./proot --version

# 1. Download a minimal Alpine Linux root filesystem
wget https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/alpine-minirootfs-3.24.2-x86_64.tar.gz

# 2. Create a folder and extract it
mkdir alpine-rootfs
tar -xzf alpine-minirootfs-3.24.2-x86_64.tar.gz -C alpine-rootfs

# 3. Run PRoot to enter your isolated Linux environment!
./proot -r ./alpine-rootfs -w /root /bin/sh

# Copy host DNS settings into the Alpine rootfs
cp /etc/resolv.conf ./alpine-rootfs/etc/resolv.conf

./proot -b /dev -r ./alpine-rootfs -w /root /bin/sh
