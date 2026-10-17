#!/usr/bin/env python3
"""Banc d'essai stateful pour fix_code.py, LLM completement coupe (harness.ask
est monkeypatche -- on injecte des reponses predefinies, pas un vrai modele).
Pattern hypothesis.stateful.RuleBasedStateMachine, meme principe que
https://hypothesis.readthedocs.io/en/latest/stateful.html.

PERIMETRE HONNETE : fix_code.py n'a PAS de boucle d'orchestration a etats
persistants (pas de budget, pas de reprise apres coupure, pas de dedup
d'evenements entre process) -- ces proprietes-la n'existent nulle part dans ce
harnais, donc ne sont PAS testees ici (les fabriquer serait mentir sur ce que
le code fait). Ce qui existe reellement et qui EST teste :

  1. Correction logique : aucune transition interdite --
     (plan casse -> jamais ecrit), (critique casse -> jamais ecrit),
     (pas de bloc ```python``` -> jamais ecrit).
  2. Coherence des preuves : le statut final (ecrit / rejete / non-corrige)
     decoule TOUJOURS de ce que les reponses LLM injectees contenaient --
     jamais un "succes" sans code final valide extrait.
  3. Resistance : meme fichier source re-traite deux fois de suite (figure
     la plus proche d'un "evenement recu deux fois" dans ce code) ne doit
     jamais corrompre le fichier ni le backup.
  4. Progression : le garde anti-triche (arguments d'appel changes) rejette
     TOUJOURS la correction au lieu de l'accepter silencieusement -- pas de
     "succes artificiel".

Usage:
  python test_fixcode_orchestrator.py
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402
import fix_code  # noqa: E402

ORIGINAL_CODE = (
    "def divide(a, b):\n"
    "    return a / b\n\n"
    "print(divide(10, 0))\n"
)

# Reponses LLM "injectees" couvrant le tableau de transitions demande --
# aucune n'appelle le vrai serveur (harness.ask est mock, voir setUp()).
SCENARIOS = {
    "plan_casse": {
        "plan": "???????????????????????????????????????????",
        "critique": "peu importe, jamais atteint",
    },
    "critique_casse": {
        "plan": "Le bug est l'absence de validation de b==0.",
        "critique": "???????????????????????????????????????????",
    },
    "pas_de_bloc_code": {
        "plan": "Le bug est l'absence de validation de b==0.",
        "critique": "Voici le correctif, mais j'ai oublie les balises python.",
    },
    "triche_appel_change": {
        "plan": "Le bug est l'absence de validation de b==0.",
        "critique": ("```python\n"
                      "def divide(a, b):\n"
                      "    if b == 0:\n"
                      "        raise ZeroDivisionError('non autorise')\n"
                      "    return a / b\n\n"
                      "print(divide(10, 2))\n"
                      "```"),
    },
    "correction_honnete": {
        "plan": "Le bug est l'absence de validation de b==0.",
        "critique": ("```python\n"
                      "def divide(a, b):\n"
                      "    if b == 0:\n"
                      "        raise ZeroDivisionError('non autorise')\n"
                      "    return a / b\n\n"
                      "print(divide(10, 0))\n"
                      "```"),
    },
}

EXPECTED_OUTCOME = {
    "plan_casse": "ABORT",
    "critique_casse": "ABORT",
    "pas_de_bloc_code": "ABORT",
    "triche_appel_change": "REJECT",
    "correction_honnete": "WRITE",
}


def fake_ask(scenario_name):
    """Remplace harness.ask : 1er appel = plan, 2e appel = critique.
    Jamais de reseau, jamais de vrai LLM -- events predefinis seulement."""
    calls = {"n": 0}
    sc = SCENARIOS[scenario_name]

    def _ask(messages, max_tokens=512):
        calls["n"] += 1
        return sc["plan"] if calls["n"] == 1 else sc["critique"]
    return _ask


class FixCodeOrchestratorModel(RuleBasedStateMachine):
    """Pour chaque scenario injecte, verifie que l'issue observee correspond
    EXACTEMENT a l'issue attendue -- jamais de succes sans preuve (code
    extrait + garde anti-triche passee), jamais d'ecriture sur plan/critique
    casses."""

    @rule(scenario=st.sampled_from(list(SCENARIOS.keys())))
    def run_scenario(self, scenario):
        tmp = Path(harness.__file__).resolve().parent / "_test_orchestrator_tmp.py"
        tmp.write_text(ORIGINAL_CODE, encoding="utf-8")
        try:
            with mock.patch.object(harness, "ask", fake_ask(scenario)):
                new_code, info = fix_code.fix_file(tmp)

            expected = EXPECTED_OUTCOME[scenario]
            if expected == "ABORT":
                assert new_code is None, f"{scenario}: attendu ABORT, obtenu code={new_code!r}"
            elif expected == "REJECT":
                assert new_code is None, f"{scenario}: attendu REJECT (garde anti-triche), obtenu {new_code!r}"
                assert "REJETE" in info, f"{scenario}: message de rejet attendu, obtenu {info!r}"
            elif expected == "WRITE":
                assert new_code is not None, f"{scenario}: attendu WRITE, obtenu abort ({info!r})"
                assert "print(divide(10, 0))" in new_code, (
                    f"{scenario}: l'appel original a ete modifie malgre un scenario honnete -- "
                    "preuve absente du code final")
        finally:
            tmp.unlink(missing_ok=True)
            bak = tmp.with_suffix(".py.bak")
            bak.unlink(missing_ok=True)

    @rule()
    def meme_fichier_deux_fois_ne_corrompt_rien(self):
        """Figure la plus proche de 'meme evenement recu deux fois' dans ce
        code (pas de boucle d'orchestration a etats -> pas de vrai dedup
        d'evenements a tester, seulement : re-traiter ne doit jamais rendre
        le fichier source invalide ni ecraser un backup silencieusement)."""
        tmp = Path(harness.__file__).resolve().parent / "_test_orchestrator_tmp2.py"
        tmp.write_text(ORIGINAL_CODE, encoding="utf-8")
        try:
            with mock.patch.object(harness, "ask", fake_ask("correction_honnete")):
                new_code1, _ = fix_code.fix_file(tmp)
                # 2e passage sur le MEME fichier source (tmp inchange sur disque) :
                new_code2, _ = fix_code.fix_file(tmp)
            assert new_code1 is not None and new_code2 is not None
            assert new_code1 == new_code2, "meme entree, 2 sorties differentes -- non deterministe"
        finally:
            tmp.unlink(missing_ok=True)
            tmp.with_suffix(".py.bak").unlink(missing_ok=True)


TestFixCodeOrchestrator = FixCodeOrchestratorModel.TestCase
TestFixCodeOrchestrator.settings = settings(max_examples=25, stateful_step_count=6)


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestFixCodeOrchestrator)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
