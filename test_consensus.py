"""
test_consensus.py
Socket-based tests for the Raft consensus module.

All tests start real Node instances that communicate over sockets,
making them realistic integration tests rather than unit tests.
"""

import time
import threading
from consensus import Node, Role


def wait_for_leader(nodes, timeout=15):
    """Wait until one node becomes leader and return it. Returns None if timeout."""
    start = time.time()
    while time.time() - start < timeout:
        for node in nodes:
            if node.state == Role.LEADER:
                return node
        time.sleep(0.5)
    return None


def stop_all(nodes):
    """Stop all nodes cleanly."""
    for node in nodes:
        node.is_alive = False
    time.sleep(1)


def test_leader_election():
    print("\n=== Test 1: Leader Election ===")

    node1 = Node("node1")
    node2 = Node("node2")
    node3 = Node("node3")

    node1.start()
    node2.start()
    node3.start()

    # wait for a leader to emerge
    leader = wait_for_leader([node1, node2, node3], timeout=15)

    if leader:
        print(f"PASSED - Leader elected: {leader.node_id} (term {leader.current_term})")
        # verify only one leader
        leaders = [n for n in [node1, node2, node3] if n.state == Role.LEADER]
        print(f"Number of leaders: {len(leaders)}")
        if len(leaders) == 1:
            print("PASSED - Only one leader at a time")
        else:
            print("FAILED - Multiple leaders detected")
    else:
        print("FAILED - No leader elected within timeout")

    stop_all([node1, node2, node3])


def test_leader_failure_and_reelection():
    print("\n=== Test 2: Leader Failure and Re-election ===")

    node1 = Node("node1")
    node2 = Node("node2")
    node3 = Node("node3")

    node1.start()
    node2.start()
    node3.start()

    # wait for first leader
    leader = wait_for_leader([node1, node2, node3], timeout=15)
    if not leader:
        print("FAILED - No initial leader elected")
        stop_all([node1, node2, node3])
        return

    print(f"Initial leader: {leader.node_id} (term {leader.current_term})")

    # simulate leader failure by stopping it
    print(f"Simulating failure of {leader.node_id}...")
    leader.is_alive = False
    time.sleep(1)

    # remaining nodes
    remaining = [n for n in [node1, node2, node3] if n.node_id != leader.node_id]

    # wait for new leader
    new_leader = wait_for_leader(remaining, timeout=15)

    if new_leader and new_leader.node_id != leader.node_id:
        print(f"PASSED - New leader elected: {new_leader.node_id} (term {new_leader.current_term})")
        print(f"Term increased from {leader.current_term} to {new_leader.current_term}")
    else:
        print("FAILED - No new leader elected after failure")

    stop_all([node1, node2, node3])


def test_follower_recognizes_leader():
    print("\n=== Test 3: Followers Recognize Leader ===")

    node1 = Node("node1")
    node2 = Node("node2")
    node3 = Node("node3")

    node1.start()
    node2.start()
    node3.start()

    # wait for leader
    leader = wait_for_leader([node1, node2, node3], timeout=15)
    if not leader:
        print("FAILED - No leader elected")
        stop_all([node1, node2, node3])
        return

    # give time for heartbeats to propagate
    time.sleep(2)

    # check followers know who the leader is
    followers = [n for n in [node1, node2, node3] if n.state == Role.FOLLOWER]
    all_know_leader = all(f.leader_id == leader.node_id for f in followers)

    if all_know_leader:
        print(f"PASSED - All followers recognize {leader.node_id} as leader")
    else:
        for f in followers:
            print(f"  {f.node_id} thinks leader is: {f.leader_id}")
        print("FAILED - Some followers don't recognize the leader")

    stop_all([node1, node2, node3])


