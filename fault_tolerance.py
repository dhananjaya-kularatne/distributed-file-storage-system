import socket
import threading
import json
import os
import time
from config import NODES, BUFFER_SIZE, STORAGE_DIR

HEARTBEAT_INTERVAL = 2  # 2 seconds
HEARTBEAT_TIMEOUT = 5   # seconds - time before marking a node as failed

class Node:
    def __init__(self, node_id):
        self.node_id = node_id
        self.host = NODES[node_id]["host"]
        self.port = NODES[node_id]["port"]
        self.peers = {nid: info for nid, info in NODES.items() if nid != node_id}
        self.is_alive = True

        
        # tracking failed nodes
        self.failed_nodes = set()
        self.last_heartbeat = {nid: time.time() + 5 for nid in self.peers}

        # setting up the storage folder for this node
        self.storage_path = os.path.join(STORAGE_DIR, node_id)
        os.makedirs(self.storage_path, exist_ok=True)

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._send_heartbeats, daemon=True).start()
        threading.Thread(target=self._check_failures, daemon=True).start()
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
                    threading.Thread(
                        target=self._handle_connection,
                        args=(conn,),
                        daemon=True
                    ).start()
                except Exception as e:
                    print(f"[{self.node_id}] Error: {e}")

    def _handle_connection(self, conn):
        try:
            data = b""
            while True:
                chunk = conn.recv(BUFFER_SIZE)
                if not chunk:
                    break
                data += chunk
            if data:
                message = json.loads(data.decode())
                print(f"[{self.node_id}] Received: {message}")

                # handles incoming file save request
                if message.get("type") == "save_file":
                    self._save_local(message["filename"], message["data"])
                    conn.sendall(json.dumps({"status": "ok"}).encode())

                elif message.get("type") == "recovery_sync":
                    self._save_local(message["filename"], message["data"])
                    print(f"[{self.node_id}] Restored file from checkpoint: {message['filename']}")
                    conn.sendall(json.dumps({"status": "ok"}).encode())
                
                else:
                    conn.sendall(json.dumps({"status": "ok"}).encode())
                conn.shutdown(socket.SHUT_WR)
            
        except Exception as e:
            print(f"[{self.node_id}] Connection error: {e}")
        finally:
            conn.close()

    # save file locally without replicating
    def _save_local(self, filename, data):
        filepath = os.path.join(self.storage_path, filename)
        with open(filepath, "w") as f:
            f.write(data)
        print(f"[{self.node_id}] Saved file locally: {filename}")

    def send_message(self, target_id, message):
        host = self.peers[target_id]["host"]
        port = self.peers[target_id]["port"]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3)
                s.connect((host, port))
                s.sendall(json.dumps(message).encode())
                s.shutdown(socket.SHUT_WR)
                response = b""
                while True:
                    chunk = s.recv(BUFFER_SIZE)
                    if not chunk:
                        break
                    response += chunk
                return json.loads(response.decode()) if response else None
        except Exception as e:
            print(f"[{self.node_id}] Couldnt reach {target_id}: {e}")
            return None

    # sending heartbeats to peers every 2 seconds
    def _send_heartbeats(self):
        while self.is_alive:
            for peer_id in self.peers:
                response = self.send_message(peer_id, {
                    "type": "heartbeat",
                    "from": self.node_id
                })
                if response:
                    self.last_heartbeat[peer_id] = time.time()  #upates the last heartbeat time

                    # node will be marked as recovered if it was previously failed
                    if peer_id in self.failed_nodes:   
                        print(f"[{self.node_id}] {peer_id} is back online!")
                        self.failed_nodes.discard(peer_id)
                        self._trigger_recovery(peer_id) # trigger recovery when node comes back online

                    print(f"[{self.node_id}] Heartbeat response from {peer_id}: alive")
                else:
                    print(f"[{self.node_id}] No response from {peer_id}")
            time.sleep(HEARTBEAT_INTERVAL)

    def _check_failures(self):
        while self.is_alive:
            for peer_id in self.peers:
                time_since_last = time.time() - self.last_heartbeat[peer_id]
                if time_since_last > HEARTBEAT_TIMEOUT:
                    if peer_id not in self.failed_nodes:
                        self.failed_nodes.add(peer_id)
                        print(f"[{self.node_id}] {peer_id} has FAILED! No heartbeat for {time_since_last:.1f}s")
            time.sleep(1)

    # checkpointing - save file to local storage
    def save_file(self, filename, data):
        filepath = os.path.join(self.storage_path, filename)
        with open(filepath, "w") as f:
            f.write(data)
        print(f"[{self.node_id}] Saved file: {filename}")

        self._replicate_file(filename, data) # this would replicate file to alive nodes

    # load file from local storage
    def load_file(self, filename):
        filepath = os.path.join(self.storage_path, filename)
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                return f.read()
        return None

    # list all files in local storage
    def list_files(self):
        return os.listdir(self.storage_path)


    # replicate file to all alive peer nodes
    def _replicate_file(self, filename, data):
        for peer_id in self.peers:
            if peer_id not in self.failed_nodes:
                response = self.send_message(peer_id, {
                    "type": "save_file",
                    "filename": filename,
                    "data": data
                })
                if response and response.get("status") == "ok":
                    print(f"[{self.node_id}] Replicated {filename} to {peer_id}")
                else:
                    print(f"[{self.node_id}] Failed to replicate {filename} to {peer_id}")

    # checkpointing and rollback recovery
    # push all files to the recovered node from this node's storage
    def _trigger_recovery(self, recovered_node_id):
        print(f"[{self.node_id}] Starting recovery for {recovered_node_id}...")
        for filename in self.list_files():
            data = self.load_file(filename)
            response = self.send_message(recovered_node_id, {
                "type": "recovery_sync",
                "filename": filename,
                "data": data
            })
            if response and response.get("status") == "ok":
                print(f"[{self.node_id}] Recovered {filename} to {recovered_node_id}")
            else:
                print(f"[{self.node_id}] Failed to recover {filename} to {recovered_node_id}")

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