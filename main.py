"""
main.py
Entry point to start an integrated node in the distributed file storage system.

Usage:
    python main.py <node_id>

Example:
    python main.py node1
    python main.py node2
    python main.py node3
"""

import sys
import time
from server import IntegratedNode


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py <node_id>")
        print("Example: python main.py node1")
        sys.exit(1)

    node_id = sys.argv[1]

    if node_id not in ["node1", "node2", "node3"]:
        print(f"Invalid node_id: {node_id}")
        print("Valid options: node1, node2, node3")
        sys.exit(1)

    node = IntegratedNode(node_id)
    node.start()

    print(f"[{node_id}] Running... Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        node.is_alive = False
        print(f"[{node_id}] Shutting down.")