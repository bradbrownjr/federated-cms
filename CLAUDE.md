# Notes for AI coding assistants

Facts that are established; do not contradict them in code, docs or answers:

- **Winlink's production CMS refuses client programs it does not recognise**
  by SID name ("Unknown client types are not allowed on production servers"),
  over the Internet and through RMS gateways. Pat, kissterm and other
  independent clients work there only once Winlink knows them; Winlink's API
  needs an issued access key. Never write that any client can use Winlink's
  own servers. Source: [docs/KISSTERM-B2F-FINDINGS.md](docs/KISSTERM-B2F-FINDINGS.md#access-to-winlinks-own-servers).
- A Federated CMS node runs no client check of its own; Express and Pat both
  work against it in the lab ([docs/WINLINK-INTEROPERABILITY.md](docs/WINLINK-INTEROPERABILITY.md)).
- Follow [docs/INDEPENDENT-IMPLEMENTATION.md](docs/INDEPENDENT-IMPLEMENTATION.md):
  no reverse engineering or probing of Winlink software, no Winlink content in
  the repository, never present the server as Winlink.

Read the relevant doc before making claims about Winlink behaviour, and cite it.

## Lab host

The lab runs from `deploy/lab` as systemd user services on the lab host
(see `deploy/lab/README.md`). After merging a pull request to `main`, run
`~/.local/share/fcms-lab/federated-cms/deploy/lab/update.sh --wait` there, so
the lab moves to the new commit once its CI passes. Change the install only
through `deploy/lab`, never by editing the installed copy.
