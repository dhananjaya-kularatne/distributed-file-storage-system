"""
app.py  —  DistFS Web Console
Professional distributed-systems dashboard style (inspired by Consul / Kubernetes UI).
Includes live replication flow animation and recovery progress indicators.

Usage:
    pip install flask
    python app.py
    Open: http://localhost:8080
"""

from flask import Flask, request, jsonify, render_template_string
import socket, json, os, base64, struct, subprocess, sys, time, threading
from collections import deque
from config import NODES, BUFFER_SIZE

app = Flask(__name__)
node_processes = {}

# ── In-memory event stream (last 60 events) ─────────────────
_events = deque(maxlen=60)
_events_lock = threading.Lock()

def push_event(kind, message, node=None):
    with _events_lock:
        _events.appendleft({
            "ts": time.time(),
            "kind": kind,          # ok | warn | error | repl | recover
            "msg": message,
            "node": node
        })

# ── Socket helpers ───────────────────────────────────────────

def send_request(node_id, message, timeout=10):
    host = NODES[node_id]["host"]
    port = NODES[node_id]["port"]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
            s.sendall(json.dumps(message).encode())
            s.shutdown(socket.SHUT_WR)
            resp = b""
            while True:
                chunk = s.recv(BUFFER_SIZE)
                if not chunk: break
                resp += chunk
            return json.loads(resp.decode()) if resp else None
    except Exception:
        return None

def send_download_request(node_id, message, timeout=30):
    host = NODES[node_id]["host"]
    port = NODES[node_id]["port"]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
            s.sendall(json.dumps(message).encode())
            s.shutdown(socket.SHUT_WR)
            hdr = b""
            while len(hdr) < 4:
                c = s.recv(4 - len(hdr))
                if not c: return None
                hdr += c
            total = struct.unpack(">I", hdr)[0]
            data = b""
            while len(data) < total:
                c = s.recv(min(BUFFER_SIZE, total - len(data)))
                if not c: return None
                data += c
            return json.loads(data.decode())
    except Exception:
        return None

# ── API ──────────────────────────────────────────────────────

@app.route("/api/status")
def get_status():
    statuses = {}
    for nid in NODES:
        hb   = send_request(nid, {"type": "heartbeat", "from": "ui"}, timeout=2)
        raft = send_request(nid, {"type": "raft_status"}, timeout=2)
        proc = node_processes.get(nid)
        statuses[nid] = {
            "online": hb is not None,
            "raft": raft,
            "launched_by_ui": proc is not None and proc.poll() is None
        }
    return jsonify(statuses)

@app.route("/api/events")
def get_events():
    with _events_lock:
        return jsonify(list(_events))

@app.route("/api/node/<node_id>/stop", methods=["POST"])
def stop_node(node_id):
    if node_id not in NODES:
        return jsonify({"error": "Invalid node"}), 400
    proc = node_processes.get(node_id)
    if proc and proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=3)
        except: proc.kill()
        node_processes.pop(node_id, None)
        push_event("warn", f"{node_id} stopped manually", node_id)
        return jsonify({"status": "ok"})
    port = NODES[node_id]["port"]
    try:
        if sys.platform == "win32":
            subprocess.run(
                f'for /f "tokens=5" %a in (\'netstat -aon ^| find ":{port}"\') do taskkill /F /PID %a',
                shell=True, capture_output=True)
        else:
            subprocess.run(f"fuser -k {port}/tcp", shell=True, capture_output=True)
        push_event("warn", f"{node_id} stopped manually", node_id)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/node/<node_id>/start", methods=["POST"])
