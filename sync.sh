#!/bin/bash

REMOTE="krihl4n@192.168.100.24"
REMOTE_DIR="/home/krihl4n/tanks_monitoring"
LOCAL_DIR="."

case "$1" in
  pull)
    scp -r "$REMOTE:$REMOTE_DIR/"* "$LOCAL_DIR"
    ;;
  push)
    rsync -av -e ssh \
      --exclude='sync.sh' \
      --exclude='planned_pumpout.json' \
      --exclude='*.csv' \
      --exclude='__pycache__' \
      --exclude='tests' \
      "$LOCAL_DIR/" "$REMOTE:$REMOTE_DIR/"
    ;;
  *)
    echo "Użycie: $0 {pull|push}"
    echo "  pull - kopiuje ze zdalnej lokalizacji do lokalnej"
    echo "  push - kopiuje z lokalnej do zdalnej (bez sync.sh)"
    exit 1
    ;;
esac
