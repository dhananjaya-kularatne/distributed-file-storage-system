from consensus import Node, Role


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
