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
            handler = threading.Thread(target=self.handle_time_request, args=(conn,))
            handler.daemon = True
            handler.start()

    def handle_time_request(self, conn):
        """Send current node time as JSON and close the connection."""
        try:
            current_time = time.time()
            response = json.dumps({"timestamp": current_time})
            conn.sendall(response.encode('utf-8'))
        except Exception as e:
            print(f"[{self.node_id}] Error handling time request: {e}")
        finally:
            conn.close()

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

    def sync_with_server(self, target_node_id):
        """Synchronize node clock using Cristian's algorithm."""
        try:
            send_time = time.time()
            sock = self.connect_to_node(target_node_id)
            if sock is None:
                return None

            sock.sendall(b"time_request")
            data = sock.recv(BUFFER_SIZE)
            receive_time = time.time()
            sock.close()

            if not data:
                return None

            response = json.loads(data.decode('utf-8'))
            server_timestamp = response["timestamp"]

            rtt = receive_time - send_time
            estimated_server_time = server_timestamp + (rtt / 2)
            current_local_time = time.time()
            self.clock_offset = estimated_server_time - current_local_time

            print(f"[{self.node_id}] Synced with {target_node_id}")
            print(f"[{self.node_id}] RTT: {rtt:.4f}s")
            print(f"[{self.node_id}] Estimated server time: {estimated_server_time:.4f}")
            print(f"[{self.node_id}] Clock offset: {self.clock_offset:.4f}s")

            return self.clock_offset
        except Exception as e:
            print(f"[{self.node_id}] Failed to sync with {target_node_id}: {e}")
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