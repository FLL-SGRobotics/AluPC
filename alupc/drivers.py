"""Treiber für virtuelle Geräte – installiert vom Installer (Windows) bzw. Installationsskript (Linux), oder später
mit einem Klick in AluPC (Seite „Audio“, Virtuelle Kamera).

* Virtuelles Mikro / virtueller Ausgang
  - Windows: VB-CABLE (VB-Audio, kostenlos/Spende) – vom Hersteller geladen und still installiert. Windows fragt
    beim ersten Mal selbst, ob der Treiber installiert werden darf (lässt sich nicht abschalten).
  - Linux: nichts nötig – PipeWire/PulseAudio kann das selbst (mixer.py).
* Virtuelle Kamera (Monitor 2 als Webcam)
  - Windows: virtuelle Kamera von OBS Studio (per winget installiert, Kamera-Treiber mit regsvr32 angemeldet –
    OBS selbst muss dafür nie laufen).
  - Linux: v4l2loopback (Paket v4l2loopback-dkms) mit einem Gerät „AluPC Monitor 2“ (/dev/video42), dauerhaft.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
CAM_LABEL = "AluPC Bildschirm"
OLD_CAM_LABELS = ("AluPC Monitor 2",)  # bis 0.138: Gerät mit altem Namen läuft weiter (bis zum Neustart)
V4L2_NR = 42
V4L2_OPTIONS = f'devices=1 video_nr={V4L2_NR} card_label="{CAM_LABEL}" exclusive_caps=1'
VBCABLE_URLS = ["https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip",
                "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack43.zip"]
OBS_VCAM_CLSID = "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}"  # „OBS Virtual Camera“ (DirectShow-Filter)
WHAT = {"vbcable": "Virtuelles Mikro (VB-CABLE)", "kamera": "Virtuelle Kamera", "pactl": "Ton-Werkzeuge (pactl)"}
LINUX_ONLY = {"pactl"}


# --------------------------------------------------------------------------- Skripte
def vbcable_ps() -> str:
    urls = ",".join(f"'{u}'" for u in VBCABLE_URLS)
    return f"""
$ErrorActionPreference = 'Stop'
$tmp = Join-Path $env:TEMP 'alupc-vbcable'
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp 'vbcable.zip'
$ok = $false
foreach ($u in @({urls})) {{
  try {{ Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $zip; $ok = $true; break }} catch {{ }}
}}
if (-not $ok) {{ Write-Output 'FEHLER: VB-CABLE konnte nicht geladen werden (Internet?)'; exit 2 }}
Expand-Archive -Force $zip (Join-Path $tmp 'x')
$setup = Get-ChildItem (Join-Path $tmp 'x') -Recurse -Filter 'VBCABLE_Setup_x64.exe' | Select-Object -First 1
if (-not $setup) {{ Write-Output 'FEHLER: Setup fehlt im Paket'; exit 3 }}
$p = Start-Process -FilePath $setup.FullName -ArgumentList '-i','-h' -Wait -PassThru
Start-Sleep -Seconds 3
""" + HIDE_16CH_PS + """
Write-Output "VB-CABLE: fertig ($($p.ExitCode))"
"""


# VB-CABLE legt zwei Wiedergabe-Geräte an: „CABLE Input“ und „CABLE In 16ch“ (dasselbe Kabel mit 16 Kanälen).
# Eins reicht – den 16-Kanal-Eingang im Gerätemanager abschalten, dann sehen ihn weder Windows noch Programme.
HIDE_16CH_PS = r"""
Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue |
  Where-Object { $_.FriendlyName -like '*CABLE In 16*' } |
  ForEach-Object { Disable-PnpDevice -InstanceId $_.InstanceId -Confirm:$false -ErrorAction SilentlyContinue }
"""


def vbcable_toggle_ps(on: bool) -> str:
    """VB-CABLE im Gerätemanager an- oder abschalten: aus = taucht in Windows und allen Programmen nicht mehr auf."""
    verb = "Enable-PnpDevice" if on else "Disable-PnpDevice"
    return f"""
$ErrorActionPreference = 'Continue'
$cable = @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {{ $_.FriendlyName -like 'VB-Audio Virtual*Cable*' -and $_.Class -eq 'MEDIA' }})
if ($cable.Count -eq 0) {{ Write-Output 'FEHLER: VB-CABLE ist nicht installiert'; exit 2 }}
foreach ($d in $cable) {{ {verb} -InstanceId $d.InstanceId -Confirm:$false -ErrorAction SilentlyContinue }}
Start-Sleep -Seconds 2
""" + HIDE_16CH_PS + """
Write-Output 'VB-CABLE: fertig'
"""


def vbcable_set_enabled(on: bool, run=subprocess.run) -> tuple[bool, str]:
    """Windows: VB-CABLE an/aus (fragt einmal nach Administrator-Rechten). Blockiert – im Hintergrund aufrufen."""
    if not IS_WINDOWS:
        return True, ""
    enc = encoded_command(vbcable_toggle_ps(on))
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
           "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
           f"'-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{enc}'"]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"VB-CABLE umschalten ging nicht: {exc}"
    if r.returncode != 0:
        return False, "Abgebrochen (keine Administrator-Rechte?)"
    return True, "VB-CABLE " + ("eingeschaltet." if on else "ausgeschaltet – Windows und Programme sehen es nicht mehr.")


def obs_vcam_ps() -> str:
    return r"""
