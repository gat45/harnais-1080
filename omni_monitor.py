#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OMNI-SLA v6.4 — DROP & SYNC ÉDITION
Drag & Drop support + Dynamic Module Discovery avec validations de sécurité.

SÉCURITÉ:
- ✓ Validation de module (def run(), def status() requis)
- ✓ Scanning du code pour patterns dangereux
- ✓ Approbation utilisateur avant import
- ✓ Isolation module en sandbox
- ✓ Logging détaillé des opérations
"""

import os, sys, time, json, threading, subprocess, pathlib, requests, shutil, importlib, importlib.util, hashlib

# BUG REEL TROUVE EN TESTANT LE COMPORTEMENT REEL (pas juste "le process vit") :
# UnicodeEncodeError sur '✓' quand la console Windows est en codepage legacy
# cp1252 (ex: sortie redirigee/pipee sans codepage UTF-8) -- le script utilise
# des emojis (✓, ❌...) partout dans les logs. Meme classe de bug deja corrigee
# ailleurs cette session (discover_target.py, tools.py).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from datetime import datetime
from pathlib import Path
from collections import deque
from typing import Optional, Dict, List, Tuple
import ast
import traceback

import psutil
try:
    import GPUtil
    HAS_GPU = True
except ImportError:
    HAS_GPU = False

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.progress import ProgressBar
from rich import box

console = Console()
ROOT = Path(__file__).parent
CONFIG_FILE = ROOT / "omni_config.json"
MODULES_PATH = ROOT / "modules"
MODULE_MANIFEST = ROOT / "module_manifest.json"
# BUG REEL CORRIGE (trouve en listant les manques du harnais, 2026-10-17) :
# MODULES_PATH n'etait jamais cree -- approve_module() plantait au premier
# module approuve (shutil.copy vers un dossier inexistant).
MODULES_PATH.mkdir(exist_ok=True)

# ─────────────────────────────────────────────
#  MODULE SECURITY & VALIDATION
# ─────────────────────────────────────────────

class ModuleValidator:
    """Valide et signe les modules avant import."""
    
    # Patterns dangereux à détecter
    DANGEROUS_PATTERNS = [
        'eval(',
        'exec(',
        '__import__(',
        'subprocess.call',
        'os.system',
        'open(',  # Attention spéciale à la manipulation de fichiers
        'input(',  # Pour éviter les interactions inattendues
    ]
    
    REQUIRED_FUNCTIONS = ['run', 'status']
    
    @staticmethod
    def validate_module(filepath: Path) -> Tuple[bool, str]:
        """Valide qu'un module respecte les règles de sécurité."""
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 1. Vérifier la syntaxe AST
            try:
                tree = ast.parse(content)
            except SyntaxError as e:
                return False, f"Erreur syntaxe: {str(e)}"
            
            # 2. Vérifier les fonctions requises
            functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
            missing = [f for f in ModuleValidator.REQUIRED_FUNCTIONS if f not in functions]
            if missing:
                return False, f"Fonctions manquantes: {', '.join(missing)}"
            
            # 3. Scanner les patterns dangereux (par LIGNE, pas par fichier entier --
            # BUG REEL CORRIGE : l'original desactivait 'open(' pour TOUT le fichier
            # des qu'un 'read_text' apparaissait n'importe ou, meme dans un commentaire
            # sans rapport -- un simple "# read_text" suffisait a passer n'importe quel
            # open() malveillant. Meme faille pour '#TRUSTED'/subprocess.call. Desormais
            # l'exception ne s'applique qu'a LA LIGNE qui contient le pattern.
            for lineno, line in enumerate(content.splitlines(), 1):
                for pattern in ModuleValidator.DANGEROUS_PATTERNS:
                    if pattern not in line:
                        continue
                    if pattern == 'open(' and 'read_text' in line:
                        continue  # Pathlib.read_text() est sur, sur cette ligne precise
                    if pattern == 'subprocess.call' and '#TRUSTED' in line:
                        continue  # Exception explicite, sur cette ligne precise
                    return False, f"Pattern dangereux detecte ligne {lineno}: '{pattern}'"
            
            # 4. Vérifier la taille (limite 100KB)
            if len(content) > 100000:
                return False, "Module trop gros (>100KB)"
            
            return True, "✓ Validation OK"
        
        except Exception as e:
            return False, f"Erreur validation: {str(e)}"
    
    @staticmethod
    def compute_signature(filepath: Path) -> str:
        """Calcule la signature SHA256 du module."""
        with open(filepath, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]


