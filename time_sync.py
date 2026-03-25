"""
time_sync.py
This module implements a TimeNode class for managing distributed clock synchronization.
It provides features for synchronizing with a time server using Cristian's algorithm,
as well as a fallback mechanism using Lamport logical clocks for ordering events.
"""

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

            # Calculate the round-trip time (RTT) and estimate the server time
            # based on Cristian's algorithm (server time + half of RTT)
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

    def start_periodic_sync(self, target_node_id, interval=10):
        """Run sync_with_server in a background daemon thread on a fixed interval."""
        def _sync_loop():
            while True:
                self.sync_with_server(target_node_id)
                time.sleep(interval)

        t = threading.Thread(target=_sync_loop)
        t.daemon = True
        t.start()
        print(f"[{self.node_id}] Periodic sync started with {target_node_id} every {interval}s")

    def get_adjusted_time(self):
        """Return local time adjusted by the stored clock offset."""
        adjusted = time.time() + self.clock_offset
        print(f"[{self.node_id}] Adjusted time: {adjusted:.4f}")
        return adjusted

    def increment_lamport(self):
        """Increment the Lamport logical clock by 1."""
        self.lamport_clock += 1
        print(f"[{self.node_id}] Lamport clock incremented to {self.lamport_clock}")
        return self.lamport_clock

    def update_lamport(self, received_time):
        """Update the Lamport clock based on a received timestamp."""
        # The new Lamport clock value is the maximum of the local clock
        # and the received clock, plus 1 to ensure strict ordering
        self.lamport_clock = max(self.lamport_clock, received_time) + 1
        print(f"[{self.node_id}] Lamport clock updated to {self.lamport_clock}")
        return self.lamport_clock

    def sync_with_fallback(self, target_node_id):
        """Attempt to synchronize with a server, falling back to Lamport clock if it fails."""
        try:
            result = self.sync_with_server(target_node_id)
            if result is None:
                self.increment_lamport()
                print(f"[{self.node_id}] Time sync failed, falling back to Lamport clock.")
            else:
                print(f"[{self.node_id}] Sync succeeded with {target_node_id}.")
        except Exception as e:
            print(f"[{self.node_id}] Error in sync_with_fallback: {e}")

    def simulate_clock_skew(self, skew_seconds):
        """Artificially add skew to the clock offset for testing purposes."""
        self.clock_offset += skew_seconds
        print(f"[{self.node_id}] Applied clock skew: {skew_seconds}s. New clock offset: {self.clock_offset}s")
        return self.clock_offset


if __name__ == "__main__":
    import sys
    node = TimeNode(sys.argv[1])
    print(f"[{node.node_id}] Node initialized on {node.host}:{node.port}")
    print(f"[{node.node_id}] Storage path: {node.storage_path}")
    print(f"[{node.node_id}] Clock offset: {node.clock_offset}")
    print(f"[{node.node_id}] Lamport clock: {node.lamport_clock}")
    node.start()
    node.simulate_clock_skew(0.5)
    node.start_periodic_sync("node2")
    while True:
        time.sleep(1)