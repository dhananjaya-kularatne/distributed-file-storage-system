"""
client.py
Client for the distributed file storage system.
Connects to any node and uploads or downloads files.
Supports text files, images, PDFs and any binary files.
"""

import socket
import json
import sys
import os
import base64
import struct
from config import NODES, BUFFER_SIZE


def recv_msg(sock):
    """Receive a length-prefixed JSON response.
    Reads the 4-byte header first to know total size,
    then reads exactly that many bytes — works for any file size.
    """
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


class Client:
    def __init__(self):
        self.nodes = NODES

    def _send_request(self, node_id, message):
        """Send a request and receive a plain JSON response (for uploads)."""
        host = self.nodes[node_id]["host"]
        port = self.nodes[node_id]["port"]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(30)
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
            print(f"[Client] Failed to connect to {node_id}: {e}")
            return None

    def _send_download_request(self, node_id, message):
        """Send a download request and receive a length-prefixed response.
        Uses length-prefix protocol to handle large files correctly.
        """
        host = self.nodes[node_id]["host"]
        port = self.nodes[node_id]["port"]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(30)
                s.connect((host, port))
                s.sendall(json.dumps(message).encode())
                s.shutdown(socket.SHUT_WR)
                return recv_msg(s)
        except Exception as e:
            print(f"[Client] Failed to connect to {node_id}: {e}")
            return None

    def upload(self, filename, data=None, node_id="node1", filepath=None):
        """
        Upload a file to the distributed system.
        - filepath: path to a file on disk (supports text, images, PDFs)
        - data: plain text string to upload directly
        Tries all nodes if the first one is unavailable.
        """
        is_binary = False

        if filepath:
            # read file from disk
            filename = os.path.basename(filepath) if filename == filepath else filename
            try:
                with open(filepath, "rb") as f:
                    raw = f.read()
                try:
                    data = raw.decode("utf-8")
                    is_binary = False
                except UnicodeDecodeError:
                    # binary file - encode as base64
                    data = base64.b64encode(raw).decode("utf-8")
                    is_binary = True
            except Exception as e:
                print(f"[Client] Failed to read file: {e}")
                return None

        message = {
            "type": "client_upload",
            "filename": filename,
            "data": data,
            "is_binary": is_binary
        }

        # try the given node first
        response = self._send_request(node_id, message)

        # handle redirect if node is not the leader
        if response and response.get("status") == "error" and "leader_id" in response:
            leader_id = response.get("leader_id")
            if leader_id and leader_id in self.nodes:
                print(f"[Client] Redirecting to leader {leader_id}...")
                response = self._send_request(leader_id, message)

        if response and response.get("status") == "ok":
            print(f"[Client] Upload successful: {response.get('message')}")
            print(f"[Client] Lamport clock at upload: {response.get('lamport_clock')}")
            print(f"[Client] Raft leader: {response.get('raft_leader')}")
            print(f"[Client] Committed: {response.get('committed')}")
            return response

        # if that fails, try other nodes
        print(f"[Client] {node_id} unavailable, trying other nodes...")
        for nid in self.nodes:
            if nid != node_id:
                response = self._send_request(nid, message)
                if response and response.get("status") == "ok":
                    print(f"[Client] Upload successful via {nid}: {response.get('message')}")
                    return response

        print("[Client] Upload failed - all nodes unreachable")
        return None

    def download(self, filename, node_id="node1", save_to=None):
        """
        Download a file from the distributed system.
        - save_to: path to save the downloaded file on disk
        Uses length-prefixed protocol to support large files.
        Tries all nodes if the first one is unavailable.
        """
        message = {
            "type": "client_download",
            "filename": filename
        }

        # try the given node first
        response = self._send_download_request(node_id, message)
        if not (response and response.get("status") == "ok"):
            print(f"[Client] {node_id} unavailable or file not found, trying other nodes...")
            for nid in self.nodes:
                if nid != node_id:
                    response = self._send_download_request(nid, message)
                    if response and response.get("status") == "ok":
                        print(f"[Client] Found file on {nid}")
                        break

        if not (response and response.get("status") == "ok"):
            print(f"[Client] Download failed - file '{filename}' not found on any node")
            return None

        data = response.get("data")
        is_binary = response.get("is_binary", False)

        print(f"[Client] Download successful: {filename}")
        print(f"[Client] Timestamp: {response.get('timestamp')}")
        print(f"[Client] Raft leader: {response.get('raft_leader')}")

        # save to disk if path provided
        if save_to:
            try:
                if is_binary:
                    with open(save_to, "wb") as f:
                        f.write(base64.b64decode(data))
                else:
                    with open(save_to, "w") as f:
                        f.write(data)
                print(f"[Client] File saved to: {save_to}")
            except Exception as e:
                print(f"[Client] Failed to save file: {e}")

        return data

    def get_raft_status(self, node_id="node1"):
        """Get the current Raft consensus status from a node."""
        response = self._send_request(node_id, {"type": "raft_status"})
        if response:
            print(f"[Client] Raft status from {node_id}: {response}")
        return response


if __name__ == "__main__":
    client = Client()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python client.py upload <filename> <data_or_filepath> [node_id]")
        print("  python client.py download <filename> [node_id] [save_to_path]")
        print("  python client.py status [node_id]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "upload" and len(sys.argv) >= 4:
        filename = sys.argv[2]
        data_or_path = sys.argv[3]
        node_id = sys.argv[4] if len(sys.argv) >= 5 else "node1"
        if os.path.exists(data_or_path):
            client.upload(filename=filename, node_id=node_id, filepath=data_or_path)
        else:
            client.upload(filename=filename, data=data_or_path, node_id=node_id)

    elif command == "download" and len(sys.argv) >= 3:
        filename = sys.argv[2]
        node_id = sys.argv[3] if len(sys.argv) >= 4 else "node1"
        save_to = sys.argv[4] if len(sys.argv) == 5 else None
        result = client.download(filename, node_id, save_to)
        if result and not save_to:
            print(f"[Client] File contents: {result}")

    elif command == "status":
        node_id = sys.argv[2] if len(sys.argv) >= 3 else "node1"
        client.get_raft_status(node_id)

    else:
        print("Invalid command")
        print("Usage:")
        print("  python client.py upload <filename> <data_or_filepath> [node_id]")
        print("  python client.py download <filename> [node_id] [save_to_path]")
        print("  python client.py status [node_id]")