"""
Installatore grafico dell'agent Windows.

Due modi:
  - INSTALLATORE UNICO (logplatform-agent-setup-<cliente>.exe, scaricato dal pannello):
    contiene l'agent (agent_payload.zip incorporato dalla build) e, in coda al
    file, la configurazione e i certificati del cliente. Installa in
    C:\\Program Files\\LogPlatform Agent, una cartella che solo Amministratori e
    SYSTEM possono modificare o leggere; se c'era una versione installata altrove
    ne riprende lo stato (posizione nei registri eventi) e propone di cancellarla.
  - CARTELLA ESTRATTA (vecchio pacchetto zip, installer_gui.exe accanto a
    logplatform-agent.exe): installa il servizio dove si trova, dopo aver tolto
    agli utenti normali i permessi sulla cartella.

In entrambi i casi: servizio con avvio automatico e riavvio in caso di arresto
imprevisto, blocco USB facoltativo.

Va eseguito come Amministratore (il manifest PyInstaller chiede l'elevazione).
"""
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

from installer_logic import (
    set_usb_block_in_ini, detect_existing_installation,
    build_sc_create_args, build_sc_failure_args, build_icacls_lockdown_commands,
    read_tenant_payload_from_file, default_install_dir, exe_dir_from_image_path,
)

SERVICE_NAME = "LogPlatformAgent"
EXE_NAME = "logplatform-agent.exe"
STATE_FILE = "agent_state.json"


def is_admin() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_sc(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["sc.exe", *args], capture_output=True, text=True)


def check_service_exists() -> bool:
    result = run_sc("query", SERVICE_NAME)
    return detect_existing_installation(result.returncode)


def current_service_dir() -> str | None:
    """Cartella da cui parte il servizio già installato (dal registro, indipendente dalla lingua)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            rf"SYSTEM\CurrentControlSet\Services\{SERVICE_NAME}") as key:
            return exe_dir_from_image_path(winreg.QueryValueEx(key, "ImagePath")[0])
    except OSError:
        return None


def embedded_agent_zip() -> str | None:
    """agent_payload.zip incorporato nell'installatore unico (None nel vecchio pacchetto)."""
    base = getattr(sys, "_MEIPASS", None)
    path = os.path.join(base, "agent_payload.zip") if base else None
    return path if path and os.path.isfile(path) else None


def lock_down_folder(folder: str) -> tuple:
    """Solo Amministratori e SYSTEM sulla cartella dell'agent. Ritorna (ok, messaggio)."""
    for args in build_icacls_lockdown_commands(folder):
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            dettaglio = (result.stderr or result.stdout or "").strip()
            return False, f"Impossibile proteggere la cartella {folder}:\n{dettaglio}"
    return True, ""


def remove_service() -> None:
    if check_service_exists():
        run_sc("stop", SERVICE_NAME)
        time.sleep(3)
        run_sc("delete", SERVICE_NAME)
        time.sleep(2)


def create_and_start_service(exe_path: str) -> tuple:
    result = run_sc(*build_sc_create_args(SERVICE_NAME, exe_path))
    if result.returncode != 0:
        return False, f"Creazione del servizio non riuscita:\n{result.stderr or result.stdout}"
    run_sc(*build_sc_failure_args(SERVICE_NAME))
    result = run_sc("start", SERVICE_NAME)
    if result.returncode != 0:
        return False, f"Il servizio è stato creato ma non è partito:\n{result.stderr or result.stdout}"
    return True, "Servizio installato e avviato correttamente."


def install_from_setup(agent_zip: str, config: dict, block_usb: bool) -> tuple:
    """Installatore unico: Program Files, permessi stretti, configurazione del cliente.
    Ritorna (ok, messaggio, cartella precedente da proporre di cancellare o None)."""
    target = default_install_dir()
    old_dir = current_service_dir()
    remove_service()

    os.makedirs(target, exist_ok=True)
    # Permessi stretti PRIMA di scrivere i file: nessun momento in cui un utente
    # normale possa infilare qualcosa nella cartella del servizio.
    ok, msg = lock_down_folder(target)
    if not ok:
        return False, msg, None

    with zipfile.ZipFile(agent_zip) as z:
        for entry in z.infolist():
            name = entry.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p]
            if not parts or name.startswith("/") or ":" in name or any(p in (".", "..") for p in parts):
                return False, f"Voce non ammessa nell'installatore: {entry.filename}", None
            dest = os.path.join(target, *parts)
            if entry.is_dir():
                os.makedirs(dest, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(entry) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    for name, data in config.items():
        with open(os.path.join(target, name), "wb") as out:
            out.write(data)

    # Stato della vecchia installazione (fin dove erano stati letti i registri eventi)
    moved_from = None
    if old_dir and os.path.normcase(os.path.abspath(old_dir)) != os.path.normcase(os.path.abspath(target)):
        moved_from = old_dir
        old_state = os.path.join(old_dir, STATE_FILE)
        if os.path.isfile(old_state) and not os.path.exists(os.path.join(target, STATE_FILE)):
            shutil.copy2(old_state, os.path.join(target, STATE_FILE))

    # Ripassa i permessi su tutto quello che è stato appena scritto
    ok, msg = lock_down_folder(target)
    if not ok:
        return False, msg, None
    set_usb_block_in_ini(os.path.join(target, "agent.ini"), block_usb)

    ok, msg = create_and_start_service(os.path.join(target, EXE_NAME))
    if ok:
        msg = f"Agent installato in:\n{target}\n\n{msg}"
    return ok, msg, (moved_from if ok else None)


def install_in_place(folder: str, block_usb: bool) -> tuple:
    """Vecchio pacchetto zip: servizio dalla cartella estratta, dopo averla protetta."""
    remove_service()
    ok, msg = lock_down_folder(folder)
    if not ok:
        return False, msg
    ini = os.path.join(folder, "agent.ini")
    if os.path.exists(ini):
        set_usb_block_in_ini(ini, block_usb)
    return create_and_start_service(os.path.join(folder, EXE_NAME))


def self_check(out_path: str) -> None:
    """Usato dalla build: verifica che l'installatore veda l'agent incorporato e la
    configurazione in coda, senza installare niente."""
    try:
        config = read_tenant_payload_from_file(sys.executable)
        result = {"ok": True, "agent_zip": bool(embedded_agent_zip()),
                  "config_files": sorted(config) if config else []}
        if result["agent_zip"]:
            with zipfile.ZipFile(embedded_agent_zip()) as z:
                result["has_agent_exe"] = EXE_NAME in z.namelist()
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "error": str(e)}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f)


