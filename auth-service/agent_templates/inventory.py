"""
Raccolta periodica di inventario hardware/software/risorse, inviata come
messaggio GELF speciale (log_class=inventory) insieme ai log normali.
Solo libreria standard: nessuna dipendenza esterna da installare sui
client, oltre a pywin32 già richiesto dall'agent Windows.

Il formato dell'inventario è identico su Linux e Windows (stesso schema
JSON), così il pannello/le ricerche possono trattarlo allo stesso modo
indipendentemente dalla piattaforma del client.
"""
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ---------------------------------------------------------------- Linux ---

def _linux_cpu():
    model = "sconosciuto"
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    return {"model": model, "cores": os.cpu_count() or 0}


def _linux_memory():
    total_kb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                    break
    except OSError:
        pass
    return {"total_mb": round(total_kb / 1024)}


def _linux_disks():
    disks = []
    seen_devices = set()
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mount_point, fstype = parts[0], parts[1], parts[2]
                if fstype in ("proc", "sysfs", "devtmpfs", "tmpfs", "cgroup", "cgroup2",
                              "overlay", "squashfs", "devpts", "mqueue", "debugfs",
                              "tracefs", "securityfs", "pstore", "bpf", "autofs"):
                    continue
                if device in seen_devices:
                    continue
                usage = _safe(lambda: shutil.disk_usage(mount_point))
                if not usage:
                    continue
                seen_devices.add(device)
                disks.append({
                    "mount": mount_point,
                    "device": device,
                    "total_gb": round(usage.total / (1024 ** 3), 1),
                    "used_gb": round((usage.total - usage.free) / (1024 ** 3), 1),
                })
    except OSError:
        pass
    return disks


def _linux_network():
    interfaces = []
    net_dir = "/sys/class/net"
    try:
        for iface in os.listdir(net_dir):
            if iface == "lo":
                continue
            mac = _safe(lambda: open(f"{net_dir}/{iface}/address").read().strip(), "")
            interfaces.append({"name": iface, "mac": mac})
    except OSError:
        pass
    return interfaces


def _linux_software():
    # Prova dpkg (Debian/Ubuntu), poi rpm (RHEL/CentOS/Rocky), altrimenti vuoto.
    try:
        out = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package}\\t${Version}\\n"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0 and out.stdout.strip():
            return [{"name": n, "version": v} for n, _, v in
                    (line.partition("\t") for line in out.stdout.strip().split("\n"))]
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        out = subprocess.run(
            ["rpm", "-qa", "--qf", "%{NAME}\\t%{VERSION}\\n"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0 and out.stdout.strip():
            return [{"name": n, "version": v} for n, _, v in
                    (line.partition("\t") for line in out.stdout.strip().split("\n"))]
    except (OSError, subprocess.TimeoutExpired):
        pass

    return []


def _linux_os_info():
    info = {"family": "Linux"}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    info["name"] = line.split("=", 1)[1].strip().strip('"')
                    break
    except OSError:
        info["name"] = platform.platform()
    return info


# -------------------------------------------------------------- Windows ---

def _windows_cpu():
    model = "sconosciuto"
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        model = winreg.QueryValueEx(key, "ProcessorNameString")[0]
    except Exception:
        pass
    return {"model": model, "cores": os.cpu_count() or 0}


def _windows_memory():
    total_mb = 0
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        total_mb = round(stat.ullTotalPhys / (1024 * 1024))
    except Exception:
        pass
    return {"total_mb": total_mb}


def _windows_disks():
    disks = []
    import string
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            usage = _safe(lambda: shutil.disk_usage(drive))
            if usage:
                disks.append({
                    "mount": drive, "device": drive,
                    "total_gb": round(usage.total / (1024 ** 3), 1),
                    "used_gb": round((usage.total - usage.free) / (1024 ** 3), 1),
                })
    return disks


def _windows_network():
    interfaces = []
    try:
        hostname = socket.gethostname()
        _, _, ip_list = socket.gethostbyname_ex(hostname)
        for ip in ip_list:
            interfaces.append({"name": "?", "ip": ip})
    except Exception:
        pass
    return interfaces


def _windows_software():
    """Elenca i programmi installati leggendo il registro (stesso approccio
    usato dal Pannello di Controllo di Windows), sia a 32 sia a 64 bit."""
    software = []
    try:
        import winreg
        uninstall_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, path in uninstall_paths:
            try:
                key = winreg.OpenKey(hive, path)
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)
                    name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                    try:
                        version = winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                    except OSError:
                        version = ""
                    software.append({"name": name, "version": version})
                except OSError:
                    continue
    except Exception:
        pass
    return software


def _windows_os_info():
    """platform.platform() riporta 'Windows-10' anche su Windows 11: per
    compatibilita' storica, entrambi condividono lo schema di numerazione
    versione major.minor 10.0 - la build (>= 22000 per Windows 11, < 22000
    per Windows 10) e' l'unico modo affidabile per distinguerli. Scoperto
    con un dato reale: un PC Windows 11 (build 26200) veniva riportato
    come 'Windows-10-10.0.26200-SP0'."""
    try:
        v = sys.getwindowsversion()
        build = v.build
        if v.major == 10 and build >= 22000:
            name = f"Windows 11 (build {build})"
        elif v.major == 10:
            name = f"Windows 10 (build {build})"
        else:
            name = platform.platform()
    except AttributeError:
        # sys.getwindowsversion() esiste solo su Windows: fallback per
        # completezza (non dovrebbe capitare, questa funzione viene
        # chiamata solo quando platform.system() == "Windows").
        name = platform.platform()
    return {"family": "Windows", "name": name}


# -------------------------------------------------------------- comune ---

def collect_inventory(tenant: str, hostname: str) -> dict:
    """Ritorna lo snapshot di inventario completo per questa macchina."""
    is_windows = platform.system() == "Windows"

    cpu = _windows_cpu() if is_windows else _linux_cpu()
    memory = _windows_memory() if is_windows else _linux_memory()
    disks = _windows_disks() if is_windows else _linux_disks()
    network = _windows_network() if is_windows else _linux_network()
    software = _windows_software() if is_windows else _linux_software()
    os_info = _windows_os_info() if is_windows else _linux_os_info()

    return {
        "tenant": tenant,
        "hostname": hostname,
        "collected_at": time.time(),
        "os": os_info,
        "cpu": cpu,
        "memory": memory,
        "disks": disks,
        "network": network,
        "software_count": len(software),
        "software": software,
    }


if __name__ == "__main__":
    # Test manuale: python3 inventory.py
    inv = collect_inventory("test-tenant", socket.gethostname())
    print(json.dumps(inv, indent=2, ensure_ascii=False)[:3000])
    print(f"\n... totale pacchetti software rilevati: {inv['software_count']}")


def build_inventory_gelf_message(tenant: str, hostname: str, build_gelf_message_fn) -> bytes:
    """Costruisce il messaggio GELF per un inventario, usando la stessa
    funzione build_gelf_message già usata per i log normali (passata come
    parametro per evitare un giro di import circolare tra i moduli)."""
    inv = collect_inventory(tenant, hostname)
    summary = (f"Inventario: {inv['os'].get('name', '?')}, "
               f"{inv['cpu']['cores']} core, {inv['memory']['total_mb']} MB RAM, "
               f"{inv['software_count']} pacchetti software")
    return build_gelf_message_fn(
        tenant, hostname, summary,
        full_message=json.dumps(inv, ensure_ascii=False),
        level=6,
        extra={"log_class": "inventory"},
    )
