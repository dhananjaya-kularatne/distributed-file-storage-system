import time
import threading
from fault_tolerance import Node

def test_heartbeat():
    print("\n=== Test 1: Heartbeat Detection ===")
    node1 = Node("node1")
    node2 = Node("node2")
    node1.start()
    node2.start()

    time.sleep(6)

    # both nodes see each other as alive
    print(f"node1 failed nodes: {node1.failed_nodes}")
    print(f"node2 failed nodes: {node2.failed_nodes}")

    if "node2" not in node1.failed_nodes and "node1" not in node2.failed_nodes:
        print("PASSED - both nodes are sending and receiving heartbeats")
    else:
        print("FAILED - heartbeat not working correctly")

    node1.is_alive = False
    node2.is_alive = False
    time.sleep(1)

def test_failure_detection():
    print("\n=== Test 2: Failure Detection ===")
    node1 = Node("node1")
    node2 = Node("node2")
    node1.start()
    node2.start()

    time.sleep(4)

    # simulate node2 failure
    print("Simulating node2 failure...")
    node2.is_alive = False
    time.sleep(7)

    # node1 should detect node2 as failed
    print(f"node1 failed nodes: {node1.failed_nodes}")

    if "node2" in node1.failed_nodes:
        print("PASSED - node1 detected node2 failure")
    else:
        print("FAILED - node1 did not detect node2 failure")

    node1.is_alive = False
    time.sleep(1)

if __name__ == "__main__":
    test_heartbeat()
    test_failure_detection()