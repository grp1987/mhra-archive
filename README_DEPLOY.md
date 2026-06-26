# MHRA Archive — VPS deploy (internet, passcode-gated)

A trimmed, **serve-only** build of the MHRA Archive for the Windows VPS. Carries
the slim catalog DB (catalog + MAH names + first-authorised dates + change log,
~70 MB). PDFs stream live from MHRA's blob store on click. No PDF mirror, no
enrichment scripts — those stay on the Mac, which periodically pushes an updated
`mhra.db`.

## Security posture
- **Passcode gate** (no username) — set via `secret_pw.txt`, gates every request.
  The cookie stores only a salted hash, never the passcode.
- **Bare IP + open port (HTTP)** — simple, but the passcode travels **unencrypted**.
  The data is public (it's the MHRA site), so the passcode only keeps strangers
  out; **don't reuse an important password**. Ask the Mac side to add a self-signed
  cert if you want it encrypted.
- Runs at **BelowNormal priority** with bounded threads so it can't starve ORO.

## One-time setup on the VPS (elevated PowerShell)
```powershell
cd C:\nyo-mhra            # wherever you cloned this repo
"my-passcode" | Set-Content secret_pw.txt   # choose a passcode (kept out of git)
.\run_vps.ps1
```
That installs deps, opens firewall TCP 8090, and registers a low-priority
scheduled task that serves at boot. Then browse to:

    http://<VPS-public-IP>:8090     (enter the passcode)

## Keeping it current
The Mac rebuilds the slim DB and pushes it. On the VPS:
```powershell
cd C:\nyo-mhra ; git pull ; Restart-ScheduledTask -TaskName MHRA-Archive
```

## Stop / remove
```powershell
Stop-ScheduledTask -TaskName MHRA-Archive
Unregister-ScheduledTask -TaskName MHRA-Archive -Confirm:$false
Remove-NetFirewallRule -DisplayName "MHRA Archive 8090"
```

## Files
| File | Role |
|------|------|
| `app.py` | Flask app + passcode gate |
| `db.py` | SQLite search (slim DB has no `body_fts`, so "search inside text" auto-falls-back to metadata) |
| `download.py` / `report.py` | PDF proxy + change report (read-only here) |
| `serve_vps.py` | waitress production entrypoint |
| `run_vps.ps1` | one-shot setup: deps + firewall + scheduled task |
| `mhra.db` | slim catalog DB |