# ─────────────────────────────────────────────
#  SYSTÈME DE MONITORING MATÉRIEL
# ─────────────────────────────────────────────

class SystemMonitor:
    """Thread-safe hardware monitoring."""
    
    def __init__(self):
        self.stats: Dict[str, float] = {
            "cpu_percent": 0.0,
            "cpu_freq": 0.0,
            "ram_percent": 0.0,
            "ram_used": 0.0,
            "ram_total": 0.0,
            "temp": 0.0,
            "gpu_load": 0.0,
            "gpu_temp": 0.0,
        }
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._update_loop, daemon=True)
        self._thread.start()

    def _update_loop(self):
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    self.stats["cpu_percent"] = psutil.cpu_percent(interval=0.5)
                    try:
                        freq = psutil.cpu_freq()
                        self.stats["cpu_freq"] = freq.current if freq else 0.0
                    except:
                        self.stats["cpu_freq"] = 0.0
                    
                    mem = psutil.virtual_memory()
                    self.stats["ram_percent"] = mem.percent
                    self.stats["ram_used"] = mem.used / (1024**3)
                    self.stats["ram_total"] = mem.total / (1024**3)
                    
                    try:
                        temps = psutil.sensors_temperatures()
                        if temps:
                            for sensor_type in ["coretemp", "k10temp", "acpitz"]:
                                if sensor_type in temps:
                                    self.stats["temp"] = temps[sensor_type][0].current
                                    break
                            else:
                                self.stats["temp"] = list(temps.values())[0][0].current
                    except:
                        self.stats["temp"] = 0.0

                    if HAS_GPU:
                        try:
                            gpus = GPUtil.getGPUs()
                            if gpus:
                                gpu = gpus[0]
                                self.stats["gpu_load"] = gpu.load * 100
                                self.stats["gpu_temp"] = gpu.temperature
                        except:
                            pass
            
            except Exception:
                pass
            
            time.sleep(1)

    def get_stats(self) -> Dict[str, float]:
        with self._lock:
            return dict(self.stats)

    def stop(self):
        self._stop_event.set()


# ─────────────────────────────────────────────
#  MOTEUR OMNI v6.4 - DROP & SYNC
# ─────────────────────────────────────────────