def test_append_command():
    print("\n=== Test 4: Leader Appends Command to Log ===")

    node1 = Node("node1")
    node2 = Node("node2")
    node3 = Node("node3")

    node1.start()
    node2.start()
    node3.start()

    # wait for leader
    leader = wait_for_leader([node1, node2, node3], timeout=15)
    if not leader:
        print("FAILED - No leader elected")
        stop_all([node1, node2, node3])
        return

    print(f"Leader: {leader.node_id}")

    # append a command via the leader
    committed = leader.append_command({"action": "upload", "filename": "test.txt"})

    if committed:
        print(f"PASSED - Command committed successfully")
        print(f"Leader log length: {len(leader.log)}")
        print(f"Commit index: {leader.commit_index}")
    else:
        print("FAILED - Command not committed")

    stop_all([node1, node2, node3])


def test_raft_status():
    print("\n=== Test 5: Raft Status Reporting ===")

    node1 = Node("node1")
    node2 = Node("node2")
    node3 = Node("node3")

    node1.start()
    node2.start()
    node3.start()

    # wait for leader
    leader = wait_for_leader([node1, node2, node3], timeout=15)
    if not leader:
        print("FAILED - No leader elected")
        stop_all([node1, node2, node3])
        return

    time.sleep(1)

    # check status of all nodes
    for node in [node1, node2, node3]:
        status = node.get_status()
        print(f"  {status['node_id']}: role={status['role']}, term={status['term']}, leader={status['leader_id']}")

    # verify is_leader() works correctly
    leaders = [n for n in [node1, node2, node3] if n.is_leader()]
    if len(leaders) == 1 and leaders[0].node_id == leader.node_id:
        print("PASSED - is_leader() works correctly")
    else:
        print("FAILED - is_leader() not working correctly")

    stop_all([node1, node2, node3])


def test_persistent_state(tmp_path=None):
    print("\n=== Test 6: Crash Recovery - Persistent State ===")

    import tempfile
    import os

    state_dir = tmp_path or tempfile.mkdtemp()

    # create node, set some state and persist
    node = Node("node1", state_dir=state_dir)
    node.current_term = 5
    node.voted_for = "node2"
    node.log = [{"term": 4, "cmd": "x"}, {"term": 5, "cmd": "y"}]
    node.persist_state()

    # create new instance simulating restart - should load persisted state
    node2 = Node("node1", state_dir=state_dir)
    node2.load_state()

    if (node2.current_term == 5 and
        node2.voted_for == "node2" and
        node2.log == [{"term": 4, "cmd": "x"}, {"term": 5, "cmd": "y"}]):
        print("PASSED - State correctly restored after crash")
    else:
        print("FAILED - State not correctly restored")
        print(f"  term: {node2.current_term} (expected 5)")
        print(f"  voted_for: {node2.voted_for} (expected node2)")
        print(f"  log: {node2.log}")

def test_term_increments_on_election():
    print("\n=== Test 7: Term is at Least 1 After Election ===")
    node1 = Node("node1")
    node2 = Node("node2")
    node3 = Node("node3")
    node1.start()
    node2.start()
    node3.start()

    leader = wait_for_leader([node1, node2, node3], timeout=15)
    if not leader:
        print("FAILED - no leader elected")
        stop_all([node1, node2, node3])
        return

    if leader.current_term >= 1:
        print(f"PASSED - term is {leader.current_term} after election")
    else:
        print("FAILED - term should be at least 1")

    stop_all([node1, node2, node3])


def test_node_rejects_stale_vote():
    print("\n=== Test 8: Node Rejects Stale Vote Request ===")
    node = Node("node1")
    node.current_term = 10

    response = node.on_request_vote(
        candidate_id="node2",
        candidate_term=5,
        last_log_index=-1,
        last_log_term=0
    )

    if response["voteGranted"] == False and response["term"] == 10:
        print("PASSED - stale vote request rejected correctly")
    else:
        print("FAILED - stale vote was not rejected")


if __name__ == "__main__":
    test_leader_election()
    time.sleep(2)
    test_leader_failure_and_reelection()
    time.sleep(2)
    test_follower_recognizes_leader()
    time.sleep(2)
    test_append_command()
    time.sleep(2)
    test_raft_status()
    time.sleep(2)
    test_persistent_state()
    time.sleep(2)
    test_term_increments_on_election()
    time.sleep(2)
    test_node_rejects_stale_vote()
    print("\n=== All tests completed ===")
    