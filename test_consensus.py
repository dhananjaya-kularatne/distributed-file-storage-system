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
    peers = {"n1": n1, "n2": n2, "n3": n3}

    elected = n1.start_election(peers)
    assert elected is True
    assert n1.state == Role.LEADER


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


def test_send_heartbeats_replication_round():
    # leader has initial prefix log and will append one new entry to replicate
    leader = Node("l1", peers=["f1", "f2"]) 
    # initialize leader log with two entries
    leader.log = [{"term": 1, "cmd": "x"}, {"term": 1, "cmd": "y"}]
    leader.current_term = 2

    # followers have the prefix (so replication of new entry should succeed)
    f1 = Node("f1", peers=["l1", "f2"])
    f2 = Node("f2", peers=["l1", "f1"])
    f1.log = leader.log.copy()
    f2.log = leader.log.copy()

    peers = {"l1": leader, "f1": f1, "f2": f2}

    leader.become_leader()
    # leader appends a new entry at index 2
    leader.log.append({"term": leader.current_term, "cmd": "z"})

    # send one round of heartbeats/replication
    leader.send_heartbeats(peers)

    # followers should have received heartbeat and appended the new entry
    assert f1.last_heartbeat > 0
    assert f2.last_heartbeat > 0
    assert f1.log[-1]["cmd"] == "z"
    assert f2.log[-1]["cmd"] == "z"

    # leader's match_index and next_index updated
    assert leader.match_index["f1"] == 2
    assert leader.match_index["f2"] == 2
    assert leader.next_index["f1"] == 3
    assert leader.next_index["f2"] == 3
