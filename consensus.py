from enum import Enum
from typing import List, Optional, Dict


class Role(Enum):
    FOLLOWER = "Follower"
    CANDIDATE = "Candidate"
    LEADER = "Leader"


class Node:
    """Minimal Raft node skeleton for bootstrapping consensus logic.

    This file intentionally contains only the smallest set of fields and
    state-transition helpers needed to proceed with implementing elections
    and replication in later steps.
    """

    def __init__(self, node_id: str, peers: Optional[List[str]] = None):
        self.id = node_id
        self.peers: List[str] = peers or []

        # Persistent state on all servers
        self.current_term: int = 0
        self.voted_for: Optional[str] = None
        self.log: List[dict] = []

        # Volatile state on all servers
        self.commit_index: int = -1
        self.last_applied: int = -1

        # Volatile state on leaders
        self.next_index: Dict[str, int] = {}
        self.match_index: Dict[str, int] = {}

        # Role
        self.state: Role = Role.FOLLOWER

    def become_follower(self, term: int, leader_id: Optional[str] = None) -> None:
        self.state = Role.FOLLOWER
        self.current_term = term
        self.voted_for = None

    def become_candidate(self) -> None:
        self.state = Role.CANDIDATE
        self.current_term += 1
        self.voted_for = self.id

    def become_leader(self) -> None:
        self.state = Role.LEADER
        last_log_index = len(self.log) - 1
        for p in self.peers:
            # per Raft, leader initializes nextIndex to lastLogIndex+1
            self.next_index[p] = last_log_index + 1
            self.match_index[p] = -1

    def __repr__(self) -> str:
        return f"<Node id={self.id} role={self.state.value} term={self.current_term} peers={len(self.peers)}>"

    # --- RequestVote RPC handler ---
    def on_request_vote(self, candidate_id: str, candidate_term: int, last_log_index: int = -1, last_log_term: int = 0):
        """Handle an incoming RequestVote RPC from a candidate.

        Returns a dict with keys `voteGranted` (bool) and `term` (int).
        """
        if candidate_term < self.current_term:
            return {"voteGranted": False, "term": self.current_term}

        # If candidate has higher term, update and become follower
        if candidate_term > self.current_term:
            self.become_follower(candidate_term)

        # Simple log up-to-date check: prefer candidate if its last index is >= ours
        local_last_index = len(self.log) - 1
        if self.voted_for is None or self.voted_for == candidate_id:
            if last_log_index >= local_last_index:
                self.voted_for = candidate_id
                return {"voteGranted": True, "term": self.current_term}

        return {"voteGranted": False, "term": self.current_term}

    # --- Candidate behavior: start election ---
    def start_election(self, peers_map: Dict[str, 'Node']) -> bool:
        """Begin election: become candidate, solicit votes from peers_map.

        peers_map maps peer id -> Node instance. Returns True if elected leader.
        """
        self.become_candidate()

        votes = 1  # vote for self
        total = len(self.peers) + 1
        majority = total // 2 + 1

        for p in self.peers:
            peer = peers_map.get(p)
            if peer is None:
                continue

            resp = peer.on_request_vote(
                candidate_id=self.id,
                candidate_term=self.current_term,
                last_log_index=len(self.log) - 1,
                last_log_term=self.log[-1]["term"] if self.log else 0,
            )

            # If peer has higher term, step down
            if resp.get("term", 0) > self.current_term:
                self.become_follower(resp["term"])
                return False

            if resp.get("voteGranted"):
                votes += 1

        if votes >= majority:
            self.become_leader()
            return True

        return False
