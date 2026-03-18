import socket
import threading
import json
import os
import time
from config import NODES, BUFFER_SIZE, STORAGE_DIR

class TimeNode:
    def __init__(self, node_id):
        self.node_id = node_id
        self.host = NODES[node_id]["host"]
        self.port = NODES[node_id]["port"]
        self.is_alive = True

        # clock offset from time server
        self.clock_offset = 0

        # lamport clock counter
        self.lamport_clock = 0

        # storage
        self.storage_path = os.path.join(STORAGE_DIR, node_id)
        os.makedirs(self.storage_path, exist_ok=True)

if __name__ == "__main__":
    import sys
    node = TimeNode(sys.argv[1])
    print(f"[{node.node_id}] Node initialized on {node.host}:{node.port}")
    print(f"[{node.node_id}] Storage path: {node.storage_path}")
    print(f"[{node.node_id}] Clock offset: {node.clock_offset}")
    print(f"[{node.node_id}] Lamport clock: {node.lamport_clock}")