from enum import Enum
from typing import List, Optional, Dict, Union
import json
import os
import time
import random
import socket
import threading
from config import NODES, BUFFER_SIZE
from pathlib import Path

# ADD ELECTION_TIMEOUT constants
ELECTION_TIMEOUT_MIN = 0.5
ELECTION_TIMEOUT_MAX = 1.0
HEARTBEAT_INTERVAL = 0.1

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

        # Where to store persistent state files. If None, persistence is disabled
        # (useful for tests that don't want global files).
        self.state_dir: Optional[Path] = Path(state_dir) if state_dir is not None else None
        if self.state_dir is not None:
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

        # Snapshot state (for log compaction)
        self.last_snapshot_index: int = -1
        self.last_snapshot_term: int = 0

        # Role
        self.state: Role = Role.FOLLOWER

        # Leader tracking and heartbeat timestamp (for follower election timers)
        self.leader_id: Optional[str] = None
        self.last_heartbeat: float = 0.0

        # Election timeout base in milliseconds. Election timeout is randomized
        # per Raft to a value in [base, 2*base] ms. Tests can override base.
        self.base_election_timeout_ms: int = 150
        self.election_timeout_sec: float = self._random_election_timeout()

        # Load persisted state if present and persistence enabled
        if self.state_dir is not None:
            self.load_state()

    def become_follower(self, term: int, leader_id: Optional[str] = None) -> None:
        self.state = Role.FOLLOWER
        self.current_term = term
        self.voted_for = None

    # --- Persistence helpers ---
    def _state_file(self) -> Path:
        if self.state_dir is None:
            raise RuntimeError("persistence disabled for this node (state_dir=None)")
        return self.state_dir / f"raft_state_{self.id}.json"

    def persist_state(self) -> None:
        """Persist `current_term`, `voted_for`, and `log` atomically to disk."""
        if self.state_dir is None:
            # persistence disabled; no-op
            return
        data = {
            "current_term": self.current_term,
            "voted_for": self.voted_for,
            "log": self.log,
            "last_snapshot_index": self.last_snapshot_index,
            "last_snapshot_term": self.last_snapshot_term,
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
        if self.state_dir is None:
            return
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
            self.last_snapshot_index = int(data.get("last_snapshot_index", self.last_snapshot_index))
            self.last_snapshot_term = int(data.get("last_snapshot_term", self.last_snapshot_term))
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
        self.leader_id = self.id
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
            if next_idx <= self.last_snapshot_index:
                # Send InstallSnapshot instead
                resp = peer.on_install_snapshot(
                    leader_id=self.id,
                    leader_term=self.current_term,
                    last_included_index=self.last_snapshot_index,
                    last_included_term=self.last_snapshot_term,
                    data={},  # snapshot data
                )
                if resp.get("term", 0) > self.current_term:
                    self.become_follower(resp["term"])
                    return
                # After snapshot, set next_index to last_snapshot_index + 1
                self.next_index[p] = self.last_snapshot_index + 1
                self.match_index[p] = self.last_snapshot_index
                continue

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

    def replicate_entry(self, peers_map: Dict[str, 'Node'], command: dict, max_rounds: int = 10) -> bool:
        """Leader appends a client write and tries to replicate until committed by majority.

        Returns True if committed, False otherwise.
        This is a synchronous helper for tests/simulations.
        """
        if self.state != Role.LEADER:
            return False

        # Append to local log
        entry = {"term": self.current_term, **command}
        self.log.append(entry)
        idx = len(self.log) - 1

        total = len(self.peers) + 1
        majority = total // 2 + 1

        rounds = 0
        while rounds < max_rounds:
            rounds += 1
            # send one round of AppendEntries to followers
            self.send_heartbeats(peers_map)

            # count nodes (including leader) that have replicated idx
            replicated = 1 if idx < len(self.log) else 0
            for p in self.peers:
                if self.match_index.get(p, -1) >= idx:
                    replicated += 1

            if replicated >= majority:
                # commit and apply
                self.commit_index = max(self.commit_index, idx)
                # apply entries up to commit_index
                while self.last_applied < self.commit_index:
                    self.last_applied += 1
                return True

        return False

    def create_snapshot(self, snapshot_index: int) -> None:
        """Create a snapshot up to the given index, compacting the log."""
        if snapshot_index < self.last_snapshot_index:
            return  # already snapshotted
        if snapshot_index >= len(self.log):
            return  # index out of range

        # Assume snapshot includes state machine state; here we just compact log
        self.last_snapshot_index = snapshot_index
        self.last_snapshot_term = self.log[snapshot_index]["term"]
        # Remove entries up to snapshot_index (keep snapshot_index + 1 onwards)
        self.log = self.log[snapshot_index + 1:]
        # Adjust commit_index and last_applied relative to snapshot
        self.commit_index = max(-1, self.commit_index - (snapshot_index + 1))
        self.last_applied = max(-1, self.last_applied - (snapshot_index + 1))
        try:
            self.persist_state()
        except Exception:
            pass

    def on_install_snapshot(self, leader_id: str, leader_term: int, last_included_index: int, last_included_term: int, data: dict) -> dict:
        """Handle InstallSnapshot RPC to install a snapshot from leader."""
        if leader_term < self.current_term:
            return {"term": self.current_term}

        if leader_term > self.current_term:
            self.become_follower(leader_term, leader_id)
            try:
                self.persist_state()
            except Exception:
                pass

        # Install the snapshot
        self.last_snapshot_index = last_included_index
        self.last_snapshot_term = last_included_term
        # Discard log entries up to last_included_index
        self.log = [e for e in self.log if e.get("index", -1) > last_included_index]
        # Reset commit_index and last_applied
        self.commit_index = max(self.commit_index, last_included_index)
        self.last_applied = max(self.last_applied, last_included_index)
        # Apply snapshot data (e.g., restore state machine)
        # For simplicity, assume data is empty or handled elsewhere

        try:
            self.persist_state()
        except Exception:
            pass

        return {"term": self.current_term}

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

    def send_message(self, target_id, message):
        """Send a message to another node via socket."""
        host = NODES[target_id]["host"]
        port = NODES[target_id]["port"]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect((host, port))
                s.sendall(json.dumps(message).encode())
                s.shutdown(socket.SHUT_WR)
                response = b""
                while True:
                    chunk = s.recv(BUFFER_SIZE)
                    if not chunk:
                        break
                    response += chunk
                return json.loads(response.decode()) if response else None
        except Exception:
            return None

    def start_election_socket(self):
        """Start leader election using sockets."""
        self.become_candidate()
        votes = 1
        majority = (len(self.peers) + 1) // 2 + 1
        last_log_index = len(self.log) - 1
        last_log_term = self.log[-1]["term"] if self.log else 0

        for peer_id in self.peers:
            response = self.send_message(peer_id, {
                "type": "vote_request",
                "from": self.id,
                "term": self.current_term,
                "last_log_index": last_log_index,
                "last_log_term": last_log_term
            })
            if response:
                if response.get("term", 0) > self.current_term:
                    self.become_follower(response["term"])
                    return False
                if response.get("vote_granted"):
                    votes += 1

        if votes >= majority:
            self.become_leader()
            return True
        return False

    def handle_vote_request(self, message):
        """Handle incoming vote request from a candidate."""
        term = message.get("term", 0)
        candidate_id = message.get("from")
        last_log_index = message.get("last_log_index", -1)
        last_log_term = message.get("last_log_term", 0)

        if term < self.current_term:
            return {"vote_granted": False, "term": self.current_term}

        if term > self.current_term:
            self.become_follower(term)

        local_last_index = len(self.log) - 1
        local_last_term = self.log[-1]["term"] if self.log else 0
        up_to_date = (last_log_term > local_last_term) or \
                     (last_log_term == local_last_term and last_log_index >= local_last_index)

        if (self.voted_for is None or self.voted_for == candidate_id) and up_to_date:
            self.voted_for = candidate_id
            self.persist_state()
            return {"vote_granted": True, "term": self.current_term}

        return {"vote_granted": False, "term": self.current_term}

    def handle_append_entries(self, message):
        """Handle incoming heartbeat or log replication from leader."""
        term = message.get("term", 0)
        leader_id = message.get("leader_id")
        entries = message.get("entries", [])
        prev_log_index = message.get("prev_log_index", -1)
        prev_log_term = message.get("prev_log_term", 0)
        leader_commit = message.get("leader_commit", -1)

        if term < self.current_term:
            return {"success": False, "term": self.current_term}

        if term > self.current_term:
            self.become_follower(term, leader_id)

        # reset election timer
        self.last_heartbeat = time.time()
        self.leader_id = leader_id

        # log consistency check
        if prev_log_index != -1:
            if prev_log_index >= len(self.log):
                return {"success": False, "term": self.current_term}
            if self.log[prev_log_index].get("term") != prev_log_term:
                return {"success": False, "term": self.current_term}

        # append new entries
        if entries:
            insert_at = prev_log_index + 1
            self.log = self.log[:insert_at] + entries
            self.persist_state()

        # update commit index
        if leader_commit > self.commit_index:
            self.commit_index = min(leader_commit, len(self.log) - 1)

        return {"success": True, "term": self.current_term}

    def send_heartbeats_socket(self):
        """Send heartbeats to all peers via sockets."""
        if self.state != Role.LEADER:
            return
        for peer_id in self.peers:
            prev_log_index = self.next_index.get(peer_id, len(self.log)) - 1
            prev_log_term = self.log[prev_log_index]["term"] if prev_log_index >= 0 and prev_log_index < len(self.log) else 0
            entries = self.log[self.next_index.get(peer_id, len(self.log)):]

            response = self.send_message(peer_id, {
                "type": "append_entries",
                "term": self.current_term,
                "leader_id": self.id,
                "prev_log_index": prev_log_index,
                "prev_log_term": prev_log_term,
                "entries": entries,
                "leader_commit": self.commit_index
            })

            if response:
                if response.get("term", 0) > self.current_term:
                    self.become_follower(response["term"])
                    return
                if response.get("success"):
                    matched = prev_log_index + len(entries)
                    self.match_index[peer_id] = matched
                    self.next_index[peer_id] = matched + 1
                else:
                    self.next_index[peer_id] = max(0, self.next_index.get(peer_id, 1) - 1)

    def start_raft_loop(self):
        """Background thread — checks election timeout and sends heartbeats."""
        def _loop():
            while True:
                if self.state == Role.LEADER:
                    self.send_heartbeats_socket()
                    time.sleep(HEARTBEAT_INTERVAL)
                else:
                    elapsed = time.time() - self.last_heartbeat
                    if elapsed >= self.election_timeout_sec:
                        print(f"[{self.id}] Election timeout — starting election")
                        self.start_election_socket()
                    time.sleep(0.05)

        threading.Thread(target=_loop, daemon=True).start()
        print(f"[{self.id}] Raft loop started")
