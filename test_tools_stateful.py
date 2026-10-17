#!/usr/bin/env python3
"""Tests stateful (hypothesis.stateful, pattern RuleBasedStateMachine --
https://hypothesis.readthedocs.io/en/latest/stateful.html) pour tools.py.

Couvre le manque signale le 2026-10-17 ("zero test unitaire pour tools.py").
Modele : un dict Python en memoire qui doit toujours correspondre a l'etat
reel du disque (.harness_memory/*.md) apres toute sequence d'operations --
exactement le cas d'usage que la doc Hypothesis donne pour une base cle/valeur
("a miniature Redis-like database").

Invariants testes :
  - memory_write puis memory_read(meme slug) renvoie TOUJOURS le contenu ecrit
    (round-trip, quel que soit l'ordre des operations precedentes)
  - grep ne renvoie jamais plus de max_hits resultats, jamais plus de
    max_files fichiers scannes (le bug reel corrige cette session --
    scan non borne qui bloquait le process plusieurs minutes)
  - write_file refuse TOUJOURS un chemin hors du workspace, quelle que soit
    la sequence d'appels precedente (invariant de securite, pas de fenetre
    de race ou un etat precedent le contournerait)

Usage:
  python -m pytest test_tools_stateful.py -v
  python test_tools_stateful.py              # mode direct, sans pytest
"""
import shutil
import string
import sys
from pathlib import Path

from hypothesis import HealthCheck, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant, precondition

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools  # noqa: E402

def _cleanup():
    if tools.MEMORY_DIR.exists():
        shutil.rmtree(tools.MEMORY_DIR)
    tools.MEMORY_DIR.mkdir(exist_ok=True)


SLUG_ALPHABET = string.ascii_lowercase + string.digits + "-_"
slugs = st.text(alphabet=SLUG_ALPHABET, min_size=1, max_size=20)
titles = st.text(min_size=0, max_size=50)
contents = st.text(min_size=0, max_size=500)


class MemoryModel(RuleBasedStateMachine):
    """Modele = dict slug->content qui doit toujours matcher le disque."""

    def __init__(self):
        super().__init__()
        # BUG REEL TROUVE EN LANCANT CE TEST : le nettoyage n'avait lieu
        # qu'une fois pour toute la suite (dans __main__), pas entre chaque
        # exemple genere par hypothesis -- des notes laissees par un exemple
        # precedent faussaient l'invariant disk_matches_model du suivant
        # (FlakyStrategyDefinition). RuleBasedStateMachine.__init__ est
        # appele au debut de CHAQUE exemple -- c'est ici qu'il faut nettoyer.
        _cleanup()
        self.model = {}

    @rule(slug=slugs, title=titles, content=contents)
    def write(self, slug, title, content):
        result = tools.tool_memory_write(slug, title, content)
        if "error" in result:
            # Le slug genere par hypothesis a echoue la validation regex --
            # verifier que c'est bien pour la bonne raison, pas un crash.
            assert not tools.re.match(r"^[a-z0-9_-]+$", slug)
            return
        self.model[slug] = content

    @rule(slug=slugs)
    def read_existing_or_not(self, slug):
        result = tools.tool_memory_read(slug)
        if slug in self.model:
            assert "content" in result, f"slug {slug!r} connu du modele mais absent du disque"
            assert self.model[slug] in result["content"]
        else:
            assert "error" in result, f"slug {slug!r} inconnu mais lu sans erreur : {result}"

    @invariant()
    def disk_matches_model(self):
        """Apres CHAQUE regle, le nombre de notes sur disque == nombre dans le modele."""
        on_disk = set(tools.tool_memory_read()["notes"])
        assert on_disk == set(self.model.keys()), (on_disk, set(self.model.keys()))


class GrepBoundsModel(RuleBasedStateMachine):
    """Invariant de bornes : grep ne doit jamais depasser max_files/max_hits,
    quel que soit le pattern (y compris des regex couteuses en retour arriere)."""

    @rule(pattern=st.sampled_from([".*", "a+", "[A-Za-z]+", "(a|a)*b", "x{0,50}"]),
          max_files=st.integers(min_value=1, max_value=50),
          max_hits=st.integers(min_value=1, max_value=50))
    def bounded_grep(self, pattern, max_files, max_hits):
        result = tools.tool_grep(pattern, max_files=max_files, max_hits=max_hits)
        if "error" in result:
            return
        assert result["files_scanned"] <= max_files
        assert len(result["hits"]) <= max_hits


class ConfinementModel(RuleBasedStateMachine):
    """Invariant de securite : write_file ne doit JAMAIS ecrire hors
    workspace, pour aucune forme de chemin (relatif, absolu ailleurs,
    traversee ..)."""

    @rule(path=st.sampled_from([
        "../../outside.txt",
        "C:/Windows/System32/evil.txt",
        "..\\..\\..\\outside2.txt",
        str(Path(tools.WORKSPACE).parent / "escaped.txt"),
    ]))
    def reject_outside_paths(self, path):
        result = tools.tool_write_file(path, "contenu de test")
        assert "error" in result, f"chemin hors-workspace accepte a tort : {path} -> {result}"
        escaped = Path(tools.WORKSPACE).parent / "escaped.txt"
        assert not escaped.exists(), "ecriture hors-workspace reellement survenue sur disque"


TestMemory = MemoryModel.TestCase
TestGrepBounds = GrepBoundsModel.TestCase
TestConfinement = ConfinementModel.TestCase

TestMemory.settings = settings(max_examples=60, stateful_step_count=15,
                                # I/O disque reel a chaque regle (memory_write/read ecrivent de
                                # vrais fichiers) -- lenteur attendue, pas un defaut de la strategie.
                                suppress_health_check=[HealthCheck.too_slow])
TestGrepBounds.settings = settings(max_examples=30, stateful_step_count=8)
TestConfinement.settings = settings(max_examples=10, stateful_step_count=5)


if __name__ == "__main__":
    import unittest
    _cleanup()
    try:
        suite = unittest.TestSuite()
        loader = unittest.TestLoader()
        for case in (TestMemory, TestGrepBounds, TestConfinement):
            suite.addTest(loader.loadTestsFromTestCase(case))
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)
    finally:
        _cleanup()
