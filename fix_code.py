#!/usr/bin/env python3
"""Portage de Dual-Agent_Code_Fixer_FIXED.py (fourni par l'utilisateur, 2 bugs
reels corriges a la source : os.replace() au lieu de p.rename() -- plantait
FileExistsError sur Windows a la 2e passe -- et import corrige) vers le
serveur llama.cpp local (127.0.0.1:8080) au lieu de LM Studio/FastFlow.
Memes 2 agents (Strategiste puis Critique), CLI au lieu de tkinter pour
etre appelable depuis lama_harness_launcher.exe.

Usage:
  python fix_code.py <dossier> [--max-files N] [--dry-run]
"""
import argparse
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402

SYS_STRATEGE = ("Tu es un Architecte IA. Ton role est de challenger le code en "
                "utilisant les concepts de la litterature (Self-Refine, CodeBERT) "
                "et les patterns connus. Identifie les bugs reels et propose un "
                "plan de correction concis.")
SYS_CRITIQUE = ("Tu es un Critique Senior. Ton role est de contredire le plan si "
                 "necessaire, d'eliminer les hallucinations, et d'ecrire le code "
                 "FINAL corrige. Reponds UNIQUEMENT avec le code entre balises "
                 "```python.\n\n"
                 "REGLE ABSOLUE (verifiee automatiquement apres coup, toute "
                 "violation rejette la correction) : tu ne dois JAMAIS modifier "
                 "les arguments litteraux des appels de fonction au niveau module "
                 "(ex: les print(...) ou les appels de demonstration/test en bas du "
                 "fichier). Si une fonction doit lever une exception sur certaines "
                 "entrees (division par zero, etc.), corrige la FONCTION pour gerer "
                 "ou lever cette exception proprement -- ne change JAMAIS l'appel "
                 "existant pour eviter de declencher le cas que tu viens de corriger. "
                 "Contourner un bug en changeant les donnees de test au lieu de "
                 "corriger la logique est un ECHEC, pas une correction.")


def module_level_call_args(code):
    """Signature des appels de fonction au niveau module (hors def/class) --
    utilisee pour detecter si l'agent critique a change les arguments d'un
    print(...)/appel de demo au lieu de corriger la fonction elle-meme (bug
    REEL observe en testant : divide(10, 0) devenu divide(10, 2) pour eviter
    de declencher l'exception que le "correctif" venait d'ajouter). Renvoie
    None si le code ne parse pas (cas normal quand on corrige une erreur de
    syntaxe -- rien a comparer dans ce cas, on ne peut pas bloquer sur cette
    garde la).

    MANQUE REEL CORRIGE (liste des manques, 2026-10-17) : la version
    precedente ne regardait que tree.body directement -- un contournement
    cache dans `if __name__ == "__main__": ...` (pattern tres courant)
    passait inapercu. Descend maintenant aussi dans le corps de ce bloc."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    bodies = [tree.body]
    for node in tree.body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
            bodies.append(node.body)
    calls = []
    for body in bodies:
        for node in body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                try:
                    calls.append(ast.dump(node.value))
                except Exception:
                    pass
    return calls


def show_diff(path, old_code, new_code):
    import difflib
    diff = difflib.unified_diff(old_code.splitlines(keepends=True),
                                 new_code.splitlines(keepends=True),
                                 fromfile=f"{path.name} (avant)", tofile=f"{path.name} (apres)")
    sys.stdout.writelines(diff)


def fix_file(path, max_chars=3000):
    code = path.read_text(encoding="utf-8", errors="ignore")
    if len(code) > 50000:
        code = code[:50000]

    plan = harness.ask([
        {"role": "system", "content": SYS_STRATEGE},
        {"role": "user", "content": f"Analyse ce code et propose un plan de correction :\n\n{code[:max_chars]}"},
    ])
    if harness.looks_broken(plan):
        return None, "plan casse (sortie LLM illisible)"

    final = harness.ask([
        {"role": "system", "content": SYS_CRITIQUE},
        {"role": "user", "content": f"Code original:\n{code[:1500]}\n\nPlan propose:\n{plan[:500]}\n\n"
                                     "Critique ce plan et donne le code corrige."},
    ], max_tokens=2000)
    if harness.looks_broken(final):
        return None, "code final casse (sortie LLM illisible)"

    m = re.search(r"```python\n(.*?)\n```", final, re.DOTALL)
    if not m:
        return None, "aucun bloc ```python``` dans la reponse"
    new_code = m.group(1)

    before = module_level_call_args(code)
    after = module_level_call_args(new_code)
    if before is not None and after is not None and before != after:
        return None, ("REJETE : les arguments des appels de niveau module ont "
                       "change (l'agent a probablement contourne le bug en "
                       "changeant les donnees de test au lieu de corriger la "
                       "fonction) -- correction ignoree, fichier original conserve")

    return new_code, plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dossier")
    ap.add_argument("--max-files", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true", help="n'ecrit rien, affiche seulement le diff")
    ap.add_argument("--yes", action="store_true",
                     help="ecrit sans demander confirmation (sinon : diff affiche + confirmation requise, "
                          "meme politique que tools.py pour write_file)")
    args = ap.parse_args()

    root = Path(args.dossier)
    if not root.is_dir():
        print(f"ERREUR : dossier introuvable : {root}")
        return 1

    py_files = list(root.glob("**/*.py"))[: args.max_files]
    print(f"{len(py_files)} fichier(s) Python trouve(s) (max {args.max_files}).")

    import os
    for i, p in enumerate(py_files, 1):
        print(f"\n[{i}/{len(py_files)}] {p.name}...")
        old_code = p.read_text(encoding="utf-8", errors="ignore")
        new_code, info = fix_file(p)
        if new_code is None:
            print(f"  ✗ non corrige : {info}")
            continue

        print("  --- diff propose ---")
        show_diff(p, old_code, new_code)
        print("  --- fin diff ---")

        if args.dry_run:
            print("  (dry-run) non ecrit.")
            continue

        write = args.yes
        if not write:
            if sys.stdin.isatty():
                write = input(f"  Ecrire cette correction dans {p.name} ? (o/n) ").strip().lower() == "o"
            else:
                print("  non-interactif et --yes absent -- non ecrit (relancer avec --yes pour appliquer).")

        if not write:
            continue

        backup = p.with_suffix(".py.bak")
        os.replace(str(p), str(backup))  # atomique, remplace meme si .bak existe deja
        p.write_text(new_code, encoding="utf-8")
        print(f"  ✓ corrige (backup : {backup.name})")

    print("\nTermine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