def start_node(node_id):
    if node_id not in NODES:
        return jsonify({"error": "Invalid node"}), 400
    if send_request(node_id, {"type": "heartbeat", "from": "ui"}, timeout=2):
        return jsonify({"error": f"{node_id} already running"}), 400
    try:
        main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        proc = subprocess.Popen([sys.executable, main_py, node_id],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        node_processes[node_id] = proc
        push_event("ok", f"{node_id} started (pid {proc.pid})", node_id)
        return jsonify({"status": "ok", "pid": proc.pid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files and "text" not in request.form:
        return jsonify({"error": "No file or text provided"}), 400
    node_id = request.form.get("node", "node1")
    if "file" in request.files and request.files["file"].filename:
        f = request.files["file"]
        filename = f.filename
        raw = f.read()
        try:    data, is_binary = raw.decode("utf-8"), False
        except: data, is_binary = base64.b64encode(raw).decode("utf-8"), True
    else:
        filename = request.form.get("filename", "untitled.txt")
        data     = request.form.get("text", "")
        is_binary = False

    message = {"type": "client_upload", "filename": filename, "data": data, "is_binary": is_binary}
    response = send_request(node_id, message, timeout=30)
    if not response or response.get("status") != "ok":
        for nid in NODES:
            if nid != node_id:
                response = send_request(nid, message, timeout=30)
                if response and response.get("status") == "ok":
                    break

    if response and response.get("status") == "ok":
        ldr = response.get("raft_leader", "?")
        lam = response.get("lamport_clock", "?")
        push_event("repl", f"Uploaded '{filename}' → replicated to all nodes  (leader: {ldr}, Lamport: {lam})", ldr)
        return jsonify(response)
    push_event("error", f"Upload failed for '{filename}'")
    return jsonify({"error": "Upload failed"}), 500

@app.route("/api/download/<filename>")
def download(filename):
    node_id  = request.args.get("node", "node1")
    message  = {"type": "client_download", "filename": filename}
    response = send_download_request(node_id, message)
    if not response or response.get("status") != "ok":
        for nid in NODES:
            if nid != node_id:
                response = send_download_request(nid, message)
                if response and response.get("status") == "ok":
                    push_event("ok", f"Downloaded '{filename}' from {nid} (fallback from {node_id})")
                    break
    if response and response.get("status") == "ok":
        push_event("ok", f"Downloaded '{filename}' from {node_id}")
        return jsonify(response)
    return jsonify({"error": f"'{filename}' not found"}), 404

@app.route("/api/files")
def list_files():
    files = set()
    for nf in ["node1", "node2", "node3"]:
        path = os.path.join("storage", nf)
        if os.path.exists(path):
            for f in os.listdir(path):
                if not f.endswith(".meta"):
                    files.add(f)
    return jsonify({"files": sorted(files)})

# ── HTML ─────────────────────────────────────────────────────

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>DistFS Console</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
/* ── Design tokens ── */
:root {
  --bg:         #f4f6f9;
  --sidebar-bg: #1a2332;
  --sidebar-w:  220px;
  --surface:    #ffffff;
  --border:     #dde2ec;
  --border2:    #c8d0e0;

  --navy:       #1a2332;
  --blue:       #1d6ae5;
  --blue-lt:    #e8f0fd;
  --blue-mid:   #bdd0f8;

  --teal:       #0d9488;
  --teal-lt:    #e0f5f3;

  --green:      #16a34a;
  --green-lt:   #dcfce7;
  --green-mid:  #86efac;

  --amber:      #d97706;
  --amber-lt:   #fef3c7;
  --amber-mid:  #fcd34d;

  --red:        #dc2626;
  --red-lt:     #fee2e2;
  --red-mid:    #fca5a5;

  --purple:     #7c3aed;
  --purple-lt:  #ede9fe;

  --text:       #111827;
  --text2:      #4b5563;
  --text3:      #9ca3af;

  --mono:       'IBM Plex Mono', monospace;
  --sans:       'IBM Plex Sans', sans-serif;
  --radius:     6px;
}

* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:var(--sans); display:flex; min-height:100vh; font-size:14px; }

/* ── SIDEBAR ── */
.sidebar {
  width: var(--sidebar-w);
  background: var(--sidebar-bg);
  color: #e2e8f0;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  position: fixed;
  top: 0; left: 0; bottom: 0;
  z-index: 10;
}

.sidebar-logo {
  padding: 20px 18px 16px;
  border-bottom: 1px solid #2d3f55;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-logo .product { font-size: 15px; font-weight: 700; color: #f1f5f9; letter-spacing: -0.2px; }
.sidebar-logo .version { font-size: 11px; color: #64748b; font-family: var(--mono); }

.sidebar-section { padding: 18px 0 0; }

.sidebar-label {
  font-size: 10px; font-weight: 600;
  letter-spacing: 1.5px; text-transform: uppercase;
  color: #475569; padding: 0 18px 8px;
}

.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 18px; font-size: 13px; font-weight: 500;
  color: #94a3b8; cursor: pointer; transition: all .15s;
  border-left: 3px solid transparent;
}
.nav-item:hover { color: #e2e8f0; background: #243447; }
.nav-item.active { color: #ffffff; background: #243447; border-left-color: #1d6ae5; }
.nav-icon { width: 16px; text-align: center; flex-shrink: 0; }

.sidebar-footer {
  margin-top: auto;
  padding: 14px 18px;
  border-top: 1px solid #2d3f55;
  font-size: 11px; color: #475569; font-family: var(--mono);
}

/* ── MAIN ── */
.main {
  margin-left: var(--sidebar-w);
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

/* ── TOPBAR ── */
.topbar {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 0 24px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky; top: 0; z-index: 9;
}

.topbar-title { font-size: 14px; font-weight: 600; color: var(--text); }
.topbar-right { display: flex; align-items: center; gap: 12px; }

.cluster-badge {
  display: flex; align-items: center; gap: 6px;
  background: var(--green-lt);
  border: 1px solid var(--green-mid);
  border-radius: 20px;
  padding: 4px 12px;
  font-size: 11.5px; font-weight: 600;
  color: var(--green);
}
.cluster-badge .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 0 2px var(--green-lt);
  animation: blink 2s infinite;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.4} }

.cluster-badge.degraded { background: var(--amber-lt); border-color: var(--amber-mid); color: var(--amber); }
.cluster-badge.degraded .dot { background: var(--amber); box-shadow: 0 0 0 2px var(--amber-lt); }
.cluster-badge.down { background: var(--red-lt); border-color: var(--red-mid); color: var(--red); }
.cluster-badge.down .dot { background: var(--red); box-shadow: 0 0 0 2px var(--red-lt); }

.icon-btn {
  width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text2);
  cursor: pointer; transition: all .15s; font-size: 14px;
}
.icon-btn:hover { border-color: var(--blue); color: var(--blue); background: var(--blue-lt); }

/* ── CONTENT ── */
.content { padding: 22px 24px; flex: 1; }

/* ── SECTION HEADER ── */
.section-hd {
  display: flex; align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.section-hd h2 {
  font-size: 13px; font-weight: 600; color: var(--text);
  display: flex; align-items: center; gap: 7px;
}

.section-hd h2 .ico {
  width: 22px; height: 22px;
  background: var(--blue-lt);
  border: 1px solid var(--blue-mid);
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; color: var(--blue);
}

/* ── NODE GRID ── */
.node-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 22px;
}

.node-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  transition: box-shadow .15s, border-color .15s;
  position: relative;
}

.node-card:hover { box-shadow: 0 2px 12px rgba(0,0,0,.08); }

.nc-topbar {
  height: 3px;
  background: var(--border2);
  transition: background .3s;
}

.node-card.healthy .nc-topbar  { background: var(--green); }
.node-card.leader  .nc-topbar  { background: var(--blue); }
.node-card.offline .nc-topbar  { background: var(--red); }
.node-card.recovering .nc-topbar { background: var(--amber); }
.node-card.replicating .nc-topbar { background: var(--teal); }

.nc-body { padding: 14px; }

.nc-head {
  display: flex; align-items: center;
  justify-content: space-between; margin-bottom: 10px;
}

.nc-id { font-size: 14px; font-weight: 700; font-family: var(--mono); color: var(--text); }

.status-dot {
  width: 9px; height: 9px; border-radius: 50%;
  background: var(--text3); flex-shrink: 0;
}
.status-dot.healthy    { background: var(--green); box-shadow: 0 0 0 3px var(--green-lt); animation: blink 2.5s infinite; }
.status-dot.leader     { background: var(--blue);  box-shadow: 0 0 0 3px var(--blue-lt);  animation: blink 2s infinite; }
.status-dot.offline    { background: var(--red);   box-shadow: 0 0 0 3px var(--red-lt); }
.status-dot.recovering { background: var(--amber); box-shadow: 0 0 0 3px var(--amber-lt); animation: blink 1s infinite; }
.status-dot.replicating{ background: var(--teal);  box-shadow: 0 0 0 3px var(--teal-lt); animation: blink 0.7s infinite; }

.role-tag {
  font-size: 10px; font-weight: 600;
  letter-spacing: .8px; text-transform: uppercase;
  padding: 2px 8px; border-radius: 3px;
  display: inline-flex; align-items: center; gap: 4px;
  margin-bottom: 10px; border: 1px solid;
}
.role-tag.Leader    { color: var(--blue);  border-color: var(--blue-mid);   background: var(--blue-lt); }
.role-tag.Follower  { color: var(--text2); border-color: var(--border2);    background: #f8fafc; }
.role-tag.Candidate { color: var(--purple); border-color: #c4b5fd;          background: var(--purple-lt); }
.role-tag.Offline   { color: var(--red);   border-color: var(--red-mid);    background: var(--red-lt); }

.nc-meta {
  font-size: 11.5px; color: var(--text3);
  font-family: var(--mono);
  line-height: 1.9; margin-bottom: 12px;
}
.nc-meta strong { color: var(--text2); font-weight: 500; }

/* replication progress bar */
.repl-bar-wrap {
  display: none;
  margin-bottom: 10px;
}
.repl-bar-wrap.show { display: block; }
.repl-bar-label {
  font-size: 10px; font-weight: 600; color: var(--teal);
  margin-bottom: 4px; display: flex; align-items: center; gap: 5px;
}
.repl-bar-label::before {
  content: '';
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--teal);
  animation: blink .6s infinite;
  display: inline-block;
}
.repl-bar {
  height: 4px; background: var(--teal-lt);
  border-radius: 2px; overflow: hidden;
}
.repl-bar-fill {
  height: 100%; width: 0%;
  background: var(--teal);
  border-radius: 2px;
  transition: width .3s ease;
}

/* recovery progress */
.recover-bar-wrap { display: none; margin-bottom: 10px; }
.recover-bar-wrap.show { display: block; }
.recover-bar-label {
  font-size: 10px; font-weight: 600; color: var(--amber);
  margin-bottom: 4px; display: flex; align-items: center; gap: 5px;
}
.recover-bar-label::before {
  content: '';
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--amber);
  animation: blink .8s infinite;
  display: inline-block;
}
.recover-bar { height: 4px; background: var(--amber-lt); border-radius: 2px; overflow: hidden; }
.recover-bar-fill {
  height: 100%; width: 0%;
  background: var(--amber);
  border-radius: 2px;
  transition: width .5s ease;
}

.nc-btns { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }

.nbt {
  padding: 6px 0;
  font-family: var(--sans);
  font-size: 11px; font-weight: 600;
  border-radius: 4px; cursor: pointer;
  border: 1px solid; transition: all .15s;
  text-align: center; text-transform: uppercase; letter-spacing: .5px;
}
.nbt.start { background: var(--green-lt); border-color: var(--green-mid); color: var(--green); }
.nbt.start:hover:not(:disabled) { background: var(--green); color: white; border-color: var(--green); }
.nbt.stop  { background: var(--red-lt); border-color: var(--red-mid); color: var(--red); }
.nbt.stop:hover:not(:disabled)  { background: var(--red); color: white; border-color: var(--red); }
.nbt:disabled { opacity: .35; cursor: not-allowed; }

/* ── 2-COL GRID ── */
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 18px; }

/* ── PANEL ── */
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.panel-hd {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
}

.panel-hd .ph-title { font-size: 13px; font-weight: 600; }

.panel-hd .ph-ico {
  width: 26px; height: 26px;
  background: var(--blue-lt);
  border: 1px solid var(--blue-mid);
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; color: var(--blue);
}

.panel-body { padding: 14px 16px; }

/* ── UPLOAD ── */
.upload-zone {
  border: 1.5px dashed var(--border2);
  border-radius: var(--radius);
  padding: 18px; text-align: center;
  cursor: pointer; transition: all .2s;
  background: #fafbfd;
  margin-bottom: 12px;
}
.upload-zone:hover, .upload-zone.over {
  border-color: var(--blue);
  background: var(--blue-lt);
}
.uz-icon { font-size: 22px; margin-bottom: 6px; }
.uz-text { font-size: 12px; color: var(--text3); }
.uz-text span { color: var(--blue); font-weight: 600; }
.uz-fname { font-size: 12px; color: var(--blue); font-weight: 500; margin-top: 4px; }

input[type="file"] { display: none; }

input[type="text"], textarea, select {
  width: 100%;
  background: #fafbfd;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text);
  font-family: var(--sans);
  font-size: 13px;
  padding: 8px 10px;
  outline: none;
  transition: border-color .15s, box-shadow .15s;
  margin-bottom: 8px;
}
input[type="text"]:focus, textarea:focus, select:focus {
  border-color: var(--blue);
  box-shadow: 0 0 0 3px rgba(29,106,229,.1);
  background: var(--surface);
}
textarea { resize: vertical; min-height: 68px; font-family: var(--mono); font-size: 12px; }
select { cursor: pointer; }
select option { background: var(--surface); }

.irow { display: flex; gap: 8px; align-items: flex-end; }
.irow select { margin-bottom: 0; }

.divider { height: 1px; background: var(--border); margin: 10px 0; }

/* primary button */
.btn {
  background: var(--blue);
  border: none; color: white;
  font-family: var(--sans);
  font-size: 12px; font-weight: 600;
  padding: 9px 18px; border-radius: 4px;
  cursor: pointer; white-space: nowrap;
  transition: all .15s;
  box-shadow: 0 1px 4px rgba(29,106,229,.3);
}
.btn:hover { background: #1558c9; box-shadow: 0 3px 10px rgba(29,106,229,.35); }
.btn:disabled { opacity: .45; cursor: not-allowed; box-shadow: none; }

.gbtn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text2);
  font-family: var(--sans); font-size: 11px; font-weight: 500;
  padding: 5px 11px; border-radius: 4px;
  cursor: pointer; transition: all .15s;
}
.gbtn:hover { border-color: var(--blue); color: var(--blue); background: var(--blue-lt); }

