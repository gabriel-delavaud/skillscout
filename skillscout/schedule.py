"""Tâche planifiée Windows. `skillscout routine --register` est à lancer
depuis le terminal de l'utilisateur : l'app Claude tourne en conteneur MSIX,
un enregistrement fait depuis elle n'est pas fiable."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "skillscout-routine"
DAY = "Monday"
AT = "10:00"


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def interpreter() -> str:
    """pythonw.exe à côté de l'interpréteur courant (aucune fenêtre), sinon python."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else exe)


def register_script(python: str) -> str:
    return "; ".join([
        f"$a = New-ScheduledTaskAction -Execute {_ps_quote(python)} "
        "-Argument '-m skillscout routine'",
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


def _windows(is_windows: bool | None) -> bool:
    if (os.name == "nt") if is_windows is None else is_windows:
        return True
    print("La planification automatique ne concerne que Windows.", file=sys.stderr)
    return False


def register(*, is_windows: bool | None = None) -> int:
    if not _windows(is_windows):
        return 1
    p = _powershell(register_script(interpreter()))
    if p.returncode != 0:
        print(f"Échec de l'enregistrement : {p.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"Tâche « {TASK_NAME} » enregistrée : chaque lundi à {AT}, rattrapée au "
          "démarrage suivant si le PC était éteint.")
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
