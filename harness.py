#!/usr/bin/env python3
"""Harnais minimal qui utilise l'API llama.cpp (OpenAI-compatible) du serveur
local deja lance (llama_launcher.exe, http://127.0.0.1:8080). Ne lance rien,
ne choisit pas de modele -- utilise celui deja charge par le serveur.

Usage:
  python harness.py "ta question"              # une reponse
  python harness.py                             # mode conversation (boucle)
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bm25  # noqa: E402
import tools as harness_tools  # noqa: E402

URL = "http://127.0.0.1:8080/v1/chat/completions"
SKILL_DISCOVER = r"E:\oneplus\.agents\skills\git-ecosystem-investigator-claude\scripts\discover_target.py"
BM25_INDEX = str(Path(__file__).resolve().parent / "bm25_index.json")
_bm25_docs = None


def rag_context(query, top=3):
    """Vraie recherche BM25 sur les rapports/docs locaux (665 fichiers indexes),
    pas une simulation -- utilise bm25.py construit et teste cette session."""
    global _bm25_docs
    if _bm25_docs is None:
        try:
            _bm25_docs = json.loads(Path(BM25_INDEX).read_text(encoding="utf-8"))
        except OSError:
            _bm25_docs = []
    hits = bm25.bm25_search(_bm25_docs, query, top=top)
    if not hits:
        return None
    return "\n\n".join(f"[{h['path']}] {h['excerpt']}" for h in hits)


def ask(messages, max_tokens=512):
    """Le vrai correctif au <think> force (README #5) n'est PAS cote client
    (gonfler max_tokens gaspille du temps GPU) mais cote serveur :
    --reasoning-budget 0 force la fin immediate du bloc think. Teste ce
    soir : 5.4s de bout en bout avec ce flag, contre une derive de 2048
    tokens de raisonnement sans jamais atteindre la reponse sans lui."""
    body = json.dumps({"messages": messages, "max_tokens": max_tokens}).encode("utf-8")
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    choice = data["choices"][0]["message"]
    return choice.get("content") or choice.get("reasoning_content") or ""


LAUNCHER_CONFIG = str(Path(__file__).resolve().parent / "llama_launcher.json")


def looks_broken(text):
    """Heuristique : sortie dominee par '?' = detokenizer/tokenizer casse
    (confirme ce soir sur q38_9b_s3_gutop.gguf), pas une reponse normale."""
    if not text:
        return True
    q = text.count("?")
    return q > 0 and q / len(text) > 0.5


def known_recipe_for(model_path):
    """Avant de diagnostiquer un bug : verifie si ce modele a deja une recette
    documentee dans llama_launcher.json ('tuned'). Le cas q38_9b_s3_gutop.gguf
    (sortie '?' casse) etait exactement ca -- -c 80000 n'etait pas une recette
    documentee, juste une valeur ad hoc, alors que README_RELEASE.md donne la
    vraie config testee (-c 2048, MTP chain3) pour ce fichier precis."""
    try:
        cfg = json.loads(Path(LAUNCHER_CONFIG).read_text(encoding="utf-8"))
    except OSError:
        return None
    return cfg.get("tuned", {}).get(model_path)


def auto_diagnose(symptom, model_path=None):
    """D'abord : y a-t-il une recette documentee connue pour ce modele exact
    (llama_launcher.json['tuned'])? Si oui, c'est la vraie cause probable
    (mauvaise config, pas un bug upstream) -- pas la peine d'interroger GitHub.
    Sinon, integration du skill git-ecosystem-investigator-claude :
    discover_target.py (deja teste en reseau reel cette session)."""
    if model_path:
        recipe = known_recipe_for(model_path)
        if recipe:
            print(f"\n[diagnostic auto] recette documentee trouvee pour {model_path} :")
            print(" ", json.dumps(recipe["settings"], ensure_ascii=False))
            print("  -- si le lancement actuel ne correspond pas a ces reglages, "
                  "c'est la cause la plus probable (pas un bug upstream).")
            return
    out = r"E:\temp\skilltest\harness_diagnosis.json"
    try:
        subprocess.run([sys.executable, SKILL_DISCOVER, "--workspace-root", "E:\\",
                        "--symptom", symptom, "--out", out], timeout=30)
        data = json.load(open(out, encoding="utf-8"))
        print("\n[diagnostic auto] signatures:", [s["value"] for s in data["extracted_signatures"]])
        for g in data.get("global_candidates", [])[:3]:
            print("  piste:", g["repo_full_name"], "-", g["matched_message"][:80])
        if not data.get("global_candidates") and not data.get("candidate_repos"):
            print("  rien trouve (local ni global) -- pas un bug deja documente connu")
    except Exception as e:
        print(f"[diagnostic auto] echec : {type(e).__name__}: {e}")


LOG_FILE = Path(__file__).resolve().parent / ".harness_memory" / "conversation_log.md"


def append_log(question, reply):
    """MANQUE REEL CORRIGE (liste des manques, 2026-10-17) : rien n'etait
    journalise automatiquement en mode conversation -- tools.py expose
    memory_write mais harness.py ne l'appelait jamais lui-meme. Log simple,
    append-only, pas un outil LLM (pas besoin que le modele le decide)."""
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n## Q: {question}\n\n{reply}\n")
    except OSError:
        pass


def handle_reply(reply, model_path=None, interactive=True):
    """MANQUE REEL CORRIGE : le dispatch d'outils (tools.py) n'etait cable
    QUE sur le mode question unique (sys.argv) -- invisible en conversation.
    Factorise ici pour les deux modes. Gere aussi la confirmation d'ecriture
    REELLEMENT (avant : le message "confirmation requise" s'affichait puis
    rien ne se passait jamais, aucun moyen de confirmer)."""
    if looks_broken(reply):
        auto_diagnose("llama.cpp qwen35 tokenizer detokenize gibberish question marks output",
                      model_path=model_path)
        return
    req = harness_tools.parse_tool_request(reply)
    if not req:
        return
    name, args = req
    result = harness_tools.dispatch(name, args)
    if result.get("requires_confirmation"):
        if interactive and sys.stdin.isatty():
            ans = input(f"[outil {name}] ecriture demandee ({args}) -- confirmer ? (o/n) ").strip().lower()
            if ans == "o":
                result = harness_tools.dispatch(name, args, confirmed_write=True)
                print(f"[outil {name}] execute ->", json.dumps(result, ensure_ascii=False)[:500])
            else:
                print(f"[outil {name}] annule par l'utilisateur.")
        else:
            print(f"\n[outil {name}] confirmation requise (ecriture) -- non-interactif, pas execute.")
    else:
        print(f"\n[outil {name}] ->", json.dumps(result, ensure_ascii=False)[:500])


def current_model_path():
    try:
        return json.loads(Path(LAUNCHER_CONFIG).read_text(encoding="utf-8")).get("model_path")
    except OSError:
        return None


def main():
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        messages = []
        ctx = rag_context(question)
        if ctx:
            print(f"[RAG] contexte trouve (BM25, {ctx.count(chr(10)+chr(10))+1} extrait(s))")
            messages.append({"role": "system", "content": "Contexte documentaire local (BM25, "
                             "peut etre non pertinent) :\n" + ctx})
        messages.append({"role": "user", "content": question})
        reply = ask(messages)
        print(reply)
        append_log(question, reply)
        handle_reply(reply, model_path=current_model_path())
        return

    history = []
    print("Harnais llama.cpp local -- Ctrl+C pour quitter")
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        history.append({"role": "user", "content": q})
        reply = ask(history)
        print(reply)
        history.append({"role": "assistant", "content": reply})
        append_log(q, reply)
        handle_reply(reply, model_path=current_model_path())


if __name__ == "__main__":
    main()
