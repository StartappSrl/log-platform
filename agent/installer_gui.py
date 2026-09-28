"""
Installer grafico per l'agent Windows - rileva un'installazione
precedente, permette di scegliere il blocco USB, installa/reinstalla il
servizio con avvio automatico e riavvio automatico in caso di arresto
imprevisto (stesso comportamento di install.bat, ma con un'interfaccia
invece di un terminale).

Va eseguito come Amministratore (il manifest PyInstaller richiede
l'elevazione automaticamente - vedi le note di build).
"""
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox

from installer_logic import (
    set_usb_block_in_ini, detect_existing_installation,
    build_sc_create_args, build_sc_failure_args,
)

SERVICE_NAME = "LogPlatformAgent"


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


def install_service(exe_path: str) -> tuple:
    """Ritorna (successo: bool, messaggio: str)."""
    if check_service_exists():
        run_sc("stop", SERVICE_NAME)
        time.sleep(3)
        run_sc("delete", SERVICE_NAME)
        time.sleep(2)

    result = run_sc(*build_sc_create_args(SERVICE_NAME, exe_path))
    if result.returncode != 0:
        return False, f"Creazione servizio fallita:\n{result.stderr or result.stdout}"

    run_sc(*build_sc_failure_args(SERVICE_NAME))

    result = run_sc("start", SERVICE_NAME)
    if result.returncode != 0:
        return False, f"Il servizio e' stato creato ma non e' partito:\n{result.stderr or result.stdout}"

    return True, "Servizio installato e avviato correttamente."


class InstallerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Installazione Log Platform Agent")
        root.geometry("440x300")
        root.resizable(False, False)

        self.script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.exe_path = os.path.join(self.script_dir, "logplatform-agent.exe")
        self.ini_path = os.path.join(self.script_dir, "agent.ini")

        tk.Label(root, text="Log Platform Agent", font=("Segoe UI", 14, "bold")).pack(pady=(15, 5))

        existing = check_service_exists()
        status_text = (
            "Rilevata un'installazione precedente: verra' sostituita."
            if existing else
            "Nessuna installazione precedente rilevata."
        )
        tk.Label(root, text=status_text, wraplength=400).pack(pady=5)

        if not os.path.exists(self.exe_path):
            tk.Label(root, text=f"ATTENZIONE: {os.path.basename(self.exe_path)} non trovato in questa cartella.",
                     fg="#dc2626", wraplength=400).pack(pady=5)

        self.usb_var = tk.BooleanVar(value=False)
        tk.Checkbutton(root, text="Blocca storage USB (chiavette, dischi esterni)",
                        variable=self.usb_var).pack(pady=10)
        tk.Label(root, text="(non blocca tastiera/mouse/stampanti - solo lo storage)",
                 font=("Segoe UI", 8), fg="#64748b").pack()

        if not is_admin():
            tk.Label(root, text="Esegui questo programma come Amministratore per procedere.",
                     fg="#dc2626", wraplength=400).pack(pady=10)

        self.install_btn = tk.Button(root, text="Installa", command=self.on_install,
                                       width=20, height=2)
        self.install_btn.pack(pady=15)

        self.status_label = tk.Label(root, text="", wraplength=400)
        self.status_label.pack(pady=5)

    def on_install(self):
        if not is_admin():
            messagebox.showerror("Permessi insufficienti",
                                   "Esegui questo programma come Amministratore.")
            return
        if not os.path.exists(self.exe_path):
            messagebox.showerror("File mancante",
                                   f"Non trovo {self.exe_path}.\nAssicurati di eseguire questo programma dalla cartella dell'agent.")
            return

        self.install_btn.config(state=tk.DISABLED)
        self.status_label.config(text="Installazione in corso...")
        self.root.update()

        if os.path.exists(self.ini_path):
            set_usb_block_in_ini(self.ini_path, self.usb_var.get())

        ok, msg = install_service(self.exe_path)

        if ok:
            messagebox.showinfo("Fatto", msg)
        else:
            messagebox.showerror("Errore", msg)
        self.status_label.config(text=msg)
        self.install_btn.config(state=tk.NORMAL)


if __name__ == "__main__":
    root = tk.Tk()
    app = InstallerApp(root)
    root.mainloop()
