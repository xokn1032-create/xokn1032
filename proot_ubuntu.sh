curl -LO https://proot.gitlab.io/proot/bin/proot
chmod +x ./proot
./proot --version

wget https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-amd64.tar.gz

mkdir ubuntu-rootfs
tar -xzf ubuntu-base-24.04.4-base-amd64.tar.gz -C ubuntu-rootfs
cp /etc/resolv.conf ./ubuntu-rootfs/etc/resolv.conf
