import time
import os
from fault_tolerance import Node


def clear_storage():
    for nid in ["node1", "node2", "node3"]:
        path = os.path.join("storage", nid)
        if os.path.exists(path):
            for f in os.listdir(path):
                try:
                    os.remove(os.path.join(path, f))
                except:
                    pass

def test_failure_detection():
    
    print("\n=== Test 1: Failure Detection using Heartbeat ===")
    node1 = Node("node1")
    node2 = Node("node2")
    node1.start()
    node2.start()
    time.sleep(4)

    # check heartbeats working - both nodes alive
    print(f"node1 failed nodes: {node1.failed_nodes}")
    print(f"node2 failed nodes: {node2.failed_nodes}")

    # simulate node2 failure
    print("Simulating node2 failure...")
    node2.is_alive = False
    time.sleep(7)

    print(f"node1 failed nodes: {node1.failed_nodes}")
    if "node2" in node1.failed_nodes:
        print("PASSED - heartbeat detected node2 failure")
    else:
        print("FAILED - failure not detected")

    node1.is_alive = False
    time.sleep(1)

def test_replication():
    print("\n=== Test 2: File Replication ===")
    node1 = Node("node1")
    node2 = Node("node2")
    node3 = Node("node3")
    node1.start()
    node2.start()
    node3.start()

    time.sleep(4)

    # save a file on node1
    print("Saving file on node1...")
    node1.save_file("replication_test.txt", "this file should be on all nodes")
    time.sleep(2)

    # check if file is on node2 and node3
    data2 = node2.load_file("replication_test.txt")
    data3 = node3.load_file("replication_test.txt")

    print(f"node2 has file: {data2}")
    print(f"node3 has file: {data3}")

    if data2 and data3:
        print("PASSED - file replicated to all nodes")
    else:
        print("FAILED - file not replicated correctly")

    node1.is_alive = False
    node2.is_alive = False
    node3.is_alive = False
    time.sleep(1)

def test_recovery():
    print("\n=== Test 3: Checkpointing and Rollback Recovery ===")
    node1 = Node("node1")
    node2 = Node("node2")
    node1.start()
    node2.start()

    time.sleep(4)

    # save a file on node1 so it replicates to node2
    print("Saving file on node1...")
    node1.save_file("recovery_test.txt", "this should be recovered")
    time.sleep(2)

    # simulate node2 failure
    print("Simulating node2 failure...")
    node2.is_alive = False
    time.sleep(7)

    # bring node2 back online
    print("Bringing node2 back online...")
    node2_recovered = Node("node2")
    node2_recovered.start()
    time.sleep(7)

    # check if node2 got its files back
    data, _ = node2_recovered.load_file("recovery_test.txt")
    print(f"Recovered data: {data}")

    if data == "this should be recovered":
        print("PASSED - node2 recovered its files after rejoining")
    else:
        print("FAILED - recovery did not work correctly")

    node1.is_alive = False
    node2_recovered.is_alive = False
    time.sleep(1)

def test_no_replication_to_failed_node():
    print("\n=== Test 4: No Replication to Failed Node ===")
    clear_storage()
    node1 = Node("node1")
    node2 = Node("node2")
    node3 = Node("node3")
    node1.start()
    node2.start()
    node3.start()
    time.sleep(3)

    # stop node3 and let node1 detect it naturally
    print("Stopping node3...")
    node3.is_alive = False
    time.sleep(7)

    print("Saving file — node3 is offline...")
    node1.save_file("skip_test.txt", "should not reach node3")
    time.sleep(2)

    data3, _ = node3.load_file("skip_test.txt")
    if data3 is None:
        print("PASSED - file not replicated to failed node3")
    else:
        print("FAILED - file was replicated to a failed node")

    node1.is_alive = False
    node2.is_alive = False
    time.sleep(1)


def test_binary_file_save_and_load():
    print("\n=== Test 5: Binary File Save and Load ===")
    clear_storage()
    import base64, os
    node1 = Node("node1")
    node1.start()
    time.sleep(1)

    binary_content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
    encoded = base64.b64encode(binary_content).decode()

    node1.save_file("test_image.png", encoded, is_binary=True)
    time.sleep(1)

    # read directly from storage to verify binary was saved correctly
    filepath = os.path.join(node1.storage_path, "test_image.png")
    with open(filepath, "rb") as f:
        saved_content = f.read()

    if saved_content == binary_content:
        print("PASSED - binary file saved and loaded correctly")
    else:
        print("FAILED - binary file not handled correctly")

    node1.is_alive = False
    time.sleep(1)


if __name__ == "__main__":
    clear_storage()
    test_failure_detection()
    test_replication()
    test_recovery()
    test_no_replication_to_failed_node()
    test_binary_file_save_and_load()