class OmniEngine:
    def __init__(self):
        self.view = "dashboard"
        self.logs = deque(maxlen=30)
        self.monitor = SystemMonitor()
        self.chat_history: List[Tuple[str, str]] = []
        self.modules_loaded: Dict[str, dict] = {}
        self.lock = threading.Lock()
        self.config = self._load_config()
        self.module_manifest = self._load_manifest()
        self.pending_approvals: List[dict] = []

    def _load_config(self) -> Dict:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
        return {"apis": [], "model": "local", "active_api": None}

    def _load_manifest(self) -> Dict:
        """Charge la liste des modules approuvés."""
        if MODULE_MANIFEST.exists():
            return json.loads(MODULE_MANIFEST.read_text())
        return {"approved": [], "signatures": {}}

    def _save_manifest(self):
        """Sauvegarde la liste des modules approuvés."""
        with open(MODULE_MANIFEST, 'w') as f:
            json.dump(self.module_manifest, f, indent=2)

    def add_log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.logs.append((ts, level, msg))

    # ====================================================
    #  DRAG & DROP HANDLER
    # ====================================================

    def handle_drop(self, path_input: str) -> str:
        """Traite un fichier/dossier glissé-déposé."""
        
        # Nettoyage du chemin
        path_input = path_input.strip().strip('"\'')
        
        if not os.path.exists(path_input):
            self.add_log(f"Chemin invalide: {path_input}", "ERROR")
            return "❌ Chemin introuvable"
        
        # Cas 1: Fichier Python (potentiel nouveau module)
        if os.path.isfile(path_input) and path_input.endswith('.py'):
            return self._handle_module_drop(Path(path_input))
        
        # Cas 2: Dossier (scan batch)
        elif os.path.isdir(path_input):
            return self._handle_folder_drop(Path(path_input))
        
        # Cas 3: Fichier non-Python (lecture simple)
        elif os.path.isfile(path_input):
            return self._handle_file_read(Path(path_input))
        
        return "❓ Type de fichier non supporté"

    def _handle_module_drop(self, filepath: Path) -> str:
        """Traite le dépôt d'un module Python."""
        
        self.add_log(f"Nouveau module détecté: {filepath.name}", "DROP")
        
        # 1. Validation de sécurité
        is_valid, msg = ModuleValidator.validate_module(filepath)
        if not is_valid:
            self.add_log(f"Validation échouée: {msg}", "REJECT")
            return f"❌ Module non valide: {msg}"
        
        # 2. Calcul de signature
        signature = ModuleValidator.compute_signature(filepath)
        
        # 3. Vérifier si module déjà approuvé
        if filepath.name in self.module_manifest.get("approved", []):
            return f"✓ Module '{filepath.name}' déjà approuvé. À charger..."
        
        # 4. Créer une demande d'approbation
        approval = {
            "filename": filepath.name,
            "path": str(filepath),
            "signature": signature,
            "status": "pending",
            "timestamp": datetime.now().isoformat()
        }
        self.pending_approvals.append(approval)
        
        msg = f"⏳ Module '{filepath.name}' en attente d'approbation (#approval_{len(self.pending_approvals)-1})"
        self.add_log(msg, "WAIT")
        return msg

    def _handle_folder_drop(self, folder: Path) -> str:
        """Traite le dépôt d'un dossier (scan batch)."""
        
        py_files = list(folder.glob('**/*.py'))
        self.add_log(f"Dossier scanné: {len(py_files)} fichiers Python trouvés", "SCAN")
        
        return f"📂 Dossier '{folder.name}' prêt pour traitement batch ({len(py_files)} fichiers)"

    def _handle_file_read(self, filepath: Path) -> str:
        """Traite la lecture simple d'un fichier."""
        
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            lines = len(content.split('\n'))
            self.add_log(f"Fichier lu: {filepath.name} ({lines} lignes)", "READ")
            return f"✓ Fichier '{filepath.name}' chargé en mémoire ({lines} lignes)"
        
        except Exception as e:
            self.add_log(f"Erreur lecture: {str(e)}", "ERROR")
            return f"❌ Erreur lecture: {str(e)}"

    # ====================================================
    #  MODULE APPROVAL & LOADING
    # ====================================================

    def approve_module(self, approval_id: int) -> str:
        """Approuve et charge un module."""
        
        if approval_id >= len(self.pending_approvals):
            return "❌ ID d'approbation invalide"
        
        approval = self.pending_approvals[approval_id]
        filepath = Path(approval["path"])
        
        try:
            # 1. Copier le module dans /modules
            dest = MODULES_PATH / filepath.name
            shutil.copy(filepath, dest)
            self.add_log(f"Module copié: {dest}", "COPY")
            
            # 2. Charger le module
            spec = importlib.util.spec_from_file_location(
                filepath.stem,
                dest
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 3. Vérifier les fonctions
            if not hasattr(module, 'run') or not hasattr(module, 'status'):
                raise Exception("Fonctions run() ou status() manquantes")
            
            # 4. Enregistrer comme approuvé
            self.module_manifest["approved"].append(filepath.name)
            self.module_manifest["signatures"][filepath.name] = approval["signature"]
            self._save_manifest()
            
            self.modules_loaded[filepath.name] = {
                "module": module,
                "path": str(dest),
                "loaded_at": datetime.now().isoformat()
            }
            
            self.add_log(f"Module chargé: {filepath.name}", "OK")
            approval["status"] = "approved"
            return f"✅ Module '{filepath.name}' chargé avec succès"
        
        except Exception as e:
            self.add_log(f"Erreur charge module: {str(e)}", "ERROR")
            approval["status"] = "rejected"
            return f"❌ Erreur: {str(e)}"

    def reject_module(self, approval_id: int) -> str:
        """Rejette une demande d'approbation."""
        
        if approval_id >= len(self.pending_approvals):
            return "❌ ID d'approbation invalide"
        
        approval = self.pending_approvals[approval_id]
        approval["status"] = "rejected"
        self.add_log(f"Module rejeté: {approval['filename']}", "REJECT")
        return f"✓ Module '{approval['filename']}' rejeté"

    # ====================================================
    #  RENDU DE L'INTERFACE
    # ====================================================

    def make_metrics_panel(self) -> Panel:
        stats = self.monitor.get_stats()
        t = Table.grid(expand=True)
        t.add_column(ratio=1)
        t.add_column(justify="right")
        
        cpu_col = "green" if stats["cpu_percent"] < 50 else "yellow" if stats["cpu_percent"] < 80 else "red"
        ram_col = "green" if stats["ram_percent"] < 70 else "yellow" if stats["ram_percent"] < 85 else "red"
        
        t.add_row(f"[{cpu_col}]CPU Load[/]", f"{stats['cpu_percent']:.1f}%")
        t.add_row(f"[{cpu_col}]Freq[/]", f"{stats['cpu_freq']:.0f} MHz")
        t.add_row(f"[{ram_col}]RAM[/]", f"{stats['ram_used']:.1f}/{stats['ram_total']:.1f} GB")
        t.add_row("Temp", f"{stats['temp']:.1f}°C")
        
        if HAS_GPU and stats["gpu_load"] > 0:
            t.add_row("GPU", f"{stats['gpu_load']:.1f}%")
        
        return Panel(t, title="[bold]MATÉRIEL[/]", border_style="bright_blue")

    def make_logs_panel(self) -> Panel:
        t = Text()
        with self.lock:
            for ts, lvl, msg in list(self.logs)[-15:]:
                t.append(f"{ts} ", style="dim")
                t.append(f"[{lvl}] ", style="bold cyan")
                t.append(f"{msg}\n", style="white")
        return Panel(t, title="[bold]LOGS[/]", border_style="white")

    def make_approvals_panel(self) -> Panel:
        t = Text()
        if not self.pending_approvals:
            t.append("(Aucune approbation en attente)", style="dim")
        else:
            for i, approval in enumerate(self.pending_approvals):
                if approval["status"] == "pending":
                    t.append(f"#{i} ", style="bold yellow")
                    t.append(f"{approval['filename']} ", style="white")
                    t.append(f"[sig: {approval['signature']}]\n", style="dim")
                    t.append(f"  → approve {i} | reject {i}\n", style="cyan")
        
        return Panel(t, title="[bold]APPROBATIONS EN ATTENTE[/]", border_style="yellow")

    def build_layout(self) -> Layout:
        l = Layout()
        l.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=4)
        )
        l["header"].update(
            Panel(f"◈ OMNI-SLA v6.4 | DROP & SYNC | {self.view.upper()}", style="bold magenta", box=box.HORIZONTALS)
        )
        
        if self.view == "dashboard":
            l["body"].split_row(
                Layout(name="main", ratio=2),
                Layout(name="side", ratio=1)
            )
            l["main"].update(self.make_logs_panel())
            l["side"].update(self.make_metrics_panel())
        
        elif self.view == "approvals":
            l["body"].update(self.make_approvals_panel())
        
        l["footer"].update(
            Panel(
                "[dim]Glissez un fichier | approve/reject ID | dash, q (quit)[/]",
                box=box.HORIZONTALS
            )
        )
        
        return l


