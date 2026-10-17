#!/usr/bin/env python3
"""Portage PC des outils de l'app Android philo-agent-harness (meme protocole
OUTIL: x / ARGS: cle=valeur que ReasoningController.kt), pour piloter le
llama.cpp du 1080 avec le meme jeu d'outils que l'app.

Outils lecture seule (jamais de confirmation) : grep, memory_read, git_status, git_diff
Outils ecriture (confirmation requise avant execution reelle) : write_file, memory_write
"""
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

WORKSPACE = Path(r"E:\lama-tensorRT 1050-5070")
MEMORY_DIR = WORKSPACE / ".harness_memory"
MEMORY_DIR.mkdir(exist_ok=True)

READ_ONLY_TOOLS = {"grep", "memory_read", "git_status", "git_diff", "skill_discover", "skill_verify_claim"}
WRITE_TOOLS = {"write_file", "memory_write"}
IGNORE_DIRS = {".git", "node_modules", "__pycache__", "build", ".venv", "venv"}


def _confined(path_str):
    """Meme garde-fou que WorkspaceGrepExecutor.kt/Workspace.inside() :
    refuse tout chemin qui sort du workspace, jamais une confiance aveugle."""
    p = (WORKSPACE / path_str).resolve() if not Path(path_str).is_absolute() else Path(path_str).resolve()
    try:
        p.relative_to(WORKSPACE.resolve())
    except ValueError:
        return None
    return p


def tool_grep(pattern, extensions=None, max_hits=100, max_files=4000):
    """BUG REEL TROUVE EN TESTANT (meme session) : la premiere version n'avait
    aucune limite de fichiers scannes -- ce workspace contient 2 arbres source
    llama.cpp complets (des dizaines de milliers de fichiers), le process est
    reste bloque plusieurs minutes. Meme garde-fou que WorkspaceGrepExecutor.kt
    (maxFiles) -- jamais oublie une deuxieme fois."""
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"error": f"regex invalide: {e}"}
    exts = [e.strip().lstrip(".") for e in extensions.split(",")] if extensions else None
    hits, scanned, truncated = [], 0, False
    for p in WORKSPACE.rglob("*"):
        if scanned >= max_files:
            truncated = True
            break
        if p.is_dir() or any(part in IGNORE_DIRS for part in p.parts):
            continue
        if exts and p.suffix.lstrip(".").lower() not in exts:
            continue
        if p.stat().st_size > 2_000_000:
            continue
        scanned += 1
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if len(hits) >= max_hits:
                break
            if rx.search(line):
                hits.append({"path": str(p.relative_to(WORKSPACE)), "line": i, "text": line.strip()[:200]})
    return {"hits": hits, "files_scanned": scanned, "truncated": truncated}


def tool_memory_read(slug=None):
    if slug:
        f = MEMORY_DIR / f"{slug}.md"
        return {"content": f.read_text(encoding="utf-8")} if f.exists() else {"error": "note introuvable"}
    notes = sorted(p.stem for p in MEMORY_DIR.glob("*.md"))
    return {"notes": notes}


def tool_memory_write(slug, title, content):
    if not slug or not re.match(r"^[a-z0-9_-]+$", slug):
        return {"error": "slug invalide (minuscules/chiffres/-/_ seulement)"}
    f = MEMORY_DIR / f"{slug}.md"
    f.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
    return {"written": str(f)}


def tool_git_status():
    r = subprocess.run(["git", "status", "--short"], cwd=str(WORKSPACE),
                       capture_output=True, text=True, timeout=10)
    return {"output": r.stdout or "(aucun changement)", "returncode": r.returncode}


def tool_git_diff():
    r = subprocess.run(["git", "diff"], cwd=str(WORKSPACE),
                       capture_output=True, text=True, timeout=10)
    return {"output": r.stdout or "(aucun diff)", "returncode": r.returncode}


