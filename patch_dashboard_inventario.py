#!/usr/bin/env python3
"""
Applica automaticamente le 4 modifiche per la scheda Inventario a
static/dashboard.html. Uso:
    python3 patch_dashboard_inventario.py static/dashboard.html

Cerca punti di aggancio precisi nel file esistente e inserisce il nuovo
codice subito dopo. Se un punto di aggancio non viene trovato, lo segnala
chiaramente invece di applicare una modifica a metà.
"""
import sys

NAV_ANCHOR = '<button data-panel="notifiche" onclick="showPanel(\'notifiche\')">Notifiche</button>'
NAV_INSERT = '\n  <button data-panel="inventario" onclick="showPanel(\'inventario\')">Inventario</button>'

MAIN_CLOSE_ANCHOR = '</main>'
SECTION_INSERT = '''
  <section class="panel" id="panel-inventario">
    <div class="row">
      <div id="inventario-tenant-wrap">
        <label>Cliente</label>
        <select id="inventario-tenant"></select>
      </div>
      <button class="primary" onclick="loadInventory()">Aggiorna</button>
    </div>
    <table id="inventory-table">
      <thead><tr>
        <th>Host</th><th>Sistema operativo</th><th>CPU</th><th>RAM</th>
        <th>Disco (usato/totale)</th><th>Software</th><th>Ultimo aggiornamento</th>
      </tr></thead>
      <tbody></tbody>
    </table>
    <div class="empty" id="inventory-empty" style="display:none">Nessun inventario ricevuto ancora per questo cliente (arriva entro 24 ore dal primo avvio dell'agent).</div>

    <div id="inventory-software-detail" style="display:none; margin-top:1.5rem">
      <h3 id="inventory-software-title" style="font-size:.95rem"></h3>
      <table id="inventory-software-table">
        <thead><tr><th>Pacchetto</th><th>Versione</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

'''

SCRIPT_ANCHOR = '// --- Avvio ---'
SCRIPT_INSERT = '''// --- Inventario ---
async function loadInventory() {
  const tenant = document.getElementById('inventario-tenant').value;
  if (!tenant) return;
  document.getElementById('inventory-software-detail').style.display = 'none';
  try {
    const hosts = await api('/_authgate/dashboard/inventory?tenant=' + encodeURIComponent(tenant));
    const tbody = document.querySelector('#inventory-table tbody');
    tbody.innerHTML = '';
    document.getElementById('inventory-empty').style.display = hosts.length ? 'none' : 'block';
    hosts.forEach(h => {
      const os = (h.os || {}).name || '?';
      const cpu = h.cpu ? `${h.cpu.model} (${h.cpu.cores} core)` : '?';
      const ram = h.memory ? `${(h.memory.total_mb / 1024).toFixed(1)} GB` : '?';
      const disk = (h.disks && h.disks[0]) ? `${h.disks[0].used_gb} / ${h.disks[0].total_gb} GB` : '-';
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${h.hostname}</td><td>${os}</td><td>${cpu}</td><td>${ram}</td><td>${disk}</td>
        <td><a href="#" onclick="showSoftware('${tenant}','${h.hostname}');return false;">${h.software_count || 0} pacchetti</a></td>
        <td>${h.timestamp || ''}</td>`;
      tbody.appendChild(tr);
    });
    showError('');
  } catch (e) { showError(e.message); }
}

async function showSoftware(tenant, hostname) {
  try {
    const software = await api('/_authgate/dashboard/inventory/software?tenant=' + encodeURIComponent(tenant) + '&hostname=' + encodeURIComponent(hostname));
    document.getElementById('inventory-software-title').textContent = `Software installato su ${hostname}`;
    const tbody = document.querySelector('#inventory-software-table tbody');
    tbody.innerHTML = '';
    software.forEach(pkg => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${pkg.name}</td><td>${pkg.version || ''}</td>`;
      tbody.appendChild(tr);
    });
    document.getElementById('inventory-software-detail').style.display = 'block';
  } catch (e) { showError(e.message); }
}

'''

SHOWPANEL_ANCHOR = "if (name === 'notifiche') { loadTenantOptions('notifica-tenant'); loadNotifications(); }"
SHOWPANEL_INSERT = "\n  if (name === 'inventario') { loadTenantOptions('inventario-tenant').then(loadInventory); }"


def patch(path: str):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    checks = [
        (NAV_ANCHOR, "bottone 'Notifiche' nel menu <nav>"),
        (MAIN_CLOSE_ANCHOR, "tag di chiusura </main>"),
        (SCRIPT_ANCHOR, "commento '// --- Avvio ---'"),
        (SHOWPANEL_ANCHOR, "riga 'if (name === ...notifiche...)' dentro showPanel()"),
    ]
    missing = [desc for anchor, desc in checks if anchor not in content]
    if missing:
        print("ERRORE: non ho trovato questi punti di aggancio nel file:")
        for m in missing:
            print(f"  - {m}")
        print("Nessuna modifica applicata (per sicurezza, tutto o niente). "
              "Il file potrebbe essere diverso da quello atteso: incollamelo e adatto lo script.")
        sys.exit(1)

    if "id=\"panel-inventario\"" in content:
        print("Il file contiene già la sezione inventario: nessuna modifica applicata (evito duplicati).")
        sys.exit(0)

    content = content.replace(NAV_ANCHOR, NAV_ANCHOR + NAV_INSERT, 1)
    content = content.replace(MAIN_CLOSE_ANCHOR, SECTION_INSERT + MAIN_CLOSE_ANCHOR, 1)
    content = content.replace(SCRIPT_ANCHOR, SCRIPT_INSERT + SCRIPT_ANCHOR, 1)
    content = content.replace(SHOWPANEL_ANCHOR, SHOWPANEL_ANCHOR + SHOWPANEL_INSERT, 1)

    backup_path = path + ".bak"
    import shutil
    shutil.copy(path, backup_path)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Fatto. Backup del file originale salvato in: {backup_path}")
    print("Le 4 modifiche sono state applicate con successo.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Uso: python3 {sys.argv[0]} percorso/dashboard.html")
        sys.exit(1)
    patch(sys.argv[1])
