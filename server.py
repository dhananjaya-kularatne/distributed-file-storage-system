"""
server.py
Integrated node combining all 4 modules:
- fault_tolerance.py (Node) -> heartbeat, failure detection, checkpointing & rollback recovery
- replication.py (Replicator) -> file replication strategy, conflict handling
- time_sync.py (TimeNode) -> Cristian's algorithm, Lamport clocks
- consensus.py (Node as RaftNode) -> Raft leader election, log replication
"""

import socket
import threading
import json
import os
import time
import base64
import struct
from fault_tolerance import Node
from replication import Replicator
from time_sync import TimeNode
from consensus import Node as RaftNode
from config import NODES, BUFFER_SIZE, STORAGE_DIR


def send_msg(conn, response):
    """Send JSON response with 4-byte length prefix for large file support."""
    data = json.dumps(response).encode()
    conn.sendall(struct.pack(">I", len(data)) + data)


def recv_msg(sock):
    """Receive a length-prefixed JSON response."""
    header = _recv_exact(sock, 4)
    if not header:
        return None
    total_len = struct.unpack(">I", header)[0]
    raw = _recv_exact(sock, total_len)
    if not raw:
        return None
    return json.loads(raw.decode())


def _recv_exact(sock, n):
    """Read exactly n bytes from a socket."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(min(BUFFER_SIZE, n - len(buf)))
        if not chunk:
            return None
        buf += chunk
    return buf


class IntegratedNode(Node):
    def __init__(self, node_id):
        # fault tolerance - heartbeat, failure detection, recovery
        super().__init__(node_id)

        # replication - file replication strategy and conflict handling
        self.replicator = Replicator(node_id)

        # time sync - Cristian's algorithm + Lamport clocks
        self.time_node = TimeNode(node_id)

        # consensus - Raft leader election + log replication
        self.raft = RaftNode(node_id)

        print(f"[{self.node_id}] IntegratedNode initialized")

    def start(self):
        """Start all 4 components."""
        # fault tolerance threads (heartbeat, failure detection, listen)
        super().start()

        # time sync - periodic sync with first peer as time server
        peers = list(self.peers.keys())
        if peers:
            time_server = peers[0]
            self.time_node.start_periodic_sync(time_server, interval=10)
            print(f"[{self.node_id}] Time sync started with {time_server}")

        # consensus - Raft election timer + heartbeat threads
        self.raft.start()
        print(f"[{self.node_id}] Consensus (Raft) started")

        print(f"[{self.node_id}] All components started")

    def _handle_connection(self, conn):
        """
        Route incoming messages to the correct handler:
        - client_upload: client uploads a file
        - client_download: client downloads a file
        - raft_status: get current Raft status
        - replicate_file: replication from Member 2 (Replicator)
        - save_file, recovery_sync: fault tolerance from Member 1 (Node)
        - heartbeat: handled by parent Node
        """
        try:
            data = b""
            while True:
                chunk = conn.recv(BUFFER_SIZE)
                if not chunk:
                    break
                data += chunk

            if data:
                message = json.loads(data.decode())
                msg_type = message.get("type")
                print(f"[{self.node_id}] Received: {msg_type}")

                # ── Client Upload ──
                if msg_type == "client_upload":
                    response = self._handle_client_upload(message)
                    conn.sendall(json.dumps(response).encode())

                # ── Client Download — uses length-prefix for large files ──
                elif msg_type == "client_download":
                    response = self._handle_client_download(message)
                    send_msg(conn, response)

                # ── Raft Status ──
                elif msg_type == "raft_status":
                    response = self.raft.get_status()
                    conn.sendall(json.dumps(response).encode())

                # ── Replication from leader (Member 2) ──
                elif msg_type == "replicate_file":
                    self.replicator.handle_replication_request(message)
                    # update Lamport clock on receiving replicated file
                    lamport = message.get("lamport_clock", 0)
                    self.time_node.update_lamport(lamport)
                    conn.sendall(json.dumps({"status": "ok"}).encode())

                # ── Fault Tolerance (Member 1) ──
                elif msg_type == "save_file":
                    self._save_local(
                        message["filename"],
                        message["data"],
                        message.get("is_binary", False)
                    )
                    conn.sendall(json.dumps({"status": "ok"}).encode())

                elif msg_type == "recovery_sync":
                    self._save_local(
                        message["filename"],
                        message["data"],
                        message.get("is_binary", False)
                    )
                    print(f"[{self.node_id}] Restored file from checkpoint: {message['filename']}")
                    conn.sendall(json.dumps({"status": "ok"}).encode())

                # ── Heartbeat and others ──
                else:
                    conn.sendall(json.dumps({"status": "ok"}).encode())

                conn.shutdown(socket.SHUT_WR)

        except Exception as e:
            print(f"[{self.node_id}] Connection error: {e}")
        finally:
            conn.close()

    def _handle_client_upload(self, message):
        """
        Handle file upload from a client.

        Integration flow:
        1. Check if this node is the Raft leader (consensus - Member 4)
           - If not leader, forward to the known leader
        2. Increment Lamport clock (time sync - Member 3)
        3. Append command to Raft log and replicate to majority (consensus - Member 4)
        4. Save file locally with checkpointing (fault tolerance - Member 1)
        5. Replicate file to all alive peers (replication - Member 2)
        """
        filename = message.get("filename")
        data = message.get("data")
        is_binary = message.get("is_binary", False)

        if not filename or data is None:
            return {"status": "error", "message": "Missing filename or data"}

        # if not leader, forward to the known leader
        if not self.raft.is_leader():
            leader_id = self.raft.get_leader_id()
            if leader_id and leader_id != self.node_id:
                print(f"[{self.node_id}] Not leader, forwarding upload to {leader_id}")
                response = self.send_message(leader_id, message)
                if response:
                    return response
            # no leader known yet — still handle it locally
            print(f"[{self.node_id}] No leader yet, handling upload locally")

        # increment Lamport clock before write operation (time sync - Member 3)
        lamport = self.time_node.increment_lamport()

        # append command to Raft log and replicate to majority (consensus - Member 4)
        committed = self.raft.append_command({
            "action": "upload",
            "filename": filename,
            "lamport": lamport
        })
        if not committed:
            print(f"[{self.node_id}] Raft commit failed, proceeding anyway")

        # save file locally using fault tolerance checkpointing (Member 1)
        self.save_file(filename, data, is_binary)

        # replicate to followers using replication strategy (Member 2)
        file_data = base64.b64decode(data) if is_binary else data
        self.replicator.replicate_to_followers(filename, file_data, lamport)

        print(f"[{self.node_id}] File '{filename}' uploaded and replicated (Lamport: {lamport})")
        return {
            "status": "ok",
            "message": f"File '{filename}' uploaded successfully",
            "lamport_clock": lamport,
            "raft_leader": self.raft.get_leader_id(),
            "committed": committed
        }

    def _handle_client_download(self, message):
        """
        Handle file download request from a client.

        Integration flow:
        1. Load file from local storage (fault tolerance - Member 1)
        2. If not found, search other alive nodes
        3. Get adjusted timestamp (time sync - Member 3)

        Response uses length-prefix protocol to support large files.
        """
        filename = message.get("filename")

        if not filename:
            return {"status": "error", "message": "Missing filename"}

        data, is_binary = self.load_file(filename)

        if data is None:
            # try to find it on another alive node
            for peer_id in self.peers:
                if peer_id not in self.failed_nodes:
                    try:
                        host = self.peers[peer_id]["host"]
                        port = self.peers[peer_id]["port"]
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.settimeout(10)
                            s.connect((host, port))
                            s.sendall(json.dumps({
                                "type": "client_download",
                                "filename": filename
                            }).encode())
                            s.shutdown(socket.SHUT_WR)
                            response = recv_msg(s)
                            if response and response.get("status") == "ok":
                                return response
                    except Exception as e:
                        print(f"[{self.node_id}] Could not reach {peer_id}: {e}")

            return {"status": "error", "message": f"File '{filename}' not found"}

        # get adjusted time for response timestamp (time sync - Member 3)
        timestamp = self.time_node.get_adjusted_time()

        print(f"[{self.node_id}] File '{filename}' downloaded (timestamp: {timestamp:.4f})")
        return {
            "status": "ok",
            "filename": filename,
            "data": data,
            "is_binary": is_binary,
            "timestamp": timestamp,
            "raft_leader": self.raft.get_leader_id()
        }