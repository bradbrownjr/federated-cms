# Provenance

Code in this repository that was copied or ported from another bradbrownjr repository.
When the source changes, check it for fixes worth bringing over; when a file here improves,
offer the change back to the source. Paths are relative to each repository's root;
Source repo is the repository name, Source commit the commit the copy was taken from.

| Local path | Source repo | Source path | Source commit | Copied | Local changes |
|---|---|---|---|---|---|
| `src/federated_cms/lzhuf.py` | kissterm | `kissterm/winlink/lzhuf.py` | 2c16a54 | 2026-11-23 | none |
| `src/federated_cms/b2f_auth.py` | kissterm | `kissterm/winlink/secure.py` | 2c16a54 | 2026-11-23 | docstring rewritten; `usedforsecurity=False` added to MD5; challenge encoded as ASCII instead of latin-1; `from __future__` import removed |

kissterm's copies include third-party code (JNOS 2 `lzhuf.c`, wl2k-go `fbb/secure.go`); see the headers of those files.
