# Arborescence — harnais-1080

Harnais PC minimal pour piloter un serveur llama.cpp local (GTX 1080) : RAG
BM25, outils portés de l'app Android `philo-agent-harness`, intégration du
skill `git-ecosystem-investigator`, correcteur de code dual-agent, TUI de
monitoring matériel, lanceur Windows compilé.

```
harnais-1080/
├── harness.py                     Coeur : appel API OpenAI-compatible du
│                                   serveur llama.cpp local (127.0.0.1:8080),
│                                   RAG BM25, dispatch d'outils, confirmation
│                                   d'ecriture reelle, journal de conversation
│                                   automatique (.harness_memory/).
│
├── tools.py                       Portage des outils de l'app Android
│                                   (GitToolExecutor, WorkspaceGrepExecutor,
│                                   memoire) en Python : grep borne (max_files/
│                                   max_hits), memory_read/write, git_status/
│                                   diff, write_file confine au workspace,
│                                   skill_discover/skill_verify_claim (pont
│                                   vers git-ecosystem-investigator).
│
├── bm25.py                        Moteur de recherche BM25 pur Python (zero
│                                   dependance). Chunking sur frontieres
│                                   semantiques (paragraphes/titres Markdown),
│                                   repli sur decoupe a taille fixe pour les
│                                   blocs sans separateur naturel.
│
├── bm25_reindex.py                Reconstruit bm25_index.json sur les
│                                   racines reellement utilisees par le
│                                   harnais (workspace + doc du modele).
│
├── fix_code.py                    Correcteur de code dual-agent (strategiste
│                                   + critique) pointe sur le serveur local.
│                                   Garde anti-triche : rejette toute
│                                   correction qui modifie les arguments
│                                   litteraux des appels de niveau module
│                                   (y compris sous `if __name__ ==
│                                   "__main__":`) au lieu de corriger la
│                                   logique. Diff affiche avant ecriture,
│                                   confirmation requise (ou --yes explicite).
│
├── omni_monitor.py                TUI de supervision materielle (Rich) :
│                                   CPU/RAM/temp/GPU en direct + chargement de
│                                   modules Python par glisser-deposer avec
│                                   validation de securite (AST, patterns
│                                   dangereux verifies par ligne, approbation
│                                   explicite avant import).
│
├── LamaHarnessLauncher.cs          Source du lanceur Windows (compile avec le
│                                   csc.exe du .NET Framework deja present,
│                                   aucune dependance telechargee). Options :
│                                   --model/--ctx/--ngl/--threads/--port,
│                                   --no-mtp, --spec-chain/--spec-n-max,
│                                   --reasoning-budget, --ask, --reindex-rag,
│                                   --fix-code <dossier>, --monitor, --stop,
│                                   --force, --list-recipes, --log, --no-
│                                   harness, --skip-server. Health-check reel
│                                   avant d'ouvrir le harnais ; detecte un
│                                   serveur deja actif sur le port pour ne
│                                   jamais en dupliquer un second ; redirige
│                                   la sortie du serveur vers un fichier de
│                                   log persistant.
│
├── llama_launcher.json            Recettes connues par modele ("tuned") :
│                                   contexte, couches GPU, MTP/chain,
│                                   debit mesure. Source de verite consultee
│                                   automatiquement par harness.py avant de
│                                   diagnostiquer un bug sur une sortie
│                                   cassee (une mauvaise config prime sur
│                                   l'hypothese d'un bug upstream).
│
├── test_fixcode_orchestrator.py   Banc d'essai stateful (hypothesis.stateful.
│                                   RuleBasedStateMachine) pour fix_code.py --
│                                   LLM completement coupe, reponses
│                                   predefinies injectees. Verifie qu'aucun
│                                   succes n'est declare sans preuve (code
│                                   final valide + garde anti-triche passee).
│
└── test_tools_stateful.py         Banc d'essai stateful pour tools.py :
                                    round-trip memoire disque<->modele,
                                    bornes grep jamais depassees, confinement
                                    write_file jamais contourne.
```

## Non inclus dans ce depot (volontairement)

- `lama_harness_launcher.exe` — binaire compile, reproductible depuis
  `LamaHarnessLauncher.cs` (voir commande ci-dessous), pas versionne.
- `.harness_memory/`, `bm25_index.json`, `*.log`, `*.bak` — etat
  runtime/genere, pas du code source.
- Les binaires llama.cpp (`llama-server.exe`, forks) et les poids de modeles
  `.gguf` — bien trop gros, et externes a ce depot (voir `llama_launcher.json`
  pour les chemins attendus sur la machine ou ce harnais tourne).

## Recompiler le lanceur

```bash
csc.exe /nologo /out:lama_harness_launcher.exe LamaHarnessLauncher.cs
```
(`csc.exe` du .NET Framework est deja present sur une installation Windows
standard, ex: `C:\Windows\Microsoft.NET\Framework64\v2.0.50727\csc.exe`.)