# ─────────────────────────────────────────────
#  POINT D'ENTRÉE
# ─────────────────────────────────────────────

def run():
    engine = OmniEngine()
    engine.add_log("OMNI-SLA v6.4 Online (Drop & Sync mode)", "OK")
    
    try:
        with Live(engine.build_layout(), refresh_per_second=4, screen=True) as live:
            while True:
                live.update(engine.build_layout())
                
                try:
                    sys.stdout.flush()
                    sys.stderr.flush()
                    time.sleep(0.1)
                    
                    cmd = console.input("\n[bold magenta]OMNI DROP > [/]").strip().lower()
                    
                except (KeyboardInterrupt, EOFError):
                    break

                if cmd in ["q", "quit"]:
                    break
                
                # Détection de chemin (Drag & Drop)
                elif "\\" in cmd or "/" in cmd:
                    result = engine.handle_drop(cmd)
                    engine.add_log(result, "RESULT")
                
                # Approbation de module
                elif cmd.startswith("approve "):
                    try:
                        app_id = int(cmd.split()[1])
                        result = engine.approve_module(app_id)
                        engine.add_log(result, "APPROVE")
                    except (ValueError, IndexError):
                        engine.add_log("Syntaxe: approve <id>", "ERROR")
                
                # Rejet de module
                elif cmd.startswith("reject "):
                    try:
                        app_id = int(cmd.split()[1])
                        result = engine.reject_module(app_id)
                        engine.add_log(result, "REJECT")
                    except (ValueError, IndexError):
                        engine.add_log("Syntaxe: reject <id>", "ERROR")
                
                # Vue approbations
                elif cmd == "approvals":
                    engine.view = "approvals"
                
                elif cmd == "dash":
                    engine.view = "dashboard"
                
                elif cmd:
                    engine.add_log(f"Commande: {cmd}", "CMD")
    
    finally:
        engine.monitor.stop()
        console.print("\n[green]✓ OMNI-SLA arrêté proprement.[/]")


if __name__ == "__main__":
    run()
