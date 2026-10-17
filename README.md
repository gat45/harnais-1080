# harnais-1080

Harnais PC minimal pour piloter un serveur [llama.cpp](https://github.com/ggerganov/llama.cpp)
local (testé sur une GTX 1080) : RAG BM25 maison, outils portés d'une app
Android (grep borné, mémoire, git, écriture confinée au workspace), correcteur
de code dual-agent avec garde anti-triche, TUI de supervision matérielle, et
un lanceur Windows qui orchestre tout ça en une commande.

Voir [ARBORESCENCE.md](ARBORESCENCE.md) pour le détail de chaque fichier.

## Démarrage

```bash
python harness.py "ta question"      # une reponse, puis quitte
python harness.py                     # mode conversation
```

Ou via le lanceur compilé (voir `LamaHarnessLauncher.cs` pour toutes les
options : `--monitor`, `--fix-code <dossier>`, `--reindex-rag`, `--stop`...).

## Tests

```bash
python test_tools_stateful.py
python test_fixcode_orchestrator.py
```

Les deux utilisent [Hypothesis](https://hypothesis.readthedocs.io/en/latest/stateful.html)
en mode stateful ; le second coupe complètement le LLM (réponses injectées)
pour tester la logique de `fix_code.py` indépendamment de la qualité d'un
modèle.
