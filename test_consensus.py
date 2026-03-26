from consensus import Node, Role
import time


def test_bootstrap_node_defaults():
    n = Node("n1", peers=["n2", "n3"])
    assert n.id == "n1"
    assert n.state == Role.FOLLOWER
    assert n.current_term == 0
    assert n.voted_for is None
    assert isinstance(n.peers, list)


def test_state_transitions():
    n = Node("n1", peers=["n2", "n3"])
    n.become_candidate()
    assert n.state == Role.CANDIDATE
    assert n.voted_for == "n1"
    assert n.current_term >= 1

    n.become_leader()
    assert n.state == Role.LEADER
    # next_index and match_index must be initialized for peers
    assert all(p in n.next_index for p in n.peers)
    assert all(p in n.match_index for p in n.peers)


def test_election_majority():
    n1 = Node("n1", peers=["n2", "n3"])
    n2 = Node("n2", peers=["n1", "n3"])
    n3 = Node("n3", peers=["n1", "n2"])

    # test vote request handling directly
    resp = n2.on_request_vote("n1", candidate_term=1, last_log_index=-1, last_log_term=0)
    assert resp["voteGranted"] == True

    resp = n3.on_request_vote("n1", candidate_term=1, last_log_index=-1, last_log_term=0)
    assert resp["voteGranted"] == True

    print("PASSED - vote request handling works")


def test_higher_term_causes_step_down():
    n1 = Node("n1", peers=["n2", "n3"])
    n2 = Node("n2", peers=["n1", "n3"])
    n3 = Node("n3", peers=["n1", "n2"])
    # make a peer with higher term so n1 will step down when it sees it
    n2.current_term = 5
    peers = {"n1": n1, "n2": n2, "n3": n3}

    elected = n1.start_election(peers)
    assert elected is False
    assert n1.state == Role.FOLLOWER
    assert n1.current_term == 5


def test_persistent_state(tmp_path):
    state_dir = tmp_path
    n = Node("p1", peers=[], state_dir=state_dir)
    n.current_term = 3
    n.voted_for = "p2"
    n.log = [{"term": 1, "cmd": "x"}]
    n.persist_state()

    # new instance should load persisted values
    n2 = Node("p1", peers=[], state_dir=state_dir)
    assert n2.current_term == 3
    assert n2.voted_for == "p2"
    assert n2.log == [{"term": 1, "cmd": "x"}]


def test_append_entries_heartbeat_resets_timer():
    f = Node("f1", peers=["l1"])
    assert f.last_heartbeat == 0.0
    resp = f.on_append_entries(leader_id="l1", leader_term=1)
    assert resp["success"] is True
    assert f.current_term == 1
    assert f.state == Role.FOLLOWER
    assert f.leader_id == "l1"
    assert f.last_heartbeat > 0


def test_request_vote_up_to_date_logic():
    f = Node("f1", peers=[])
    # follower has a log with last term = 2
    f.log = [{"term": 2, "cmd": "x"}]

    # candidate with older last_log_term should be rejected
    resp = f.on_request_vote(candidate_id="c1", candidate_term=1, last_log_index=0, last_log_term=1)
    assert resp["voteGranted"] is False

    # candidate with equal last_log_term and index should be granted
    resp = f.on_request_vote(candidate_id="c2", candidate_term=1, last_log_index=0, last_log_term=2)
    assert resp["voteGranted"] is True


def test_election_timeout_triggers_candidate():
    n = Node("t1", peers=[], state_dir=None)
    # make timeout small for test
    n.base_election_timeout_ms = 1
    n.election_timeout_sec = 0.001
    # simulate last heartbeat far in the past
    n.last_heartbeat = time.time() - 1.0
    triggered = n.check_election_timeout()
    assert triggered is True
    assert n.state == Role.CANDIDATE


def test_heartbeat_prevents_timeout():
    n = Node("t2", peers=[], state_dir=None)
    n.base_election_timeout_ms = 200
    n.reset_election_timer()
    # immediately check should not trigger
    triggered = n.check_election_timeout(now=n.last_heartbeat + 0.001)
    assert triggered is False


def test_heartbeat_handling():
    f = Node("f1", peers=["l1"])
    resp = f.on_append_entries(
        leader_id="l1",
        leader_term=1,
        prev_log_index=-1,
        prev_log_term=0,
        entries=[],
        leader_commit=-1
    )
    assert resp["success"] == True
    assert f.leader_id == "l1"
    print("PASSED - heartbeat handling works")


def test_leader_replicate_and_commit_majority():
    leader = Node("L", peers=["A", "B"]) 
    leader.current_term = 2
    leader.log = [{"term": 1, "cmd": "x"}]

    a = Node("A", peers=["L", "B"])
    b = Node("B", peers=["L", "A"])
    # followers have prefix so replication should succeed
    a.log = leader.log.copy()
    b.log = leader.log.copy()

    peers = {"L": leader, "A": a, "B": b}

    leader.become_leader()
    committed = leader.replicate_entry(peers, {"cmd": "new"})
    assert committed is True
    # leader commit index should point to new entry
    assert leader.commit_index == len(leader.log) - 1
    assert leader.last_applied == leader.commit_index


def test_conflict_handling_decrements_next_index():
    leader = Node("L2", peers=["F"])
    leader.current_term = 3
    # leader has two entries already
    leader.log = [{"term": 1, "cmd": "a"}, {"term": 2, "cmd": "b"}]

    # follower is missing the second entry (shorter log)
    f = Node("F", peers=["L2"])
    f.log = [{"term": 1, "cmd": "a"}]

    peers = {"L2": leader, "F": f}

    leader.become_leader()
    # append new entry and attempt to replicate
    res = leader.replicate_entry(peers, {"cmd": "c"}, max_rounds=5)
    # replication should succeed (after leader decrements next_index and retries)
    assert res is True
    assert f.log[-1]["cmd"] == "c"


def test_crash_recovery_loads_state(tmp_path):
    state_dir = tmp_path
    # Simulate a node that persisted state before crash
    n = Node("crash", peers=["p1"], state_dir=state_dir)
    n.current_term = 5
    n.voted_for = "p1"
    n.log = [{"term": 4, "cmd": "x"}, {"term": 5, "cmd": "y"}]
    n.persist_state()

    # New instance (restart) should load the state
    n2 = Node("crash", peers=["p1"], state_dir=state_dir)
    assert n2.current_term == 5
    assert n2.voted_for == "p1"
    assert n2.log == [{"term": 4, "cmd": "x"}, {"term": 5, "cmd": "y"}]
    # Should start as Follower
    assert n2.state == Role.FOLLOWER


def test_snapshot_creation_and_install():
    leader = Node("L3", peers=["F2"])
    leader.log = [{"term": 1, "cmd": "a"}, {"term": 2, "cmd": "b"}, {"term": 3, "cmd": "c"}]
    leader.create_snapshot(1)  # snapshot up to index 1
    assert leader.last_snapshot_index == 1
    assert leader.last_snapshot_term == 2
    assert leader.log == [{"term": 3, "cmd": "c"}]  # compacted

    # Follower receives InstallSnapshot
    f = Node("F2", peers=["L3"])
    f.log = [{"term": 1, "cmd": "a"}]  # behind
    resp = f.on_install_snapshot(
        leader_id="L3",
        leader_term=4,
        last_included_index=1,
        last_included_term=2,
        data={}
    )
    assert resp["term"] == 4
    assert f.last_snapshot_index == 1
    assert f.last_snapshot_term == 2
    assert f.log == []  # log compacted
