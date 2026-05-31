#!/usr/bin/env bash
# Add 2 GB swap to EC2 t3.medium (4 GB RAM).
# Prevents OOM kills during Docker image builds and ML training.
# Safe to run more than once — skips if swap already exists.
set -euo pipefail

SWAP_FILE=/swapfile
SWAP_SIZE=2G

if swapon --show | grep -q "$SWAP_FILE"; then
  echo "Swap already active at $SWAP_FILE — nothing to do."
  exit 0
fi

echo "→ Allocating $SWAP_SIZE swap file at $SWAP_FILE …"
sudo fallocate -l $SWAP_SIZE $SWAP_FILE
sudo chmod 600 $SWAP_FILE
sudo mkswap $SWAP_FILE
sudo swapon $SWAP_FILE

echo "→ Making swap permanent across reboots …"
if ! grep -q "$SWAP_FILE" /etc/fstab; then
  echo "$SWAP_FILE none swap sw 0 0" | sudo tee -a /etc/fstab
fi

# Reduce swappiness — only use swap under real memory pressure
echo "→ Setting vm.swappiness=10 …"
sudo sysctl vm.swappiness=10
if ! grep -q "vm.swappiness" /etc/sysctl.conf; then
  echo "vm.swappiness=10" | sudo tee -a /etc/sysctl.conf
fi

echo ""
echo "Done. Current swap:"
swapon --show
free -h
