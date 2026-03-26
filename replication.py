import os
import json
import socket
import base64
from config import NODES, BUFFER_SIZE, STORAGE_DIR

class Replicator:
    def __init__(self, node_id):
        self.node_id = node_id
        # Filter out ourselves so we don't replicate to ourselves
        self.peers = {nid: info for nid, info in NODES.items() if nid != node_id}
        
        # Ensure our storage path exists
        self.storage_path = os.path.join(STORAGE_DIR, node_id)
        os.makedirs(self.storage_path, exist_ok=True)

    def replicate_to_followers(self, filename, file_data, lamport_clock):
        """
        Called by the LEADER.
        Asynchronously pushes the uploaded file to all follower nodes.
        file_data can be a normal string OR binary bytes. We handle binary by Base64 encoding it.
        """
        
        is_binary = isinstance(file_data, bytes)
        encoded_data = base64.b64encode(file_data).decode('utf-8') if is_binary else file_data

        message = {
            "type": "replicate_file",
            "filename": filename,
            "data": encoded_data,
            "is_binary": is_binary,
            "lamport_clock": lamport_clock
        }

        # Send this message to all available peers
        for peer_id, peer_info in self.peers.items():
            self._send_to_peer(peer_id, peer_info["host"], peer_info["port"], message)

    def _send_to_peer(self, peer_id, host, port, message):
        """Helper function to send JSON over a TCP socket quickly."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3.0)  # 3 seconds timeout
                s.connect((host, port))
                
                # Convert the message to JSON and encode it to bytes
                payload = json.dumps(message).encode('utf-8')
                s.sendall(payload)
                
                # We do not strictly wait for an 'ok' here because it's asynchronous
                 # but we can do a quick recv if needed.
                print(f"[{self.node_id}] Successfully sent replication message for '{message['filename']}' to {peer_id}")
        except Exception as e:
            print(f"[{self.node_id}] Failed to replicate to {peer_id}: {e}")

    def handle_replication_request(self, message):
        """
        Called by a FOLLOWER when receiving a 'replicate_file' message 
        from the Leader. Saves the file to disk and records the lamport clock.
        """
        filename = message.get("filename")
        encoded_data = message.get("data")
        is_binary = message.get("is_binary", False)
        lamport_clock = message.get("lamport_clock", 0)

        if not filename or encoded_data is None:
            print(f"[{self.node_id}] Received invalid replication message: missing filename or data")
            return

        file_path = os.path.join(self.storage_path, filename)
        meta_path = file_path + ".meta"

        try:
            # Decode the data appropriately
            if is_binary:
                file_data = base64.b64decode(encoded_data)
                mode = "wb"
            else:
                file_data = encoded_data
                mode = "w"

            # Save the actual file text/binary data
            with open(file_path, mode, encoding="utf-8" if not is_binary else None) as f:
                f.write(file_data)
            
            # Save metadata (the lamport clock) separately
            with open(meta_path, "w", encoding="utf-8") as meta_f:
                json.dump({"lamport_clock": lamport_clock}, meta_f)

            print(f"[{self.node_id}] Successfully saved replicated file: {filename} (Lamport Clock: {lamport_clock})")
        except Exception as e:
            print(f"[{self.node_id}] Error saving replicated file {filename}: {e}")