$ErrorActionPreference = 'Continue'
$dir = Join-Path $env:ProgramFiles 'obs-studio\data\obs-plugins\win-dshow'
if (-not (Test-Path (Join-Path $dir 'obs-virtualcam-module64.dll'))) {
  winget install --id OBSProject.OBSStudio -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
}
$dll64 = Join-Path $dir 'obs-virtualcam-module64.dll'
$dll32 = Join-Path $dir 'obs-virtualcam-module32.dll'
if (-not (Test-Path $dll64)) { Write-Output 'FEHLER: OBS Studio ließ sich nicht installieren (winget?)'; exit 4 }
Start-Process -FilePath (Join-Path $env:WINDIR 'System32\regsvr32.exe') -ArgumentList '/s','/i',"`"$dll64`"" -Wait
if (Test-Path $dll32) {
  Start-Process -FilePath (Join-Path $env:WINDIR 'SysWOW64\regsvr32.exe') -ArgumentList '/s','/i',"`"$dll32`"" -Wait
}
Write-Output 'Virtuelle Kamera: fertig'
"""


PACTL_SCRIPT = """set -e
# pactl (Ton steuern, Mischpult, virtuelle Mikros) – kommt NICHT mit pipewire-pulse, sondern mit pulseaudio-utils
if ! command -v pactl >/dev/null; then
  if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get install -y pulseaudio-utils
    apt-mark auto pulseaudio-utils >/dev/null 2>&1 || true  # „apt autoremove“ nimmt es nach AluPC mit
  elif command -v dnf >/dev/null; then dnf install -y pulseaudio-utils
  elif command -v pacman >/dev/null; then pacman -S --noconfirm libpulse
  elif command -v zypper >/dev/null; then zypper --non-interactive install pulseaudio-utils
  else echo "FEHLER: Paketverwaltung unbekannt – pulseaudio-utils bitte selbst installieren"; exit 5; fi
fi
echo "Ton-Werkzeuge: fertig"
"""


def linux_script(what: list[str]) -> str:
    """Als Administrator (pkexec): Ton-Werkzeuge (pactl) und/oder v4l2loopback installieren (Kamera: dauerhaft mit
    dem AluPC-Gerät laden)."""
    parts = [PACTL_SCRIPT] if "pactl" in what else []
    if "kamera" in what:
        parts.append(_camera_script())
    return "\n".join(parts) or "true"


def _camera_script() -> str:
    return f"""set -e
if ! modinfo v4l2loopback >/dev/null 2>&1; then
  if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get install -y v4l2loopback-dkms
    apt-mark auto v4l2loopback-dkms >/dev/null 2>&1 || true  # „apt autoremove“ nimmt es nach AluPC mit
  elif command -v dnf >/dev/null; then dnf install -y v4l2loopback
  elif command -v pacman >/dev/null; then pacman -S --noconfirm v4l2loopback-dkms
  elif command -v zypper >/dev/null; then zypper --non-interactive install v4l2loopback-kmp-default
  else echo "FEHLER: Paketverwaltung unbekannt – v4l2loopback bitte selbst installieren"; exit 5; fi
