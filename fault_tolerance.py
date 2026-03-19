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
        self.peers = {nid: info for nid, info in NODES.items() if nid != node_id}
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
                conn.sendall(json.dumps({"status": "ok"}).encode())
                conn.shutdown(socket.SHUT_WR)
        except Exception as e:
            print(f"[{self.node_id}] Connection error: {e}")
        finally:
            conn.close()

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

if __name__ == "__main__":
    import sys
    node = Node(sys.argv[1])
    node.start()
    time.sleep(2)

    # test sending a message from node1 to node2
    if node.node_id == "node1":
        response = node.send_message("node2", {"type": "hello", "from": "node1"})
        print(f"[node1] Response from node2: {response}")
        
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        node.is_alive = False
        print(f"[{node.node_id}] Shutting down.")