/* ── FILE LIST ── */
.flist { max-height: 250px; overflow-y: auto; }
.flist::-webkit-scrollbar { width: 4px; }
.flist::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }

.fi {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 8px; border-radius: 4px; transition: background .1s;
}
.fi:hover { background: var(--bg); }

.fext {
  width: 32px; height: 32px; border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  font-size: 9px; font-weight: 700; font-family: var(--mono);
  flex-shrink: 0; border: 1px solid;
}

.fname {
  font-size: 13px; color: var(--text); flex: 1;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--mono);
}

.dlbtn {
  background: var(--blue-lt);
  border: 1px solid var(--blue-mid);
  color: var(--blue);
  font-size: 11px; font-weight: 600;
  padding: 4px 10px; border-radius: 3px;
  cursor: pointer; transition: all .15s; flex-shrink: 0;
}
.dlbtn:hover { background: var(--blue); color: white; border-color: var(--blue); }

.empty-state { text-align: center; padding: 28px; color: var(--text3); font-size: 12px; }

/* ── EVENT LOG ── */
.log-panel {
  background: var(--sidebar-bg);
  border: 1px solid #2d3f55;
  border-radius: var(--radius);
  margin-bottom: 22px;
  overflow: hidden;
}

.log-hd {
  padding: 10px 16px;
  border-bottom: 1px solid #2d3f55;
  display: flex; align-items: center; justify-content: space-between;
}