fi
echo 'options v4l2loopback {V4L2_OPTIONS}' > /etc/modprobe.d/alupc-v4l2loopback.conf
echo v4l2loopback > /etc/modules-load.d/alupc-v4l2loopback.conf
if [ -d /sys/module/v4l2loopback ]; then
  if ! grep -qsE "^({'|'.join((CAM_LABEL,) + OLD_CAM_LABELS)})$" /sys/class/video4linux/*/name; then
    if command -v v4l2loopback-ctl >/dev/null; then v4l2loopback-ctl add -n "{CAM_LABEL}" -x 1 /dev/video{V4L2_NR} || true; fi
  fi
else
  modprobe v4l2loopback || {{ echo "FEHLER: Modul lässt sich nicht laden (Secure Boot? Dann den DKMS-Schlüssel bestätigen)"; exit 6; }}
fi
echo "Virtuelle Kamera: fertig"
"""


# --------------------------------------------------------------------------- Status
def v4l2_device() -> str:
    """Linux: Gerätedatei der virtuellen Kamera ("" = keine). Bevorzugt „AluPC Monitor 2“, sonst ein anderes
    v4l2loopback-Gerät (z. B. von OBS)."""
    base = Path("/sys/class/video4linux")
    if not base.is_dir():
        return ""
    other = ""
    for dev in sorted(base.iterdir()):
        try:
            name = (dev / "name").read_text().strip()
        except OSError:
            continue
        driver = ""
        try:
            driver = os.path.basename(os.path.realpath(dev / "device" / "driver"))
        except OSError:
            pass
        if name == CAM_LABEL or name in OLD_CAM_LABELS:
            return f"/dev/{dev.name}"
        if "loopback" in name.lower() or "v4l2loopback" in driver or "Dummy video device" in name:
            other = other or f"/dev/{dev.name}"
    return other


def vcam_installed() -> tuple[bool, str]:
    if IS_WINDOWS:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{OBS_VCAM_CLSID}"):
                return True, ""
        except OSError:
            pass
        dll = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "obs-studio" / "data" / "obs-plugins" / \
            "win-dshow" / "obs-virtualcam-module64.dll"
        return False, ("Kamera-Treiber von OBS ist da, aber nicht angemeldet – „Einrichten“ drücken." if dll.is_file()
                       else "Treiber fehlt – „Einrichten“ installiert ihn (OBS Studio, einmal Administrator).")
    if v4l2_device():
        return True, ""
    if shutil.which("modinfo") and subprocess.run(["modinfo", "v4l2loopback"], capture_output=True).returncode == 0:
        return False, "v4l2loopback ist installiert, aber nicht geladen – „Einrichten“ drücken."
    return False, "v4l2loopback fehlt – „Einrichten“ installiert es (einmal Administrator-Passwort)."


# --------------------------------------------------------------------------- Installieren
def encoded_command(script: str) -> str:
    """PowerShell -EncodedCommand für ein langes Skript: gzip-komprimiert und im Zielprozess entpackt – sonst wird die
    Befehlszeile zu lang (Windows: höchstens 32767 Zeichen)."""
    import base64
    import gzip

    packed = base64.b64encode(gzip.compress(script.encode("utf-8"), 9)).decode()
    boot = ("$z = New-Object IO.Compression.GZipStream((New-Object IO.MemoryStream(,[Convert]::FromBase64String("
            f"'{packed}'))), [IO.Compression.CompressionMode]::Decompress)\n"
            "Invoke-Expression (New-Object IO.StreamReader($z, [Text.Encoding]::UTF8)).ReadToEnd()\n")
    return base64.b64encode(boot.encode("utf-16-le")).decode()


def install(what: list[str], elevated: bool = False, run=subprocess.run) -> tuple[bool, str]:
    """Blockiert (im Hintergrund aufrufen). elevated=True: läuft schon als Administrator (Installer)."""
    what = [w for w in what if w in WHAT and not (IS_WINDOWS and w in LINUX_ONLY)]
    if not what:
        return True, ""
    try:
        if IS_WINDOWS:
            script = "\n".join(([vbcable_ps()] if "vbcable" in what else []) +
                               ([obs_vcam_ps()] if "kamera" in what else []))
            enc = encoded_command(script)
            from . import addons

            tracked = (["vbcable"] if "vbcable" in what else []) + (["obs"] if "kamera" in what else [])
            if elevated:
                cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]
            else:
                cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                       "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
                       f"'-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{enc}'"]
            r = addons.track(tracked, lambda: run(cmd, capture_output=True, text=True, timeout=900,
                                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
            out = (r.stdout or "") + (r.stderr or "")
            if "FEHLER" in out:
                return False, out.strip().splitlines()[-1][:200]
            if r.returncode != 0 and not elevated:
                return False, "Abgebrochen (keine Administrator-Rechte?)"
        else:
            if not {"kamera", "pactl"} & set(what):
                return True, ""
            prefix = [] if elevated or os.geteuid() == 0 else (["pkexec"] if shutil.which("pkexec") else ["sudo", "-n"])
            r = run([*prefix, "sh", "-c", linux_script(what)], capture_output=True, text=True, timeout=900)
            out = (r.stdout or "") + (r.stderr or "")
            if r.returncode != 0:
                line = next((ln for ln in reversed(out.splitlines()) if ln.strip()), "")
                return False, (line if "FEHLER" in line else
                               f"Einrichten ging nicht ({r.returncode}): {line}")[:200]
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Einrichten ging nicht: {exc}"
    return True, "Fertig: " + ", ".join(WHAT[w] for w in what)


def install_async(parent, what: str | list[str], done=None) -> tuple[bool, str]:
    """Aus der Oberfläche: im Hintergrund installieren, Ergebnis als Meldung."""
    from .ui.util import run_async

    items = [what] if isinstance(what, str) else list(what)

    def finished(result):
        ok, msg = result
        from PySide6.QtWidgets import QMessageBox

        (QMessageBox.information if ok else QMessageBox.warning)(parent, "AluPC – Treiber", msg or "Fertig.")
        if done is not None:
            done()

    run_async(lambda: install(items), finished)
    return True, ""


def main_install(arg: str) -> int:
    """`AluPC --treiber vbcable,kamera` (vom Installer, schon als Administrator)."""
    elevated = True
    if IS_WINDOWS:
        import ctypes

        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())  # Installer ohne Admin → Windows fragt selbst
    ok, msg = install([w.strip() for w in arg.split(",") if w.strip()], elevated=elevated)
    print(msg)
    return 0 if ok else 1
