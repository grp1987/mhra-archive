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
# Private R2 document archive

`r2.env` holds the private Cloudflare R2 credentials on the VPS and must never
be committed. Verify access with `python test_r2.py`.

Archive a small test batch first:

```powershell
python download.py --storage r2 --types Spc,Pil,Par --limit 5 --workers 2
```

Then archive every known version (safe to stop and rerun):

```powershell
python download.py --storage r2 --types Spc,Pil,Par --workers 4
```

Objects are stored as `mhra/documents/<first-two>/<storage-hash>.pdf`. A changed
MHRA document has a different storage hash, so it creates a new object rather
than overwriting the previous version. The persistent, gitignored
`r2_archive.db` records completed uploads separately from the deployable
`mhra.db` catalogue. Back up both databases: together they contain the catalogue,
links between versions, and the private R2 object register.

## Nightly change monitoring

After the initial R2 archive completes, run `install_nightly.ps1` once from an
elevated PowerShell. It copies the catalogue and R2 register into the persistent,
gitignored `data/` directory, refreshes today's catalogue as a clean baseline,
restarts the website against that live data, and installs `THIL-MHRA-Nightly` at
02:00 daily.

Each nightly run records new, changed and removed logical documents in the
append-only `changes` table, then archives new hashes to R2. The website Change
Log supports from/to dates and links to current and previous archived PDFs.
Output is appended to `nightly.log`.

## Recovering the original Mac mirror

`upload_existing.py` checks R2 for every locally mirrored hash and uploads only
objects that are missing. It creates `r2_recovery.db`, an independent register
for later merging into the VPS live `r2_archive.db`. It does not modify the
original Mac archive or its database. Keep the R2 environment file outside Git.
After securely copying `r2_recovery.db` to the VPS, merge it with
`merge_recovery.py --live data/r2_archive.db --recovery r2_recovery.db`.
