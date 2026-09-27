"""Tâche planifiée Windows. `skillscout routine --register` est à lancer
depuis le terminal de l'utilisateur : l'app Claude tourne en conteneur MSIX,
un enregistrement fait depuis elle n'est pas fiable."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import skillscout

TASK_NAME = "skillscout-routine"
DAY = "Monday"
AT = "10:00"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PREFLIGHT = "import skillscout; print(skillscout.__version__); print(skillscout.__file__)"
_FIX = ("Installez cette version pour le Python qui lancera la tâche : depuis le dossier "
        "cloné, `py -3.14 -m pip install --upgrade .` (remplace l'ancienne version, par "
        "exemple la 0.2.0), puis relancez `skillscout routine --register`.")


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def interpreter() -> str:
    """pythonw.exe à côté de l'interpréteur courant (aucune fenêtre), sinon python."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else exe)


def package_dir() -> Path:
    """Dossier qui contient le paquet `skillscout` en cours d'exécution. La tâche
    y démarre : `-m skillscout` place le dossier courant en tête de sys.path et
    y retrouve donc ce paquet-ci, et non une ancienne version installée
    ailleurs (le dossier par défaut d'une tâche est System32)."""
    return Path(skillscout.__file__).resolve().parent.parent


def register_script(python: str, workdir: str | Path) -> str:
    return "; ".join([
        f"$a = New-ScheduledTaskAction -Execute {_ps_quote(python)} "
        f"-Argument '-m skillscout routine' -WorkingDirectory {_ps_quote(str(workdir))}",
        f"$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {DAY} -At {AT}",
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) "
        "-MultipleInstances IgnoreNew",
        f"Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $a -Trigger $t "
        "-Settings $s -Description 'skillscout : decouverte et installation hebdomadaire "
        "de skills' -Force | Out-Null",
    ])


def unregister_script() -> str:
    return f"Unregister-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Confirm:$false"


def _powershell(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)


def _preflight(workdir: Path) -> subprocess.CompletedProcess:
    """Ce que la tâche importera : même interpréteur, même dossier de départ."""
    return subprocess.run([sys.executable, "-c", _PREFLIGHT], cwd=workdir,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60, creationflags=_NO_WINDOW)


def preflight_problem(workdir: Path) -> str | None:
    """Vérifie, avant d'enregistrer, que la tâche lancera exactement ce
    skillscout-ci (version et fichier). Renvoie la raison d'un refus, ou None."""
    here = Path(skillscout.__file__).resolve()
    try:
        p = _preflight(workdir)
    except (OSError, subprocess.SubprocessError) as e:
        return f"vérification impossible depuis {workdir} ({type(e).__name__})"
    lines = (p.stdout or "").strip().splitlines()
    if p.returncode != 0 or len(lines) < 2:
        detail = ((p.stderr or "").strip().splitlines() or ["sortie illisible"])[-1]
        return f"depuis {workdir}, {sys.executable} n'importe pas skillscout ({detail})"
    version, file = lines[-2].strip(), lines[-1].strip()
    try:
        same_file = Path(file).resolve() == here
    except (OSError, ValueError):
        same_file = False
    if version != skillscout.__version__ or not same_file:
        return (f"la tâche lancerait skillscout {version} ({file}) et non la version "
                f"{skillscout.__version__} en cours ({here})")
    return None


def _windows(is_windows: bool | None) -> bool:
    if (os.name == "nt") if is_windows is None else is_windows:
        return True
    print("La planification automatique ne concerne que Windows.", file=sys.stderr)
    return False


def register(*, is_windows: bool | None = None) -> int:
    if not _windows(is_windows):
        return 1
    workdir = package_dir()
    problem = preflight_problem(workdir)
    if problem:
        print(f"Enregistrement refusé : {problem}.", file=sys.stderr)
        print(_FIX, file=sys.stderr)
        return 1
    p = _powershell(register_script(interpreter(), workdir))
    if p.returncode != 0:
        print(f"Échec de l'enregistrement : {p.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"Tâche « {TASK_NAME} » enregistrée : chaque lundi à {AT}, rattrapée au "
          "démarrage suivant si le PC était éteint.")
    print(f"Elle lancera skillscout {skillscout.__version__} depuis {workdir}.")
    print("Jev lit sa clé dans la variable utilisateur TYPESAFE_API_KEY.")
    return 0


def unregister(*, is_windows: bool | None = None) -> int:
    if not _windows(is_windows):
        return 1
    p = _powershell(unregister_script())
    if p.returncode != 0:
        print(f"Échec de la suppression : {p.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"Tâche « {TASK_NAME} » supprimée.")
    return 0
