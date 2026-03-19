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
