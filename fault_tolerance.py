import socket
import threading
import json
import os
import time
from config import NODES, BUFFER_SIZE, STORAGE_DIR

class Node:
    def __init__(self, node_id):
        self.node_id = node_id
        self.host = NODES[node_id]["host"]
        self.port = NODES[node_id]["port"]
        self.is_alive = True
import socket
import threading
import json
import os
import time
from config import NODES, BUFFER_SIZE, STORAGE_DIR

class Node:
    def __init__(self, node_id):
        self.node_id = node_id
        self.host = NODES[node_id]["host"]
        self.port = NODES[node_id]["port"]
        self.is_alive = True

        # setting up the storage folder for this node
        self.storage_path = os.path.join(STORAGE_DIR, node_id)
        os.makedirs(self.storage_path, exist_ok=True)

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()
        print(f"[{self.node_id}] Started on {self.host}:{self.port}")

    def _listen(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(5)
            print(f"[{self.node_id}] Listening for connections...")
            while self.is_alive:
                try:
                    conn, addr = s.accept()
                    print(f"[{self.node_id}] Connection from {addr}")
                except Exception as e:
                    print(f"[{self.node_id}] Error: {e}")

if __name__ == "__main__":
    import sys
    node = Node(sys.argv[1])
    node.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        node.is_alive = False
        print(f"[{node.node_id}] Shutting down.")
        # setting up the storage folder for this node
        self.storage_path = os.path.join(STORAGE_DIR, node_id)
        os.makedirs(self.storage_path, exist_ok=True)

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()
        print(f"[{self.node_id}] Started on {self.host}:{self.port}")

    def _listen(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(5)
            print(f"[{self.node_id}] Listening for connections...")
            while self.is_alive:
                try:
                    conn, addr = s.accept()
                    print(f"[{self.node_id}] Connection from {addr}")
                except Exception as e:
                    print(f"[{self.node_id}] Error: {e}")

if __name__ == "__main__":
    import sys
    node = Node(sys.argv[1])
    node.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        node.is_alive = False
        print(f"[{node.node_id}] Shutting down.")