.log-hd span {
  font-size: 11px; font-weight: 600;
  letter-spacing: 1.5px; text-transform: uppercase;
  color: #64748b;
}

.log-entries {
  max-height: 160px; overflow-y: auto;
  padding: 6px 0;
  font-family: var(--mono);
}
.log-entries::-webkit-scrollbar { width: 4px; }
.log-entries::-webkit-scrollbar-thumb { background: #2d3f55; }

.le {
  display: flex; gap: 12px;
  padding: 3px 16px; font-size: 11.5px; line-height: 1.6;
  opacity: 0; animation: logIn .25s forwards;
}
@keyframes logIn { to { opacity: 1; } }

.lt { color: #475569; min-width: 76px; flex-shrink: 0; }
.lok    { color: #4ade80; }
.ler    { color: #f87171; }
.lin    { color: #60a5fa; }
.lwn    { color: #fbbf24; }
.lrepl  { color: #2dd4bf; font-weight: 500; }
.lrecover { color: #fb923c; font-weight: 500; }

/* ── REPLICATION FLOW ── */
.repl-flow {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
  margin-bottom: 18px;
  display: none;
}
.repl-flow.show { display: block; }

.repl-flow-title {
  font-size: 11px; font-weight: 600; letter-spacing: 1px;
  text-transform: uppercase; color: var(--teal);
  margin-bottom: 12px; display: flex; align-items: center; gap: 6px;
}
.repl-flow-title::before {
  content: '';
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--teal); animation: blink .6s infinite; display: inline-block;
}

.flow-nodes {
  display: flex; align-items: center; justify-content: center; gap: 0;
}

.flow-node {
  display: flex; flex-direction: column; align-items: center; gap: 5px;
}

.flow-node-box {
  width: 80px; height: 36px;
  border-radius: 4px;
  border: 1.5px solid;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700; font-family: var(--mono);
  transition: all .3s;
}

.flow-node-box.leader   { border-color: var(--blue);  background: var(--blue-lt);  color: var(--blue); }
.flow-node-box.pending  { border-color: var(--border2); background: var(--bg); color: var(--text3); }
.flow-node-box.synced   { border-color: var(--teal);  background: var(--teal-lt);  color: var(--teal); }
.flow-node-box.offline  { border-color: var(--red-mid); background: var(--red-lt); color: var(--red); }

.flow-node-label { font-size: 10px; color: var(--text3); font-family: var(--mono); }

.flow-arrow {
  width: 60px; display: flex; align-items: center; justify-content: center;
  position: relative; flex-shrink: 0;
}

.flow-line {
  height: 2px; background: var(--border2); width: 100%; position: relative; overflow: hidden;
}

.flow-line-fill {
  position: absolute; top: 0; left: -100%; width: 100%; height: 100%;
  background: var(--teal);
  transition: left .5s ease;
}
.flow-arrow.active .flow-line-fill { left: 0; }
.flow-arrow-tip { font-size: 14px; color: var(--teal); position: absolute; right: -3px; }

/* ── TOAST ── */
.tc { position: fixed; bottom: 20px; right: 20px; z-index: 999; display: flex; flex-direction: column; gap: 8px; }

.toast {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 11px 14px; border-radius: var(--radius);
  font-size: 12.5px; min-width: 260px; max-width: 340px;
  box-shadow: 0 4px 16px rgba(0,0,0,.12);
  animation: tslide .3s ease;
  border: 1px solid var(--border);
  background: var(--surface);
}
@keyframes tslide { from{transform:translateY(12px);opacity:0} to{transform:translateY(0);opacity:1} }

.toast-ico { font-size: 14px; flex-shrink: 0; margin-top: 1px; }
.toast-body { flex: 1; }
.toast-title { font-weight: 600; margin-bottom: 1px; }
.toast-sub   { font-size: 11px; color: var(--text3); }

.toast.success .toast-ico { color: var(--green); }
.toast.success { border-left: 3px solid var(--green); }
.toast.error   .toast-ico { color: var(--red); }
.toast.error   { border-left: 3px solid var(--red); }
.toast.info    .toast-ico { color: var(--blue); }
.toast.info    { border-left: 3px solid var(--blue); }
.toast.warn    .toast-ico { color: var(--amber); }
.toast.warn    { border-left: 3px solid var(--amber); }
.toast.repl    .toast-ico { color: var(--teal); }
.toast.repl    { border-left: 3px solid var(--teal); }

/* ── MODAL ── */
.mo { position:fixed;inset:0;background:rgba(17,24,39,.45);backdrop-filter:blur(3px);z-index:100;display:none;align-items:center;justify-content:center; }
.mo.active { display:flex; }
.md { background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:22px;width:440px;max-width:92vw;box-shadow:0 12px 40px rgba(0,0,0,.18); }
.mdtop { display:flex;align-items:center;justify-content:space-between;margin-bottom:16px; }
.mdtitle { font-size:14px;font-weight:700; }
.mdx { background:var(--bg);border:1px solid var(--border);color:var(--text2);width:26px;height:26px;border-radius:4px;cursor:pointer;font-size:13px;display:flex;align-items:center;justify-content:center;transition:all .15s; }
.mdx:hover { background:var(--red-lt);border-color:var(--red-mid);color:var(--red); }
.kvrow { display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);font-size:12.5px; }
.kvrow:last-child { border-bottom:none; }
.kvk { color:var(--text2);font-weight:500; }
.kvv { color:var(--blue);font-weight:600;font-family:var(--mono);font-size:12px; }
.mdact { display:flex;gap:8px;margin-top:16px; }
.mdact select { margin-bottom:0;flex:1; }

@media(max-width:900px) {
  .node-grid { grid-template-columns:1fr; }
  .grid2 { grid-template-columns:1fr; }
}
</style>
</head>
<body>

<!-- SIDEBAR -->
<aside class="sidebar">
  <div class="sidebar-logo">
    <div class="product">DistFS Console</div>
    <div class="version">v1.0 · Python · Raft</div>
  </div>
  <div class="sidebar-section">
    <div class="sidebar-label">Cluster</div>
    <div class="nav-item active"><span class="nav-icon">▦</span> Overview</div>
    <div class="nav-item"><span class="nav-icon">⬡</span> Nodes</div>
    <div class="nav-item"><span class="nav-icon">⎇</span> Replication</div>
  </div>
  <div class="sidebar-section">
    <div class="sidebar-label">Storage</div>
    <div class="nav-item"><span class="nav-icon">↑</span> Upload</div>
    <div class="nav-item"><span class="nav-icon">≡</span> Files</div>
  </div>
  <div class="sidebar-section">
    <div class="sidebar-label">Monitoring</div>
    <div class="nav-item"><span class="nav-icon">◎</span> Events</div>
  </div>
  <div class="sidebar-footer">
    SE2062 · Group Assignment
  </div>
</aside>

<!-- MAIN -->
<div class="main">

  <!-- TOPBAR -->
  <div class="topbar">
    <div class="topbar-title">Cluster Overview</div>
    <div class="topbar-right">
      <div class="cluster-badge" id="cluster-badge">
        <span class="dot"></span>
        <span id="cluster-label">Checking...</span>
      </div>
      <div class="icon-btn" onclick="refreshStatus()" title="Refresh">↻</div>
    </div>
  </div>

  <!-- CONTENT -->
  <div class="content">

    <!-- NODES -->
    <div class="section-hd">
      <h2><div class="ico">⬡</div> Cluster Nodes</h2>
      <button class="gbtn" onclick="refreshStatus()">↻ Refresh</button>
    </div>

    <div class="node-grid">
      <!-- node1 -->
      <div class="node-card" id="card-node1">
        <div class="nc-topbar"></div>
        <div class="nc-body">
          <div class="nc-head">
            <span class="nc-id">node1</span>
            <span class="status-dot" id="dot-node1"></span>
          </div>
          <div><span class="role-tag Offline" id="role-node1">Checking</span></div>
          <div class="nc-meta">
            port <strong>5001</strong> · raft <strong>6001</strong><br>
            term <strong id="term-node1">—</strong> · leader <strong id="leader-node1">—</strong>
          </div>
          <div class="repl-bar-wrap" id="repl-node1">
            <div class="repl-bar-label">Replicating file...</div>
            <div class="repl-bar"><div class="repl-bar-fill" id="repl-fill-node1"></div></div>
          </div>
          <div class="recover-bar-wrap" id="recover-node1">
            <div class="recover-bar-label">Recovering files...</div>
            <div class="recover-bar"><div class="recover-bar-fill" id="recover-fill-node1"></div></div>
          </div>
          <div class="nc-btns">
            <button class="nbt start" id="start-node1" onclick="startNode('node1')" disabled>▶ Start</button>
            <button class="nbt stop"  id="stop-node1"  onclick="stopNode('node1')"  disabled>■ Stop</button>
          </div>
        </div>
      </div>

      <!-- node2 -->
      <div class="node-card" id="card-node2">
        <div class="nc-topbar"></div>
        <div class="nc-body">
          <div class="nc-head">
            <span class="nc-id">node2</span>
            <span class="status-dot" id="dot-node2"></span>
          </div>
          <div><span class="role-tag Offline" id="role-node2">Checking</span></div>
          <div class="nc-meta">
            port <strong>5002</strong> · raft <strong>6002</strong><br>
            term <strong id="term-node2">—</strong> · leader <strong id="leader-node2">—</strong>
          </div>
          <div class="repl-bar-wrap" id="repl-node2">
            <div class="repl-bar-label">Replicating file...</div>
            <div class="repl-bar"><div class="repl-bar-fill" id="repl-fill-node2"></div></div>
          </div>
          <div class="recover-bar-wrap" id="recover-node2">
            <div class="recover-bar-label">Recovering files...</div>
            <div class="recover-bar"><div class="recover-bar-fill" id="recover-fill-node2"></div></div>
          </div>
          <div class="nc-btns">
            <button class="nbt start" id="start-node2" onclick="startNode('node2')" disabled>▶ Start</button>
            <button class="nbt stop"  id="stop-node2"  onclick="stopNode('node2')"  disabled>■ Stop</button>
          </div>
        </div>
      </div>

      <!-- node3 -->
      <div class="node-card" id="card-node3">
        <div class="nc-topbar"></div>
        <div class="nc-body">
          <div class="nc-head">
            <span class="nc-id">node3</span>
            <span class="status-dot" id="dot-node3"></span>
          </div>
          <div><span class="role-tag Offline" id="role-node3">Checking</span></div>
          <div class="nc-meta">
            port <strong>5003</strong> · raft <strong>6003</strong><br>
            term <strong id="term-node3">—</strong> · leader <strong id="leader-node3">—</strong>
          </div>
          <div class="repl-bar-wrap" id="repl-node3">
            <div class="repl-bar-label">Replicating file...</div>
            <div class="repl-bar"><div class="repl-bar-fill" id="repl-fill-node3"></div></div>
          </div>
          <div class="recover-bar-wrap" id="recover-node3">
            <div class="recover-bar-label">Recovering files...</div>
            <div class="recover-bar"><div class="recover-bar-fill" id="recover-fill-node3"></div></div>
          </div>
          <div class="nc-btns">
            <button class="nbt start" id="start-node3" onclick="startNode('node3')" disabled>▶ Start</button>
            <button class="nbt stop"  id="stop-node3"  onclick="stopNode('node3')"  disabled>■ Stop</button>
          </div>
        </div>
      </div>
    </div>

    <!-- REPLICATION FLOW BANNER -->
    <div class="repl-flow" id="repl-flow">
      <div class="repl-flow-title">Live Replication in Progress</div>
      <div class="flow-nodes" id="flow-nodes">
        <!-- filled by JS -->
      </div>
    </div>

    <!-- MAIN GRID -->
    <div class="grid2">

      <!-- UPLOAD -->
      <div class="panel">
        <div class="panel-hd">
          <div class="ph-ico">↑</div>
          <span class="ph-title">Upload File</span>
        </div>
        <div class="panel-body">
          <div class="upload-zone" id="uz" onclick="document.getElementById('fi').click()">
            <div class="uz-icon">☁</div>
            <div class="uz-text">Drop a file here or <span>click to browse</span></div>
            <div class="uz-fname" id="uzfn"></div>
          </div>
          <input type="file" id="fi" onchange="onFile(event)">
          <div class="divider"></div>
          <input type="text" id="fname" placeholder="filename.txt  (for text content)">
          <textarea id="ftxt" placeholder="Or type text content here..."></textarea>
          <div class="irow">
            <select id="unode">
              <option value="node1">node1</option>
              <option value="node2">node2</option>
              <option value="node3">node3</option>
            </select>
            <button class="btn" onclick="doUpload()" id="ubtn">Upload</button>
          </div>
        </div>
      </div>

      <!-- FILES -->
      <div class="panel">
        <div class="panel-hd">
          <div class="ph-ico">≡</div>
          <span class="ph-title">Stored Files</span>
          <button class="gbtn" onclick="loadFiles()" style="margin-left:auto" id="rfbtn">↻ Refresh</button>
        </div>
        <div class="panel-body" style="padding:10px 8px">
          <div class="flist" id="flist">
            <div class="empty-state">No files stored yet</div>
          </div>
        </div>
      </div>

    </div>

    <!-- EVENT LOG -->
    <div class="log-panel">
      <div class="log-hd">
        <span>Event Log</span>
        <button class="gbtn" onclick="clearLog()" style="color:#475569;border-color:#2d3f55;font-size:10px">Clear</button>
      </div>
      <div class="log-entries" id="loge">
        <div class="le"><span class="lt">—</span><span class="lin">Console ready — start nodes to begin.</span></div>
      </div>
    </div>

  </div>
</div>

<!-- TOASTS -->
<div class="tc" id="tc"></div>

<!-- DOWNLOAD MODAL -->
<div class="mo" id="dlmodal">
  <div class="md">
    <div class="mdtop">
      <span class="mdtitle">Download File</span>
      <button class="mdx" onclick="closeModal()">✕</button>
    </div>
    <div id="mdcontent"></div>
    <div class="mdact">
      <select id="dlnode">
        <option value="node1">node1</option>
        <option value="node2">node2</option>
        <option value="node3">node3</option>
      </select>
      <button class="btn" onclick="doDownload()" id="dlbtn2">Download</button>
    </div>
  </div>
</div>

<script>
let selFile = null, dlFile = null, prev = {};
const NODES = ['node1','node2','node3'];

window.onload = () => {
  refreshStatus();
  loadFiles();
  pollEvents();
  setInterval(refreshStatus, 3000);
  setInterval(loadFiles, 8000);
  setupDrop();
};

// ─── DRAG DROP ───
function setupDrop() {
  const z = document.getElementById('uz');
  z.addEventListener('dragover', e => { e.preventDefault(); z.classList.add('over'); });
  z.addEventListener('dragleave', () => z.classList.remove('over'));
  z.addEventListener('drop', e => {
    e.preventDefault(); z.classList.remove('over');
    if (e.dataTransfer.files.length) {
      selFile = e.dataTransfer.files[0];
      document.getElementById('uzfn').textContent = '📎 ' + selFile.name;
    }
  });
}
function onFile(e) {
  selFile = e.target.files[0];
  if (selFile) document.getElementById('uzfn').textContent = '📎 ' + selFile.name;
}

// ─── STATUS ───
async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    updateCards(await r.json());
  } catch(e) {}
}

function updateCards(data) {
  let online = 0;
  for (const [nid, info] of Object.entries(data)) {
    const card   = document.getElementById(`card-${nid}`);
    const dot    = document.getElementById(`dot-${nid}`);
    const roleEl = document.getElementById(`role-${nid}`);
    const termEl = document.getElementById(`term-${nid}`);
    const ldrEl  = document.getElementById(`leader-${nid}`);
    const sBtn   = document.getElementById(`start-${nid}`);
    const xBtn   = document.getElementById(`stop-${nid}`);
    if (!card) continue;

    const p = prev[nid] || {};

    if (!info.online) {
      card.className = 'node-card offline';
      dot.className  = 'status-dot offline';
      roleEl.textContent = 'Offline'; roleEl.className = 'role-tag Offline';
      termEl.textContent = '—'; ldrEl.textContent = '—';
      sBtn.disabled = false; xBtn.disabled = true;

      if (p.online) {
        addLog('wn', `${nid} went offline — remaining nodes will trigger re-election`);
        toast('Node offline', `${nid} is down. Re-election starting.`, 'warn', '⚠');
      }

      // show recovery bar when it comes back
      document.getElementById(`repl-${nid}`).classList.remove('show');

    } else {
      online++;
      const role = info.raft?.role || 'Unknown';
      card.className = `node-card ${role === 'Leader' ? 'leader' : 'healthy'}`;
      dot.className  = `status-dot ${role === 'Leader' ? 'leader' : 'healthy'}`;
      roleEl.textContent = role; roleEl.className = `role-tag ${role}`;
      termEl.textContent = info.raft?.term ?? '—';
      ldrEl.textContent  = info.raft?.leader_id ?? '—';
      sBtn.disabled = true; xBtn.disabled = false;

      if (!p.online && p.online !== undefined) {
        addLog('recover', `${nid} back online — syncing files from peers`);
        toast('Node recovered', `${nid} rejoined. Recovery in progress.`, 'info', '↺');
        showRecovery(nid);
      }

      if (p.role !== 'Leader' && role === 'Leader') {
        addLog('wn', `⚡ ${nid} elected as LEADER (term ${info.raft?.term})`);
        toast('New leader elected', `${nid} is now the Raft leader (term ${info.raft?.term})`, 'info', '⭐');
      }
    }
    prev[nid] = { online: info.online, role: info.raft?.role };
  }

  // cluster badge
  const badge = document.getElementById('cluster-badge');
  const label = document.getElementById('cluster-label');
  if (online === 3) {
    badge.className = 'cluster-badge'; label.textContent = 'Cluster Healthy · 3/3 nodes';
  } else if (online > 0) {
    badge.className = 'cluster-badge degraded'; label.textContent = `Degraded · ${online}/3 nodes`;
  } else {
    badge.className = 'cluster-badge down'; label.textContent = 'Cluster Down · 0/3 nodes';
  }
}

// ─── REPLICATION ANIMATION ───
function showReplication(leaderNode) {
  const followers = NODES.filter(n => n !== leaderNode);

  // show flow banner
  const flow = document.getElementById('repl-flow');
  const fnodes = document.getElementById('flow-nodes');
  flow.classList.add('show');

  // build flow diagram
  fnodes.innerHTML = '';
  const allNodes = [leaderNode, ...followers];
  allNodes.forEach((nid, i) => {
    const isLeader = i === 0;
    const box = document.createElement('div');
    box.className = 'flow-node';
    box.innerHTML = `
      <div class="flow-node-box ${isLeader ? 'leader' : 'pending'}" id="flow-box-${nid}">${nid}</div>
      <div class="flow-node-label">${isLeader ? '★ leader' : 'follower'}</div>`;
    fnodes.appendChild(box);

    if (!isLeader && i < allNodes.length) {
      const arrow = document.createElement('div');
      arrow.className = 'flow-arrow';
      arrow.id = `flow-arrow-${nid}`;
      arrow.innerHTML = `<div class="flow-line"><div class="flow-line-fill"></div></div>`;
      fnodes.insertBefore(arrow, box);
    }
  });

  // show repl bars on follower nodes
  followers.forEach((nid, i) => {
    const onlineEl = document.getElementById(`dot-${nid}`);
    if (!onlineEl || onlineEl.className.includes('offline')) return;

    const wrap = document.getElementById(`repl-${nid}`);
    const fill = document.getElementById(`repl-fill-${nid}`);
    const card = document.getElementById(`card-${nid}`);

    wrap.classList.add('show');
    card.classList.add('replicating');
    fill.style.width = '0%';

    // animate progress
    setTimeout(() => {
      document.getElementById(`flow-arrow-${nid}`)?.classList.add('active');
      fill.style.width = '60%';
    }, 200 + i * 300);

    setTimeout(() => {
      fill.style.width = '100%';
      document.getElementById(`flow-box-${nid}`).className = 'flow-node-box synced';
    }, 800 + i * 300);

    setTimeout(() => {
      wrap.classList.remove('show');
      card.classList.remove('replicating');
      card.className = card.className.replace('replicating', '');
    }, 2000 + i * 300);
  });

  // hide flow after animation
  setTimeout(() => {
    flow.classList.remove('show');
    fnodes.innerHTML = '';
  }, 3500);
}

// ─── RECOVERY ANIMATION ───
function showRecovery(nid) {
  const wrap = document.getElementById(`recover-${nid}`);
  const fill = document.getElementById(`recover-fill-${nid}`);
  const card = document.getElementById(`card-${nid}`);

  wrap.classList.add('show');
  card.classList.add('recovering');
  fill.style.width = '0%';

  // simulate recovery progress
  const steps = [15, 40, 65, 85, 100];
  steps.forEach((pct, i) => {
    setTimeout(() => { fill.style.width = pct + '%'; }, i * 600);
  });

  setTimeout(() => {
    wrap.classList.remove('show');
    card.classList.remove('recovering');
    addLog('recover', `${nid} recovery complete — all files synced`);
    toast('Recovery complete', `${nid} files fully synced`, 'success', '✓');
    loadFiles();
  }, steps.length * 600 + 400);
}

// ─── NODE CONTROLS ───
async function stopNode(nid) {
  const b = document.getElementById(`stop-${nid}`);
  b.disabled = true; b.textContent = '...';
  try {
    const r = await fetch(`/api/node/${nid}/stop`, { method: 'POST' });
    const d = await r.json();
    if (d.status === 'ok') {
      addLog('wn', `${nid} stopped — Raft re-election will begin`);
    } else {
      toast('Error', d.error || 'Failed to stop', 'error', '✗');
      b.disabled = false; b.textContent = '■ Stop';
    }
  } catch(e) { b.disabled = false; b.textContent = '■ Stop'; }
  setTimeout(refreshStatus, 800);
}

async function startNode(nid) {
  const b = document.getElementById(`start-${nid}`);
  b.disabled = true; b.textContent = '...';
  try {
    const r = await fetch(`/api/node/${nid}/start`, { method: 'POST' });
    const d = await r.json();
    if (d.status === 'ok') {
      addLog('ok', `${nid} starting (pid ${d.pid})`);
    } else {
      toast('Error', d.error || 'Failed to start', 'error', '✗');
      b.disabled = false; b.textContent = '▶ Start';
    }
  } catch(e) { b.disabled = false; b.textContent = '▶ Start'; }
  setTimeout(refreshStatus, 2000);
}

// ─── UPLOAD ───
async function doUpload() {
  const b = document.getElementById('ubtn');
  const nid = document.getElementById('unode').value;
  b.disabled = true; b.textContent = 'Uploading...';
  try {
    const fd = new FormData(); fd.append('node', nid);
    if (selFile) {
      fd.append('file', selFile);
    } else {
      const fn = document.getElementById('fname').value.trim();
      const tx = document.getElementById('ftxt').value.trim();
      if (!fn || !tx) { toast('Missing fields', 'Enter filename and content', 'warn', '⚠'); return; }
      fd.append('filename', fn); fd.append('text', tx);
    }
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.status === 'ok') {
      const leader = d.raft_leader || nid;
      toast('Upload complete', `Lamport: ${d.lamport_clock} · Leader: ${leader} · Committed: ${d.committed}`, 'success', '✓');
      addLog('repl', `File uploaded to ${leader} — replicating to all nodes (Lamport: ${d.lamport_clock})`);
      showReplication(leader);
      selFile = null;
      ['uzfn','fname','ftxt'].forEach(id => {
        const el = document.getElementById(id);
        if (el.tagName === 'DIV') el.textContent = ''; else el.value = '';
      });
      document.getElementById('fi').value = '';
      setTimeout(loadFiles, 1500);
    } else {
      toast('Upload failed', d.error || 'All nodes unreachable', 'error', '✗');
      addLog('er', `Upload failed: ${d.error}`);
    }
  } catch(e) { toast('Error', e.message, 'error', '✗'); }
  finally { b.disabled = false; b.textContent = 'Upload'; }
}

// ─── FILES ───
const EXT_COLORS = {
  PDF:['#dc2626','#fee2e2'], PNG:['#7c3aed','#ede9fe'], JPG:['#db2777','#fce7f3'],
  JPEG:['#db2777','#fce7f3'], TXT:['#1d6ae5','#e8f0fd'], MP4:['#d97706','#fef3c7'],
  MP3:['#059669','#d1fae5'], DOCX:['#1d4ed8','#dbeafe'], DOC:['#1d4ed8','#dbeafe'],
  CSV:['#047857','#d1fae5'], ZIP:['#6b7280','#f3f4f6'], PY:['#0284c7','#e0f2fe'],
};

async function loadFiles() {
  try {
    const r = await fetch('/api/files');
    const d = await r.json();
    renderFiles(d.files || []);
  } catch(e) {}
}

function renderFiles(files) {
  const el = document.getElementById('flist');
  if (!files.length) {
    el.innerHTML = '<div class="empty-state">No files stored yet</div>'; return;
  }
  el.innerHTML = files.map(f => {
    const ext = f.split('.').pop().toUpperCase();
    const [tc, bg] = EXT_COLORS[ext] || ['#1d6ae5','#e8f0fd'];
    return `<div class="fi">
      <div class="fext" style="background:${bg};color:${tc};border-color:${tc}30">${ext.slice(0,3)}</div>
      <span class="fname" title="${f}">${f}</span>
      <button class="dlbtn" onclick="openDl('${f}')">↓ Fetch</button>
    </div>`;
  }).join('');
}

// ─── DOWNLOAD ───
function openDl(f) {
  dlFile = f;
  document.getElementById('mdcontent').innerHTML = `
    <div class="kvrow"><span class="kvk">Filename</span><span class="kvv">${f}</span></div>
    <div class="kvrow"><span class="kvk">Source node</span><span class="kvv">Select below</span></div>`;
  document.getElementById('dlmodal').classList.add('active');
}
function closeModal() { document.getElementById('dlmodal').classList.remove('active'); dlFile = null; }

async function doDownload() {
  if (!dlFile) return;
  const nid = document.getElementById('dlnode').value;
  const b = document.getElementById('dlbtn2');
  b.disabled = true; b.textContent = 'Fetching...';
  try {
    const r = await fetch(`/api/download/${encodeURIComponent(dlFile)}?node=${nid}`);
    const d = await r.json();
    if (d.status === 'ok') {
      const ts = new Date(d.timestamp * 1000).toLocaleTimeString();
      document.getElementById('mdcontent').innerHTML = `
        <div class="kvrow"><span class="kvk">Filename</span><span class="kvv">${d.filename}</span></div>
        <div class="kvrow"><span class="kvk">Timestamp</span><span class="kvv">${ts}</span></div>
        <div class="kvrow"><span class="kvk">Raft Leader</span><span class="kvv">${d.raft_leader}</span></div>
        <div class="kvrow"><span class="kvk">Type</span><span class="kvv">${d.is_binary ? 'Binary' : 'Text'}</span></div>
        ${!d.is_binary ? `<div class="kvrow"><span class="kvk">Preview</span><span class="kvv" style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${(d.data||'').slice(0,55)}${(d.data||'').length>55?'…':''}</span></div>` : ''}`;
      if (d.is_binary) {
        const bytes = atob(d.data); const arr = new Uint8Array(bytes.length);
        for (let i=0;i<bytes.length;i++) arr[i]=bytes.charCodeAt(i);
        const url = URL.createObjectURL(new Blob([arr]));
        const a = document.createElement('a'); a.href=url; a.download=d.filename; a.click();
        URL.revokeObjectURL(url);
      }
      toast('Download complete', `${d.filename} from ${nid}`, 'success', '✓');
      addLog('ok', `Downloaded '${d.filename}' from ${nid} — ts: ${ts}`);
    } else {
      toast('Not found', d.error || 'File not found', 'error', '✗');
    }
  } catch(e) { toast('Error', e.message, 'error', '✗'); }
  finally { b.disabled=false; b.textContent='Download'; }
}

// ─── EVENT LOG ───
let lastEventTs = 0;

async function pollEvents() {
  try {
    const r = await fetch('/api/events');
    const events = await r.json();
    events.forEach(ev => {
      if (ev.ts > lastEventTs) {
        lastEventTs = ev.ts;
        const cls = { ok:'lok', warn:'lwn', error:'ler', repl:'lrepl', recover:'lrecover' }[ev.kind] || 'lin';
        const t = new Date(ev.ts * 1000).toLocaleTimeString();
        const el = document.getElementById('loge');
        const e = document.createElement('div'); e.className='le';
        e.innerHTML = `<span class="lt">${t}</span><span class="${cls}">${ev.msg}</span>`;
        el.insertBefore(e, el.firstChild);
        while (el.children.length > 80) el.removeChild(el.lastChild);
      }
    });
  } catch(e) {}
  setTimeout(pollEvents, 2000);
}

function addLog(type, msg) {
  const el = document.getElementById('loge');
  const t = new Date().toLocaleTimeString();
  const cls = {ok:'lok',er:'ler',in:'lin',wn:'lwn',repl:'lrepl',recover:'lrecover'}[type]||'lin';
  const e = document.createElement('div'); e.className='le';
  e.innerHTML = `<span class="lt">${t}</span><span class="${cls}">${msg}</span>`;
  el.insertBefore(e, el.firstChild);
  while (el.children.length > 80) el.removeChild(el.lastChild);
}
function clearLog() { document.getElementById('loge').innerHTML=''; }

// ─── TOAST ───
function toast(title, sub, type='info', icon='i') {
  const c = document.getElementById('tc');
  const t = document.createElement('div'); t.className = `toast ${type}`;
  t.innerHTML = `<span class="toast-ico">${icon}</span><div class="toast-body"><div class="toast-title">${title}</div><div class="toast-sub">${sub}</div></div>`;
  c.appendChild(t);
  setTimeout(() => { t.style.opacity='0'; t.style.transition='opacity .3s'; setTimeout(()=>t.remove(), 300); }, 4000);
}
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

if __name__ == "__main__":
    print("DistFS Console starting...")
    print("Open: http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)