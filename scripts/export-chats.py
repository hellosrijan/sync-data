"""Export every AI chat session for this repo to readable markdown, one file per session.

Sources:
  - Claude Code: ~/.claude/projects/<repo path, non-alphanumerics -> '-'>/*.jsonl
  - opencode:    `opencode session list/export` (skipped if opencode isn't installed)

Output: <out_dir>/<YYYY-MM-DD>-<id8>.md (Claude) and <YYYY-MM-DD>-opencode-<id8>.md (opencode).
Re-running overwrites, so a growing session is refreshed. Content is verbatim. The only edit
is redacting .env values whose key contains SECRET/PASSWORD/TOKEN/KEY.

usage: python3 scripts/export-chats.py [out_dir]   (default: docs/chats, run from repo root)
"""
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
out = Path(sys.argv[1]) if len(sys.argv) > 1 else repo / "docs/chats"
out.mkdir(parents=True, exist_ok=True)

# Redact secret-looking .env values wherever they appear.
secrets = {}
env = repo / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        k, _, v = line.partition("=")
        if re.search(r"SECRET|PASSWORD|TOKEN|KEY", k) and len(v.strip()) >= 8:
            secrets[v.strip()] = f"<redacted:{k.strip()}>"


def redact(s):
    for v, r in secrets.items():
        s = s.replace(v, r)
    return s


def fenced(text, lang=""):
    ticks = "`" * max(3, max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def tool_call(name, inp):
    label = inp.get("description") or inp.get("file_path") or inp.get("filePath") or inp.get("pattern") or ""
    if name in ("Bash", "shell") and "command" in inp:
        body, lang = inp["command"], "sh"
    else:
        body, lang = json.dumps(inp, indent=2, ensure_ascii=False), "json"
    return f"<details><summary>Tool: {name} {label}</summary>\n\n{fenced(body, lang)}\n\n</details>"


def tool_result(text, error=False):
    err = " (error)" if error else ""
    return f"<details><summary>Result{err}</summary>\n\n{fenced(text)}\n\n</details>"


def write(dest, title, source, sid, started, parts):
    if not any(p.startswith("## User") for p in parts):
        return
    header = (f"# {title or 'Session'}\n\n{source} session `{sid}`, started {started}. "
              "Exported verbatim; tool calls and results are collapsed.\n")
    dest.write_text(redact("\n\n".join([header, *parts])) + "\n")
    print(dest)


# ---------------------------------------------------------------- Claude Code

def clean_user(text):
    """Drop harness-injected wrappers, keep what the user typed or pasted."""
    text = re.sub(r"(?s)<system-reminder>.*?</system-reminder>", "", text)
    text = re.sub(r"(?s)<local-command-(stdout|stderr|caveat)>.*?</local-command-\1>", "", text)
    text = re.sub(r"(?s)<command-message>.*?</command-message>|<command-args>\s*</command-args>", "", text)
    text = re.sub(r"<command-name>(.*?)</command-name>", r"_ran `\1`_", text)
    text = re.sub(r"<command-args>(.*?)</command-args>", r"`\1`", text)
    text = re.sub(r"</?pasted_content[^>]*>", "", text)
    return text.strip()


def result_text(c):
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text", f"[{x.get('type')}]") for x in c)


sessions = Path.home() / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", str(repo))
for path in sorted(sessions.glob("*.jsonl")):
    parts, first_ts, title = [], None, None
    for line in path.open():
        d = json.loads(line)
        if d.get("type") == "ai-title":
            title = d.get("aiTitle")
        if d.get("type") not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
            continue
        first_ts = first_ts or d["timestamp"]
        content = d["message"]["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for c in content:
            t = c.get("type")
            if d["type"] == "user" and t == "text":
                text = clean_user(c["text"])
                if text:
                    parts.append(f"## User\n\n{text}")
            elif t == "text" and c["text"].strip():
                parts.append(f"## Claude\n\n{c['text'].strip()}")
            elif t == "tool_use":
                parts.append(tool_call(c["name"], c["input"]))
            elif t == "tool_result":
                parts.append(tool_result(result_text(c.get("content", "")), c.get("is_error")))
    if first_ts:
        day = datetime.fromisoformat(first_ts.replace("Z", "+00:00")).astimezone().date()
        write(out / f"{day}-{path.stem[:8]}.md", title, "Claude Code", path.stem, first_ts, parts)

# ---------------------------------------------------------------- opencode


def opencode(*args):
    return subprocess.run(["opencode", *args], cwd=repo, capture_output=True, text=True,
                          timeout=120, check=True).stdout


if shutil.which("opencode") is None:
    print("opencode not installed; skipped opencode sessions", file=sys.stderr)
else:
    for s in json.loads(opencode("session", "list", "--format", "json", "-n", "1000") or "[]"):
        if Path(s.get("directory", "")).resolve() != repo:
            continue
        d = json.loads(opencode("session", "export", s["id"]))
        parts = []
        for m in d.get("messages", []):
            if m.get("type") == "user" and (m.get("text") or "").strip():
                parts.append(f"## User\n\n{m['text'].strip()}")
            elif m.get("type") == "assistant":
                for p in m.get("content") or []:
                    if p.get("type") == "text" and p.get("text", "").strip():
                        parts.append(f"## Assistant ({m.get('model', {}).get('id', '?')})\n\n{p['text'].strip()}")
                    elif p.get("type") == "tool":
                        st = p.get("state", {})
                        parts.append(tool_call(p.get("name", "?"), st.get("input", {})))
                        parts.append(tool_result(result_text(st.get("content", "")),
                                                 st.get("status") == "error"))
        started = datetime.fromtimestamp(s["created"] / 1000).astimezone()
        sid = s["id"].removeprefix("ses_")
        write(out / f"{started.date()}-opencode-{sid[:8]}.md", s.get("title"), "opencode",
              s["id"], started.isoformat(timespec="seconds"), parts)