class InstallerApp:
    def __init__(self, root):
        import tkinter as tk

        self.tk = tk
        self.root = root
        root.title("Installazione Log Platform Agent")
        root.geometry("480x340")
        root.resizable(False, False)

        self.agent_zip = embedded_agent_zip()
        self.config = None
        self.config_error = None
        if self.agent_zip:
            try:
                self.config = read_tenant_payload_from_file(sys.executable)
            except Exception as e:  # noqa: BLE001
                self.config_error = str(e)
        self.folder = os.path.dirname(os.path.abspath(sys.argv[0]))

        tk.Label(root, text="Log Platform Agent", font=("Segoe UI", 14, "bold")).pack(pady=(15, 5))

        if self.agent_zip:
            tenant = ""
            if self.config:
                for line in self.config["agent.ini"].decode("utf-8", "replace").splitlines():
                    if line.strip().startswith("tenant"):
                        tenant = line.split("=", 1)[-1].strip()
            where = f"Verrà installato in {default_install_dir()}"
            tk.Label(root, text=(f"Cliente: {tenant}\n{where}" if tenant else where), wraplength=440).pack(pady=5)
            if not self.config:
                tk.Label(root, text=("Questo installatore non contiene la configurazione del cliente: "
                                     "scaricalo dal pannello Log Platform (Clienti → Windows)."
                                     + (f"\n({self.config_error})" if self.config_error else "")),
                         fg="#dc2626", wraplength=440).pack(pady=5)
        elif not os.path.exists(os.path.join(self.folder, EXE_NAME)):
            tk.Label(root, text=f"ATTENZIONE: {EXE_NAME} non trovato in questa cartella.",
                     fg="#dc2626", wraplength=440).pack(pady=5)

        existing = check_service_exists()
        tk.Label(root, text=("Rilevata un'installazione precedente: verrà sostituita."
                             if existing else "Nessuna installazione precedente rilevata."),
                 wraplength=440).pack(pady=5)

        self.usb_var = tk.BooleanVar(value=False)
        tk.Checkbutton(root, text="Blocca storage USB (chiavette, dischi esterni)",
                       variable=self.usb_var).pack(pady=8)
        tk.Label(root, text="(non blocca tastiera/mouse/stampanti - solo lo storage)",
                 font=("Segoe UI", 8), fg="#64748b").pack()

        if not is_admin():
            tk.Label(root, text="Esegui questo programma come Amministratore per procedere.",
                     fg="#dc2626", wraplength=440).pack(pady=8)

        self.install_btn = tk.Button(root, text="Installa", command=self.on_install, width=20, height=2)
        self.install_btn.pack(pady=12)
        self.status_label = tk.Label(root, text="", wraplength=440)
        self.status_label.pack(pady=5)

    def on_install(self):
        from tkinter import messagebox

        if not is_admin():
            messagebox.showerror("Permessi insufficienti", "Esegui questo programma come Amministratore.")
            return
        if self.agent_zip and not self.config:
            messagebox.showerror("Configurazione mancante",
                                 "Scarica l'installatore del cliente dal pannello Log Platform.")
            return
        if not self.agent_zip and not os.path.exists(os.path.join(self.folder, EXE_NAME)):
            messagebox.showerror("File mancante",
                                 f"Non trovo {EXE_NAME}.\nEsegui questo programma dalla cartella dell'agent.")
            return

        self.install_btn.config(state=self.tk.DISABLED)
        self.status_label.config(text="Installazione in corso...")
        self.root.update()

        old_dir = None
        try:
            if self.agent_zip:
                ok, msg, old_dir = install_from_setup(self.agent_zip, self.config, self.usb_var.get())
            else:
                ok, msg = install_in_place(self.folder, self.usb_var.get())
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"Errore durante l'installazione:\n{e}"

        if ok:
            messagebox.showinfo("Fatto", msg)
            if old_dir and os.path.isfile(os.path.join(old_dir, EXE_NAME)) and messagebox.askyesno(
                    "Vecchia installazione",
                    f"L'agent prima era in:\n{old_dir}\n\nQuella cartella contiene ancora il vecchio "
                    "certificato e la sua chiave privata. Cancellarla?"):
                shutil.rmtree(old_dir, ignore_errors=True)
        else:
            messagebox.showerror("Errore", msg)
        self.status_label.config(text=msg)
        self.install_btn.config(state=self.tk.NORMAL)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--verifica":
        self_check(sys.argv[2])
        sys.exit(0)
    import tkinter as tk
    root = tk.Tk()
    InstallerApp(root)
    root.mainloop()
