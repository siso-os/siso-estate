#!/usr/bin/env python3
"""The estate as an MCP server (goal-2050 P3, reform R3): agents ask the city instead of searching the disk.

  python3 tools/mcp_server.py        stdio MCP server (JSON-RPC 2.0, one message per line); no dependencies

Tools: estate_where (words or a sentence -> the ranked places), estate_path (the one best path), estate_owner (who keeps
a path: its building, seat, lifecycle, postcode, from the register), estate_running (what `estate run` started).
Every answer comes from the same code `estate where` and the register use, so the MCP and the CLI never disagree.
Install (the agent stack owns harness config):
  claude mcp add -s user estate -- python3 ~/SISO_Workspace/SISO_Agents/siso-estate/tools/mcp_server.py
  codex: [mcp_servers.estate] command = "python3", args = ["<same path>"]  in ~/.codex/config.toml
"""
import importlib.machinery, importlib.util, io, json, os, sys, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.expanduser("~/SISO_Workspace")
HOME = os.path.expanduser("~")


def _estate():
    loader = importlib.machinery.SourceFileLoader("estate_cli", os.path.join(REPO, "bin", "estate"))
    spec = importlib.util.spec_from_loader("estate_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


E = _estate()


def tilde(p):
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


def where(q, n=5):
    out = []
    for c in E.where(q, n):
        p = os.path.realpath(c["path"]) if os.path.exists(c["path"]) else c["path"]
        line = {"path": tilde(p), "kind": c.get("kind", "")}
        if c.get("github"):
            line["github"] = c["github"]
        if c.get("alias"):
            line["alias"] = c["alias"]
        out.append(line)
    return out


def owner(path):
    p = os.path.realpath(os.path.expanduser(path))
    rel = os.path.relpath(p, WS) if p.startswith(WS + "/") else None
    if rel is None:
        return {"path": tilde(p), "note": "outside ~/SISO_Workspace: see plan/home-zones.json"}
    best = None
    for b in E.register().get("buildings", []):
        if rel == b["path"] or rel.startswith(b["path"] + "/"):
            if best is None or len(b["path"]) > len(best["path"]):
                best = b
    if not best:
        return {"path": tilde(p), "note": "on no building of the register; `estate where` for its district"}
    keep = ("path", "postcode", "seat", "lifecycle", "provenance", "island", "compound", "partner", "client")
    return {k: best[k] for k in keep if best.get(k)}


def running():
    try:
        return json.load(open(os.path.join(REPO, "machines", E.machine(), "running.json")))
    except (OSError, ValueError):
        return {}


TOOLS = [
    {"name": "estate_where", "description": "Find where anything lives on Shaan's machines (a repo, a client, a product, "
     "where new things go, where keys, worktrees, archives or transcripts are). Words or a whole question work. Ask this "
     "before searching the disk.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}},
                                                  "required": ["query"]}},
    {"name": "estate_path", "description": "The single best absolute path for a name or question (estate path).",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "estate_owner", "description": "Who keeps a path: its building, keeper seat, lifecycle and GitHub postcode.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "estate_running", "description": "Dev servers started with `estate run`: project, url, pid, and which agent "
     "started them.", "inputSchema": {"type": "object", "properties": {}}},
]


def call(name, args):
    with contextlib.redirect_stdout(io.StringIO()):     # the CLI code prints; stdout is the protocol
        if name == "estate_where":
            res = where(args.get("query", ""))
            return res or {"note": "nothing found; try fewer words"}
        if name == "estate_path":
            res = where(args.get("query", ""), 1)
            return res[0]["path"] if res else "nothing found"
        if name == "estate_owner":
            return owner(args.get("path", ""))
        if name == "estate_running":
            return running() or {"note": "nothing started by estate run is running"}
    raise KeyError(name)


def reply(i, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": i}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            m = json.loads(line)
        except ValueError:
            reply(None, error={"code": -32700, "message": "parse error"})
            continue
        method, i = m.get("method"), m.get("id")
        if i is None:                                    # a notification (initialized, cancelled): nothing to answer
            continue
        if method == "initialize":
            reply(i, {"protocolVersion": m.get("params", {}).get("protocolVersion", "2024-11-05"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "estate", "version": "1.0"}})
        elif method == "tools/list":
            reply(i, {"tools": TOOLS})
        elif method == "tools/call":
            p = m.get("params", {})
            try:
                out = call(p.get("name"), p.get("arguments") or {})
                text = out if isinstance(out, str) else json.dumps(out, indent=1)
                reply(i, {"content": [{"type": "text", "text": text}]})
            except KeyError:
                reply(i, error={"code": -32602, "message": f"unknown tool {p.get('name')}"})
            except Exception as e:                       # one bad call never kills the server
                reply(i, {"content": [{"type": "text", "text": f"error: {e!r}"}], "isError": True})
        elif method == "ping":
            reply(i, {})
        else:
            reply(i, error={"code": -32601, "message": f"method not found: {method}"})


if __name__ == "__main__":
    main()
