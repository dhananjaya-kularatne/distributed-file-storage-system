# Distributed File Storage System
### SE2062 — Distributed Systems | Group Assignment

---

## Team Members

| Name | Registration No. | Email | Component |
|------|-----------------|-------|-----------|
| Kularatne R.A.D | IT24103431 | it24103431@my.sliit.lk | Fault Tolerance |
| Peiris M.D.D.P | IT24100532 | it24100532@my.sliit.lk | Consensus & Agreement |
| Samaraweera S.K.U.S.S | IT24101841 | it24101841@my.sliit.lk | Data Replication & Consistency |
| Srinayaka S.P.B.M. | IT24103435 | it24103435@my.sliit.lk | Time Synchronization |

---

## Project Overview

A fault-tolerant distributed file storage system deployed across a three-node cluster. The system ensures high availability, strong consistency, and automatic recovery using four core distributed computing techniques:

- **Fault Tolerance** — Heartbeat-based failure detection (2s interval, 5s timeout) with automatic file recovery when a failed node rejoins
- **Data Replication** — Primary-backup replication with Lamport clock versioning and Base64 binary file support
- **Time Synchronization** — Cristian's Algorithm for physical clock correction with Lamport logical clocks as fallback
- **Consensus** — Raft algorithm for leader election, majority log replication, and persistent crash recovery

---

## Prerequisites

- Python 3.8 or higher
- pip

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/[your-github-username]/distributed-file-storage-system.git
cd distributed-file-storage-system

# 2. Install the only required dependency (Flask for the web dashboard)
pip install flask
```

> **Note:** No other packages are required. The system uses only Python standard library modules
> (socket, threading, json, os, time, base64, struct) plus Flask for the web UI.

---

## Project Structure

```
distributed-file-storage-system/
├── config.py               # Node addresses, ports, buffer size, storage directory
├── fault_tolerance.py      # Heartbeat detection, save/load/replicate, recovery
├── replication.py          # Primary-backup replication, binary file support
├── time_sync.py            # Cristian's Algorithm, Lamport clocks
├── consensus.py            # Raft leader election, log replication, persist state
├── server.py               # IntegratedNode — wires all 4 modules together
├── client.py               # CLI client for upload, download, status
├── app.py                  # Flask web dashboard (port 8080)
├── main.py                 # Entry point to start a node
├── test_fault_tolerance.py # 5 fault tolerance tests
├── replication_test.py     # 6 replication tests
├── test_time_sync.py       # 6 time synchronization tests
├── test_consensus.py       # 8 consensus tests
└── README.md
```

> **Note:** The `storage/` folder is NOT included in the repository (it is in `.gitignore`).
> It is created automatically when you start the nodes using `python main.py`.
> You do not need to create it manually.

---

## Running the System

### Step 1 — Start all 3 nodes (3 separate terminals)

The nodes must be started **before** the web dashboard. Starting a node automatically
creates its storage folder (`storage/node1`, `storage/node2`, `storage/node3`).

```bash
# Terminal 1
python main.py node1

# Terminal 2
python main.py node2

# Terminal 3
python main.py node3
```

Wait a few seconds for the nodes to connect and elect a Raft leader. You will see
output like `Became LEADER (term 1)` in one of the terminals.

---

### Step 2 — Launch the Web Dashboard (optional)

Once the nodes are running, open a **4th terminal** and run:

```bash
pip install flask      # only needed once
python app.py
```

Then open your browser at: **http://localhost:8080**

> **Important:** Always start the nodes (`python main.py`) **before** running `python app.py`.
> If `app.py` is opened first, all nodes will appear as Offline until the nodes are started.
> The dashboard retries automatically every 3 seconds — no restart needed.

The web dashboard shows:
- Live node status (Leader / Follower / Offline) with auto-refresh every 3 seconds
- **Start / Stop** buttons on each node card for demonstrating leader election live
- File upload with drag and drop or text input
- File list with one-click download
- Live replication progress animation when a file is uploaded
- Recovery progress bar when a stopped node restarts
- Event log showing all uploads, downloads, and node state changes

---

### Step 3 — Upload a file (CLI)

```bash
# Upload a text file
python client.py upload hello.txt "hello distributed world"

# Upload a binary file (PDF, image, etc.) — provide the full path
python client.py upload report.pdf "C:\path\to\report.pdf"
```

---

### Step 4 — Download a file (CLI)

```bash
# Download from a specific node and print contents
python client.py download hello.txt node1

# Download and save to disk
python client.py download report.pdf node1 "C:\path\to\save\report.pdf"
```

---

### Step 5 — Check Raft status (CLI)

```bash
python client.py status node1
python client.py status node2
python client.py status node3
```

---

## Running the Tests

Each module has its own test file. Run them **independently** — no nodes need to be
running first as each test starts its own Node instances internally.

```bash
# Fault Tolerance — 5 tests (~2 minutes due to heartbeat timeouts)
python test_fault_tolerance.py

# Data Replication — 6 tests (uses mock objects, runs instantly)
python replication_test.py

# Time Synchronization — 6 tests (runs instantly)
python test_time_sync.py

# Consensus — 8 socket-based integration tests (~3 minutes)
python test_consensus.py
```

> **Important:** Before running `test_fault_tolerance.py`, make sure no other nodes
> are running and delete the storage folder contents to avoid leftover files
> affecting the tests:
> ```bash
> # Windows PowerShell
> Remove-Item -Recurse -Force storage
>
> # Then run tests
> python test_fault_tolerance.py
> ```

### Test Summary

| Test File | Tests | What is Tested |
|-----------|-------|----------------|
| test_fault_tolerance.py | 5 | Failure detection, replication, recovery, failed node skip, binary files |
| replication_test.py | 6 | Text/binary replication payload, follower write modes, error handling |
| test_time_sync.py | 6 | Clock offset, Lamport ordering, sync fallback, skew accumulation |
| test_consensus.py | 8 | Leader election, re-election, follower recognition, log commit, crash recovery |
| **Total** | **25** | **All passing** |

---

## Node Configuration

Configured in `config.py`:

```python
NODES = {
    "node1": {"host": "127.0.0.1", "port": 5001},
    "node2": {"host": "127.0.0.1", "port": 5002},
    "node3": {"host": "127.0.0.1", "port": 5003},
}
STORAGE_DIR = "storage"
BUFFER_SIZE = 65536
```

Raft RPC ports are automatically set to node port + 1000:
- node1 Raft: 6001
- node2 Raft: 6002
- node3 Raft: 6003

---

## Demonstrating Key Features

### Leader Election
1. Start all 3 nodes and open the web dashboard (`python app.py`)
2. One node card will show **Leader** in amber
3. Click **Stop** on the leader node card
4. Watch the remaining two nodes elect a new leader within 5–10 seconds
5. Click **Start** to bring the stopped node back as a Follower

### File Recovery
1. Start all 3 nodes
2. Upload a file via the dashboard or CLI
3. Stop one node using the dashboard Stop button
4. Restart the stopped node using the Start button
5. The alive nodes automatically push all files back — watch the recovery progress bar

### Replication
1. Upload a file to any node
2. Download the same file from a different node
3. The content will be identical — the file was replicated to all nodes on upload

---
