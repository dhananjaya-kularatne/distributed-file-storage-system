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
