"""Windows: Anmelden und Entsperren mit einem Fingerabdruckmodul am seriellen Anschluss (z. B. HLK-ZW101).

Windows Hello nimmt solche Module nicht an. AluPC bringt deshalb einen eigenen Anmeldebaustein mit
(„Credential Provider“, `credprov\\AluPCFingerprint.dll`, Quelltext in packaging/windows/credprov): eine Kachel
„Fingerabdruck (AluPC)“ auf dem Anmelde- und Sperrbildschirm. Erkennt das Modul einen angelernten Finger,
meldet der Baustein den zugehörigen Benutzer mit dessen Windows-Passwort an.

* Das Passwort prüft AluPC beim Einrichten bei Windows (LogonUser) und legt es mit DPAPI (Maschine)
  verschlüsselt in %ProgramData%\\AluPC\\fingerprint-windows.cfg ab – lesen dürfen die Datei nur SYSTEM und
  Administratoren. Ändert man sein Windows-Passwort, muss man die Anmeldung in AluPC neu einrichten.
* Schreiben der Datei und Anmelden des Bausteins bei Windows brauchen Administratorrechte: AluPC startet
  sich dafür einmal mit `--fingerabdruck-windows <Auftrag>` über die Windows-Abfrage (UAC).
* Das Passwort funktioniert immer weiter; die Kachel ist nur ein zusätzlicher Weg.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CLSID = "{82F9D550-26AE-40CC-B8F7-F9805D8AD0EF}"
CP_KEY = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{CLSID}"
CLSID_KEY = rf"SOFTWARE\Classes\CLSID\{CLSID}"
POLICY_KEY = r"SOFTWARE\Policies\Microsoft\Windows\System"
ENTROPY = b"AluPC-Fingerabdruck"
DLL_NAME = "AluPCFingerprint.dll"
IS_WINDOWS = sys.platform.startswith("win")


class LoginError(RuntimeError):
    pass


# --------------------------------------------------------------------------- Dateien
def dll_path() -> Path | None:
    """Der mitgelieferte Anmeldebaustein (nur in der fertigen Windows-Version)."""
    env = os.environ.get("ALUPC_CREDPROV_DLL")
    candidates = [Path(env)] if env else []
    base = Path(sys.executable).parent
    candidates += [base / "credprov" / DLL_NAME, base / "_internal" / "credprov" / DLL_NAME]
    return next((p for p in candidates if p.is_file()), None)


def available() -> bool:
    return IS_WINDOWS and dll_path() is not None


def config_path() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "AluPC" / "fingerprint-windows.cfg"


def slots_path(user: str, base: Path | None = None) -> Path:
    """Plätze eines Benutzers, die er selbst (ohne Administratorrechte) schreiben darf – neue Finger/Personen
    wirken so sofort. Die Datei wird beim Einschalten mit Besitzer „Administratoren“ angelegt; der
    Anmeldebaustein liest sie nur dann (sonst gilt die Liste aus fingerprint-windows.cfg)."""
    safe = "".join(c if (c.isascii() and c.isalnum()) or c in "._- " else "_" for c in user) or "x"
    return (base or config_path().parent) / f"fingerprint-{safe}.slots"


def format_slots(slots: list[int], names: dict | None = None) -> str:
    """1. Zeile: Plätze („1,4,9“). Danach je Platz der Name der Person („4=Lena“) – nur für die Anzeige
    „Hallo Lena“ auf dem Sperrbildschirm."""
    wanted = sorted({int(s) for s in slots})
    lines = [",".join(str(s) for s in wanted)]
    for slot in wanted:
        name = " ".join(str((names or {}).get(slot, "")).split())[:60].replace("=", "-")
        if name:
            lines.append(f"{slot}={name}")
    return "\n".join(lines) + "\n"


def write_own_slots(slots: list[int], user: str | None = None, names: dict | None = None) -> bool:
    """Ohne Administratorrechte: eigene Plätze-Datei überschreiben (Rechte/Besitzer bleiben). False = geht nicht."""
    path = slots_path(user or current_account()[0])
    if not path.is_file():
        return False
    try:
        with open(path, "r+", encoding="utf-8", newline="\n") as f:  # vorhandene Datei, nicht neu anlegen
            f.seek(0)
            f.write(format_slots(slots, names))
            f.truncate()
        return True
    except OSError:
        return False


def current_sid() -> str:
    """SID des angemeldeten Benutzers (für die Rechte an seiner Plätze-Datei)."""
    if not IS_WINDOWS:
        return ""
    try:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             creationflags=0x08000000).stdout
    except OSError:
        return ""
    sid = out.strip().rsplit(",", 1)[-1].strip().strip('"')
    return sid if re.fullmatch(r"S-1-5-21(-\d+)+", sid) else ""


def format_config(cfg: dict) -> str:
    lines = [f"port={cfg.get('port', '')}", f"baud={int(cfg.get('baud', 57600))}",
             f"capacity={int(cfg.get('capacity', 300))}"]
    for name, u in sorted(cfg.get("users", {}).items()):
        slots = ",".join(str(int(s)) for s in u.get("slots", []))
        if slots and u.get("secret"):
            lines.append(f"user={name}\t{u.get('domain') or '.'}\t{slots}\t{u['secret']}")
    return "\n".join(lines) + "\n"


def parse_config(text: str) -> dict:
    cfg: dict = {"users": {}}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in ("port",):
            cfg["port"] = value
        elif key in ("baud", "capacity"):
            try:
                cfg[key] = int(value)
            except ValueError:
                pass
        elif key == "user":
            parts = value.split("\t")
            if len(parts) >= 4:
                cfg["users"][parts[0]] = {"domain": parts[1], "secret": parts[3],
                                          "slots": [int(s) for s in parts[2].split(",") if s.strip().isdigit()]}
    return cfg


def read_config(path: Path | None = None) -> dict:
    try:
        return parse_config((path or config_path()).read_text(encoding="utf-8"))
    except OSError:
        return {"users": {}}


# --------------------------------------------------------------------------- Windows-Funktionen (ctypes)
def _blob_type():
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    return DATA_BLOB


def protect(password: str) -> str:
    """DPAPI mit Maschinenschlüssel (entschlüsselbar für jeden Prozess dieses PCs, der die Datei lesen darf –
    also nur SYSTEM/Administratoren, siehe `secure_file`). Ergebnis als Hex."""
    import ctypes

    DATA_BLOB = _blob_type()
    data = password.encode("utf-8")
    inp = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    ent = DATA_BLOB(len(ENTROPY), ctypes.cast(ctypes.create_string_buffer(ENTROPY, len(ENTROPY)),
                                              ctypes.POINTER(ctypes.c_char)))
    out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN, CRYPTPROTECT_LOCAL_MACHINE = 0x1, 0x4
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), "AluPC", ctypes.byref(ent), None, None,
                                                  CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE,
                                                  ctypes.byref(out)):
        raise LoginError("Verschlüsseln fehlgeschlagen (DPAPI)")
    try:
        return ctypes.string_at(out.pbData, out.cbData).hex()
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def current_account() -> tuple[str, str]:
    """(Benutzername, Domäne) – lokale Konten und mit Microsoft-Konto verknüpfte: Domäne „.“."""
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    if not domain or domain.upper() == os.environ.get("COMPUTERNAME", "").upper():
        domain = "."
    return user, domain


def verify_password(user: str, domain: str, password: str) -> bool:
    """Stimmt das Windows-Passwort? (LogonUser – dasselbe, was Windows beim Anmelden prüft)"""
    import ctypes
    from ctypes import wintypes

    token = wintypes.HANDLE()
    ok = ctypes.windll.advapi32.LogonUserW(user, domain or ".", password, 2, 0, ctypes.byref(token))  # INTERACTIVE
    if ok:
        ctypes.windll.kernel32.CloseHandle(token)
    return bool(ok)


def registered() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CP_KEY))
        return True
    except OSError:
        return False


def login_enabled(user: str | None = None) -> bool | None:
    """Ist die Anmeldung für diesen Benutzer an? AluPC läuft ohne Administratorrechte und darf die geschützte
    fingerprint-windows.cfg NICHT lesen (Fehler bis 0.54: AluPC hielt die Anmeldung deshalb für aus und trug
    neue Finger nie nach – nur der erste Finger ging). Maßgeblich ist die eigene Plätze-Datei.
    None = eingeschaltet mit älterer Version, Zustand nicht lesbar (→ einmal aus- und wieder einschalten)."""
    user = user or current_account()[0]
    if not registered():
        return False
    if slots_path(user).is_file():
        return True
    try:
        text = config_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError:  # geschützt → nicht lesbar
        return None
    return user in parse_config(text).get("users", {})


def own_slots_file_state(slots: list[int], names: dict | None = None, user: str | None = None) -> str:
    """Kennt die Windows-Anmeldung genau die angelernten Finger? „ok“, „veraltet“ oder „fehlt“."""
    path = slots_path(user or current_account()[0])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "fehlt"
    return "ok" if text == format_slots(slots, names) else "veraltet"


# --------------------------------------------------------------------------- mit Administratorrechten
def register(dll: Path) -> None:
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, CLSID_KEY, 0, winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "AluPC Fingerabdruck")
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, CLSID_KEY + r"\InprocServer32", 0,
                            winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(dll))
        winreg.SetValueEx(k, "ThreadingModel", 0, winreg.REG_SZ, "Apartment")
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, CP_KEY, 0, winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "AluPC Fingerabdruck")
    set_default_provider(True)


def _default_provider() -> str:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return str(winreg.QueryValueEx(k, "DefaultCredentialProvider")[0])
    except OSError:
        return ""


def set_default_provider(on: bool) -> None:
    """Fingerabdruck als vorausgewählte Anmeldeoption (Windows-Richtlinie „Standard-Anmeldeinformationsanbieter“).
    So muss man auf dem Sperrbildschirm nichts anklicken – Finger auflegen genügt. Eine fremde Vorgabe
    (z. B. von der Schul-IT) wird nie überschrieben."""
    import winreg

    current = _default_provider()
    if on and current.upper() in ("", CLSID.upper()):
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0,
                                winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, "DefaultCredentialProvider", 0, winreg.REG_SZ, CLSID)
    elif not on and current.upper() == CLSID.upper():
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0, winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as k:
            winreg.DeleteValue(k, "DefaultCredentialProvider")


def unregister() -> None:
    import winreg

    try:
        set_default_provider(False)
    except OSError:
        pass
    for key in (CP_KEY, CLSID_KEY + r"\InprocServer32", CLSID_KEY):
        try:
            winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, key, winreg.KEY_WOW64_64KEY)
        except OSError:
            pass


def secure_file(path: Path) -> None:
    """Nur SYSTEM und Administratoren dürfen lesen (dort liegt das verschlüsselte Passwort)."""
    flags = 0x08000000  # CREATE_NO_WINDOW
    result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", "*S-1-5-18:F", "*S-1-5-32-544:F"],
                            capture_output=True, text=True, creationflags=flags)
    if result.returncode != 0:
        raise LoginError(f"Zugriffsrechte nicht setzbar: {(result.stdout + result.stderr).strip()}")


def make_slots_file(user: str, slots: list[int], sid: str, folder: Path) -> None:
    """(mit Administratorrechten) Plätze-Datei neu anlegen: Besitzer Administratoren, der Benutzer darf ändern."""
    path = slots_path(user, folder)
    if not re.fullmatch(r"S-1-5-21(-\d+)+", sid or ""):
        return  # ohne gültige SID: keine Datei → AluPC nimmt wie bisher den Weg über Administratorrechte
    try:
        path.unlink()  # evtl. vorher von jemand anderem angelegt → weg damit
    except OSError:
        pass
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(format_slots(slots), encoding="utf-8")
    flags = 0x08000000
    for args in (["/setowner", "*S-1-5-32-544"],
                 ["/inheritance:r", "/grant:r", "*S-1-5-18:F", "*S-1-5-32-544:F", f"*{sid}:M"]):
        result = subprocess.run(["icacls", str(path), *args], capture_output=True, text=True, creationflags=flags)
        if result.returncode != 0:
            path.unlink(missing_ok=True)
            raise LoginError(f"Zugriffsrechte nicht setzbar: {(result.stdout + result.stderr).strip()}")


def apply_request(request: dict, path: Path | None = None, dll: Path | None = None) -> None:
    """Auftrag ausführen (läuft mit Administratorrechten): „an“ (Benutzer eintragen), „plaetze“ (nur die
    Finger aktualisieren) oder „aus“ (Benutzer entfernen; ist keiner mehr übrig, Baustein abmelden)."""
    path = path or config_path()
    cfg = read_config(path)
    users = cfg.setdefault("users", {})
    user = request["user"]
    action = request["action"]
    for key in ("port", "baud", "capacity"):
        if request.get(key):
            cfg[key] = request[key]
    if action == "an":
        users[user] = {"domain": request.get("domain", "."), "slots": request["slots"], "secret": request["secret"]}
        make_slots_file(user, request["slots"], request.get("sid", ""), path.parent)
    elif action == "plaetze" and user in users:
        users[user]["slots"] = request["slots"]
    elif action == "aus":
        users.pop(user, None)
        try:
            slots_path(user, path.parent).unlink()
        except OSError:
            pass
    users = {k: v for k, v in users.items() if v.get("slots")}
    cfg["users"] = users
    if not users:
        try:
            path.unlink()
        except OSError:
            pass
        unregister()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_config(cfg), encoding="utf-8")
    secure_file(path)
    dll = dll or dll_path()
    if dll is None:
        raise LoginError("Anmeldebaustein (AluPCFingerprint.dll) fehlt – bitte AluPC neu installieren")
    register(dll)


def run_request_file(file: str) -> int:
    """Einstieg für `alupc --fingerabdruck-windows <Auftrag>` (mit Administratorrechten gestartet)."""
    result = Path(file + ".fehler")
    try:
        request = json.loads(Path(file).read_text(encoding="utf-8"))
        apply_request(request)
        return 0
    except Exception as exc:  # noqa: BLE001
        try:
            result.write_text(str(exc), encoding="utf-8")
        except OSError:
            pass
        return 1


def request_elevated(request: dict) -> None:
    """AluPC einmal mit Administratorrechten starten (Windows fragt), Auftrag ausführen lassen, warten."""
    import ctypes
    from ctypes import wintypes

    fd, name = tempfile.mkstemp(prefix="alupc-anmeldung-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(request, f)
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, f'--fingerabdruck-windows "{name}"'
    else:
        exe, params = sys.executable, f'-m alupc --fingerabdruck-windows "{name}"'

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb, info.lpFile, info.lpParameters, info.nShow = "runas", exe, params, 0
    try:
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
            raise LoginError("Abgebrochen – ohne Administratorrechte geht es nicht")
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 120_000)
        code = wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
        err = Path(name + ".fehler")
        if code.value != 0:
            text = err.read_text(encoding="utf-8") if err.exists() else f"Fehlercode {code.value}"
            raise LoginError(text)
    finally:
        for p in (Path(name), Path(name + ".fehler")):
            try:
                p.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- für AluPC (ohne Adminrechte)
def enable(password: str, slots: list[int], port: str, baud: int, capacity: int) -> None:
    user, domain = current_account()
    if not slots:
        raise LoginError("Erst mindestens einen Finger anlernen")
    if not verify_password(user, domain, password):
        raise LoginError("Das Windows-Passwort stimmt nicht (bei Microsoft-Konten: das Passwort des Kontos, "
                         "nicht die PIN)")
    request_elevated({"action": "an", "user": user, "domain": domain, "slots": sorted(slots), "port": port,
                      "baud": baud, "capacity": capacity, "secret": protect(password), "sid": current_sid()})


def update_slots(slots: list[int], port: str, baud: int, capacity: int) -> None:
    user, _ = current_account()
    request_elevated({"action": "plaetze" if slots else "aus", "user": user, "slots": sorted(slots), "port": port,
                      "baud": baud, "capacity": capacity})


def disable() -> None:
    user, _ = current_account()
    request_elevated({"action": "aus", "user": user, "slots": []})
