from enum import Enum
from typing import List, Optional, Dict, Union
import json
import os
import time
import random
from pathlib import Path


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

    def __init__(self, node_id: str, peers: Optional[List[str]] = None, state_dir: Optional[Union[str, Path]] = None):
        self.id = node_id
        self.peers: List[str] = peers or []

        # Where to store persistent state files
        self.state_dir: Path = Path(state_dir) if state_dir is not None else Path(".")
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # best-effort; tests may run in directories without explicit perms
            pass

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

        # Leader tracking and heartbeat timestamp (for follower election timers)
        self.leader_id: Optional[str] = None
        self.last_heartbeat: float = 0.0

        # Election timeout base in milliseconds. Election timeout is randomized
        # per Raft to a value in [base, 2*base] ms. Tests can override base.
        self.base_election_timeout_ms: int = 150
        self.election_timeout_sec: float = self._random_election_timeout()

        # Load persisted state if present
        self.load_state()

    def become_follower(self, term: int, leader_id: Optional[str] = None) -> None:
        self.state = Role.FOLLOWER
        self.current_term = term
        self.voted_for = None

    # --- Persistence helpers ---
    def _state_file(self) -> Path:
        return self.state_dir / f"raft_state_{self.id}.json"

    def persist_state(self) -> None:
        """Persist `current_term`, `voted_for`, and `log` atomically to disk."""
        data = {
            "current_term": self.current_term,
            "voted_for": self.voted_for,
            "log": self.log,
        }
        target = self._state_file()
        tmp = target.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        # atomic replace
        os.replace(str(tmp), str(target))

    def load_state(self) -> None:
        """Load persisted state if available. If file missing, do nothing."""
        target = self._state_file()
        if not target.exists():
            return
        try:
            with target.open("r", encoding="utf-8") as f:
                data = json.load(f)

            self.current_term = int(data.get("current_term", self.current_term))
            vf = data.get("voted_for")
            self.voted_for = vf if vf is None or isinstance(vf, str) else None
            self.log = data.get("log", self.log)
        except Exception:
            # If corrupted, ignore and start fresh (higher-level code may handle recovery)
            return

    def become_candidate(self) -> None:
        self.state = Role.CANDIDATE
        self.current_term += 1
        self.voted_for = self.id

        # When becoming candidate, reset election timer to avoid immediate re-election
        self.reset_election_timer()

    def _random_election_timeout(self) -> float:
        return random.uniform(self.base_election_timeout_ms, 2 * self.base_election_timeout_ms) / 1000.0

    def reset_election_timer(self) -> None:
        """Reset the election timer (set new randomized timeout and update last heartbeat time)."""
        self.election_timeout_sec = self._random_election_timeout()
        self.last_heartbeat = time.time()

    def check_election_timeout(self, now: Optional[float] = None) -> bool:
        """Check whether the election timeout has elapsed. If so, transition to Candidate and return True."""
        if now is None:
            now = time.time()
        elapsed = now - self.last_heartbeat
        if elapsed >= self.election_timeout_sec:
            # no heartbeat in timeout window -> become candidate
            self.become_candidate()
            return True
        return False

    def become_leader(self) -> None:
        self.state = Role.LEADER
        last_log_index = len(self.log) - 1
        for p in self.peers:
            # per Raft, leader initializes nextIndex to lastLogIndex+1
            self.next_index[p] = last_log_index + 1
            self.match_index[p] = -1
        # Heartbeat interval in milliseconds (leader sends AppendEntries at this rate)
        self.heartbeat_interval_ms: int = 50

    def send_heartbeats(self, peers_map: Dict[str, 'Node']) -> None:
        """Send AppendEntries (heartbeats) to all peers. This method performs
        one round of heartbeats/replication; it does not start a background
        thread. `peers_map` maps peer id -> Node instance.
        """
        if self.state != Role.LEADER:
            return

        for p in self.peers:
            peer = peers_map.get(p)
            if peer is None:
                continue

            next_idx = self.next_index.get(p, len(self.log))
            prev_log_index = next_idx - 1
            prev_log_term = self.log[prev_log_index]["term"] if prev_log_index >= 0 and prev_log_index < len(self.log) else 0
            entries = self.log[next_idx:]

            resp = peer.on_append_entries(
                leader_id=self.id,
                leader_term=self.current_term,
                prev_log_index=prev_log_index,
                prev_log_term=prev_log_term,
                entries=entries,
                leader_commit=self.commit_index,
            )

            # If peer reports higher term, step down
            if resp.get("term", 0) > self.current_term:
                self.become_follower(resp["term"])
                return

            if resp.get("success"):
                # update match_index and next_index
                matched = prev_log_index + len(entries)
                self.match_index[p] = matched
                self.next_index[p] = matched + 1
            else:
                # on failure, decrement next_index to retry next time
                self.next_index[p] = max(0, self.next_index.get(p, 1) - 1)

    def __repr__(self) -> str:
        return f"<Node id={self.id} role={self.state.value} term={self.current_term} peers={len(self.peers)}>"

    # --- RequestVote RPC handler ---
    def on_request_vote(self, candidate_id: str, candidate_term: int, last_log_index: int = -1, last_log_term: int = 0):
        """Handle an incoming RequestVote RPC from a candidate.

        Returns a dict with keys `voteGranted` (bool) and `term` (int).
        """
        # Reject if candidate's term is stale
        if candidate_term < self.current_term:
            return {"voteGranted": False, "term": self.current_term}

        # If candidate has higher term, update and become follower
        if candidate_term > self.current_term:
            self.become_follower(candidate_term)
            # persist term change
            try:
                self.persist_state()
            except Exception:
                pass

        # Log up-to-date check: compare last log term, then index
        local_last_index = len(self.log) - 1
        local_last_term = self.log[-1]["term"] if self.log else 0

        candidate_up_to_date = False
        if last_log_term > local_last_term:
            candidate_up_to_date = True
        elif last_log_term == local_last_term and last_log_index >= local_last_index:
            candidate_up_to_date = True

        if (self.voted_for is None or self.voted_for == candidate_id) and candidate_up_to_date:
            self.voted_for = candidate_id
            try:
                self.persist_state()
            except Exception:
                pass
            return {"voteGranted": True, "term": self.current_term}

        return {"voteGranted": False, "term": self.current_term}

    def on_append_entries(self, leader_id: str, leader_term: int, prev_log_index: int = -1, prev_log_term: int = 0, entries: Optional[List[dict]] = None, leader_commit: Optional[int] = None):
        """Handle AppendEntries RPC (used for heartbeats and replication).

        Returns dict with `success` and `term` keys. On valid leader term, reset
        the follower's heartbeat timer (`last_heartbeat`) and record leader id.
        """
        if leader_term < self.current_term:
            return {"success": False, "term": self.current_term}

        # If leader has higher term, update and become follower
        if leader_term > self.current_term:
            self.become_follower(leader_term, leader_id)
            try:
                self.persist_state()
            except Exception:
                pass

        # At this point leader_term >= current_term
        self.leader_id = leader_id
        # reset heartbeat timestamp and election timeout
        self.reset_election_timer()

        # Basic log matching check for prev_log; if mismatch, reject
        if prev_log_index != -1:
            if prev_log_index >= len(self.log):
                return {"success": False, "term": self.current_term}
            if self.log[prev_log_index].get("term", 0) != prev_log_term:
                return {"success": False, "term": self.current_term}

        # Append any new entries (heartbeat if entries is empty)
        if entries:
            # naive append: overwrite conflicting entries
            insert_at = prev_log_index + 1
            # truncate and append
            self.log = self.log[:insert_at] + entries
            try:
                self.persist_state()
            except Exception:
                pass

        # Update commit index if leader_commit provided
        if leader_commit is not None:
            self.commit_index = min(leader_commit, len(self.log) - 1)

        return {"success": True, "term": self.current_term}

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
