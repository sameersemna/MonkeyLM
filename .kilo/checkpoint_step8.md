# Dependency Security Audit — Step 8 (updated 2026-07-15)

## Summary

| Package | Pinned | Latest | CVEs in pinned version | Action |
|---------|--------|--------|----------------------|--------|
| **Pillow** | 11.3.0 | >=12.3.0 | **CVE-2026-55798** (HIGH - OS Command Injection), **CVE-2026-59200** (HIGH - Decompression Bomb DoS) | URGENT UPDATE to 12.3.0 |
| **python-dotenv** | 1.0.1 | >=1.2.2 | CVE-2026-28684 (path traversal in version <1.2.2) | UPDATE to 1.2.2+ |
| Faker | 37.8.0 | 40.28.1 | None known | Outdated, update recommended |
| ollama | 0.6.2 | ~0.30.x | Server-side CVEs (24 known) but Python client package itself has no exposed CVE at this pin. Risk is in the remote Ollama server binary, not this dependency. | Monitor |
| pixelmatch | 0.3.0 | 0.3.0 | None known | Up to date |
| playwright | 1.60.0 | ~1.60.x | None exposed at pinned version | OK |
| asyncpg | 0.30.0 | ~0.30.x | None known | OK |
| redis | 6.2.0 | ~6.4.x | None known at this pin | OK |
| httpx | 0.28.1 | ~0.28.x | Transitive: idna <3.15 (CVE-2026-45409 ReDoS), urllib3 <2.7.0 (CVE-2026-44431/44432) | Add floor pins for transitive deps |
| reportlab | 4.4.0 | ~4.4.x | None known at this pin | OK |

## Critical Findings

### CVE-2026-55798 — Pillow OS Command Injection [HIGH]
- **Package:** Pillow < 12.3.0
- **Detail:** `WindowsViewer.get_command()` constructs a cmd.exe shell command by directly embedding a file path into an f-string without escaping, calls subprocess.Popen(..., shell=True). Shell metacharacters in the file path can inject arbitrary commands.
- **Fix:** Update to Pillow >= 12.3.0
- **CVE:** CVE-2026-55798 (PYSEC-2026-2257)

### CVE-2026-59200 — Pillow Decompression Bomb DoS [HIGH]
- **Package:** Pillow 5.1.0 - 12.2.0
- **Detail:** `PdfParser.PdfStream.decode()` calls `zlib.decompress()` with bufsize set to the PDF stream Length field without upper bound on decompressed output size. A ~950 KB crafted PDF decompresses to 1 GB of memory, causing OOM termination.
- **Fix:** Update to Pillow >= 12.3.0
- **CVE:** CVE-2026-59200 (GHSA-jjj6-mw9f-p565)

### CVE-2026-28684 — python-dotenv [MEDIUM]
- **Package:** python-dotenv < 1.2.2
- **Detail:** Path vulnerability in dotenv parsing logic exploitable when reading untrusted environment files.
- **Fix:** Update to python-dotenv >= 1.2.2

### Transitive Dependencies — httpx supply chain [LOW-MEDIUM]
- idna < 3.15 has CVE-2026-45409 (ReDoS on encode()). Pulled in transitively by httpx/anyio. Floor pin `idna>=3.15` recommended.
- urllib3 < 2.7.0 has CVE-2026-44431 and CVE-2026-44432. Floor pin `urllib3>=2.7.0` for any downstream that pulls it in.

## ollama Server-Side Risk Note
The `ollama` Python package (v0.6.2) is an HTTP client wrapper.  The 24 CVEs catalogued against "ollama" target the Go server binary (e.g., heap OOB read, path traversal, null pointer dereference).  These affect the remote Ollama service itself, not this Python dependency.  Mitigation: ensure the deployed Ollama server is patched to latest version; the Python client pin can remain or be updated for API compatibility.

## Updates Applied
- Pillow: 11.3.0 → 12.3.0 (fixes CVE-2026-55798, CVE-2026-59200)
- python-dotenv: 1.0.1 → 1.2.2 (fixes CVE-2026-28684)
- Faker: 37.8.0 → 40.28.1 (latest, no known CVEs but major version behind)
- Added floor pins for critical transitive deps: `idna>=3.15`, `urllib3>=2.7.0`
