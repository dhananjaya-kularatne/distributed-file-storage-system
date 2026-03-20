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

    def start(self):
        """Start the node by launching the listen loop in a background daemon thread."""
        t = threading.Thread(target=self.listen)
        t.daemon = True
        t.start()
        print(f"[{self.node_id}] Node started on {self.host}:{self.port}")

    def listen(self):
        """Create a TCP socket, bind, and accept incoming connections."""
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((self.host, self.port))
        server_sock.listen(5)
        print(f"[{self.node_id}] Listening on {self.host}:{self.port}")

        while True:
            conn, addr = server_sock.accept()
            print(f"[{self.node_id}] Connection from {addr}")
            handler = threading.Thread(target=lambda c: c.close(), args=(conn,))
            handler.daemon = True
            handler.start()

    def connect_to_node(self, target_node_id):
        """Create a TCP socket and attempt to connect to another node."""
        try:
            target_config = NODES[target_node_id]
            host = target_config["host"]
            port = target_config["port"]

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            print(f"[{self.node_id}] Successfully connected to {target_node_id} ({host}:{port})")
            return sock
        except Exception as e:
            print(f"[{self.node_id}] Failed to connect to {target_node_id}: {e}")
            return None


if __name__ == "__main__":
    import sys
    node = TimeNode(sys.argv[1])
    print(f"[{node.node_id}] Node initialized on {node.host}:{node.port}")
    print(f"[{node.node_id}] Storage path: {node.storage_path}")
    print(f"[{node.node_id}] Clock offset: {node.clock_offset}")
    print(f"[{node.node_id}] Lamport clock: {node.lamport_clock}")
    node.start()
    while True:
        time.sleep(1)