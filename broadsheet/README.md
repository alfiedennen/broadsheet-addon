# broadsheet add-on

This is the manifest directory for the **broadsheet** Home Assistant
add-on. The repository-level README is one directory up.

For installation + usage docs see [DOCS.md](./DOCS.md), which is
what HA renders inside the add-on store.

## Files in this directory

| File | Purpose |
|---|---|
| `config.yaml` | HA add-on manifest (ingress, perms, options schema) |
| `Dockerfile` | Multi-arch container build |
| `run.sh` | Container entrypoint (writes runtime-env.js, renders nginx config, starts sidecar + nginx) |
| `nginx.conf.tpl` | nginx config template (serves SPA, proxies API + WS to supervisor with bearer injection) |
| `sidecar.py` | Tiny aiohttp service for `/api/broadsheet/curation` reads/writes |
| `DOCS.md` | Shown in HA's add-on store |
| `translations/en.yaml` | UI labels for HA's options panel |
| `www/` | Built SPA bundle, populated by CI (gitignored) |

## How it gets built

GitHub Actions workflow at `.github/workflows/builder.yaml`:

1. On tag push (`v*.*.*`): check out this repo
2. Check out the sibling `broadsheet` repo (the SPA source)
3. `pnpm build` the SPA
4. Copy the build output into this directory's `www/`
5. Run HA's official add-on builder action against the matrix
   `[aarch64, amd64]`
6. Push images to `ghcr.io/alfiedennen/broadsheet-{arch}`
