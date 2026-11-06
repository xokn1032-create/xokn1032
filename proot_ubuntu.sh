curl -LO https://proot.gitlab.io/proot/bin/proot
chmod +x ./proot
./proot --version

wget https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-amd64.tar.gz

mkdir ubuntu-rootfs
tar -xzf ubuntu-base-24.04.4-base-amd64.tar.gz -C ubuntu-rootfs
cp /etc/resolv.conf ./ubuntu-rootfs/etc/resolv.conf

apt update && apt install -y curl wget build-essential bash npm gh 
curl -L https://foundry.paradigm.xyz | bash

export PATH="$PATH:/home/cosmicbowl4/.foundry/bin"
foundryup

cd /tmp
gh repo clone ssvlabs/ssv-network

cd ssv-network

# Install both libraries
forge install OpenZeppelin/openzeppelin-contracts@v4.9.3 --no-commit
forge install OpenZeppelin/openzeppelin-contracts-upgradeable@v4.9.3 --no-commit

# Set up comprehensive remappings for both absolute and node_modules paths
cat << 'EOF' > remappings.txt
@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/
@openzeppelin/contracts-upgradeable/=lib/openzeppelin-contracts-upgradeable/contracts/
node_modules/@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/
node_modules/@openzeppelin/contracts-upgradeable/=lib/openzeppelin-contracts-upgradeable/contracts/
EOF