def tool_write_file(path, content):
    """ECRITURE reelle. L'appelant doit avoir deja obtenu confirmation --
    cette fonction ne redemande rien, meme discipline que FileEditExecutor.kt
    (le controleur gate, pas l'executeur)."""
    p = _confined(path)
    if p is None:
        return {"error": f"chemin hors workspace: {path}"}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"written": str(p), "bytes": len(content.encode("utf-8"))}


SKILL_SCRIPTS = r"E:\oneplus\.agents\skills\git-ecosystem-investigator-claude\scripts"


def tool_skill_discover(symptom):
    """Integration reelle du skill git-ecosystem-investigator-claude (deja
    teste en reseau reel cette session) : decouvre depot+signature a partir
    d'un symptome seul, local puis GitHub global."""
    out = str(MEMORY_DIR / "_skill_discover.json")
    r = subprocess.run(["python", f"{SKILL_SCRIPTS}\\discover_target.py",
                        "--workspace-root", "E:\\", "--symptom", symptom, "--out", out],
                       capture_output=True, text=True, timeout=30)
    try:
        return json.loads(Path(out).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"error": "echec discover_target.py", "stderr": r.stderr[-500:]}


def tool_skill_verify_claim(statement, defect_signature, repo, residual_signature=None):
    """Integration reelle du skill claim_verifier.py + evidence_fetcher.py :
    recupere de vraies preuves GitHub et rend un verdict a 4 statuts, jamais
    fabrique sans preuve (meme chaine que le cas kan-linux/ggml-hexagon
    valide cette session)."""
    ev_out = str(MEMORY_DIR / "_skill_evidence.json")
    r1 = subprocess.run(["python", f"{SKILL_SCRIPTS}\\evidence_fetcher.py",
                         "--repo", repo, "--signature", defect_signature, "--out", ev_out],
                        capture_output=True, text=True, timeout=30)
    if not Path(ev_out).exists():
        return {"error": "echec evidence_fetcher.py", "stderr": r1.stderr[-500:]}
    claim = {"statement": statement, "defect_signature": defect_signature}
    if residual_signature:
        claim["residual_signature"] = residual_signature
    claim_path = MEMORY_DIR / "_skill_claim.json"
    claim_path.write_text(json.dumps(claim), encoding="utf-8")
    verdict_out = str(MEMORY_DIR / "_skill_verdict.json")
    subprocess.run(["python", f"{SKILL_SCRIPTS}\\claim_verifier.py", "check",
                    "--claim", str(claim_path), "--evidence", ev_out, "--out", verdict_out],
                   capture_output=True, text=True, timeout=15)
    try:
        return json.loads(Path(verdict_out).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"error": "echec claim_verifier.py"}


TOOLS = {
    "grep": tool_grep, "memory_read": tool_memory_read, "memory_write": tool_memory_write,
    "git_status": tool_git_status, "git_diff": tool_git_diff, "write_file": tool_write_file,
    "skill_discover": tool_skill_discover, "skill_verify_claim": tool_skill_verify_claim,
}


def parse_tool_request(response):
    """Meme format que ReasoningController.parseToolRequest : 'OUTIL: nom' +
    'ARGS: cle=valeur,cle2=valeur2' optionnel."""
    tool_line = next((l for l in response.splitlines() if l.strip().upper().startswith("OUTIL:")), None)
    if not tool_line:
        return None
    name = tool_line.split(":", 1)[1].strip()
    args_line = next((l for l in response.splitlines() if l.strip().upper().startswith("ARGS:")), None)
    args = {}
    if args_line:
        for pair in args_line.split(":", 1)[1].split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                args[k.strip()] = v.strip()
    return name, args


def dispatch(name, args, confirmed_write=False):
    if name not in TOOLS:
        return {"error": f"outil '{name}' inconnu"}
    if name in WRITE_TOOLS and not confirmed_write:
        return {"requires_confirmation": True, "tool": name, "args": args}
    try:
        return TOOLS[name](**args)
    except TypeError as e:
        return {"error": f"arguments invalides pour {name}: {e}"}


if __name__ == "__main__":
    import sys
    print(json.dumps(dispatch(sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:])), indent=2, ensure_ascii=False))
