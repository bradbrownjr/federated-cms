"""One-shot, loopback-only identification probe; not a B2F server.

Run with python -m tests.sid_probe --sid '[FCMS-0.1-B2FHM$]'.
Never proxy this diagnostic to radio or a public network.
"""
import argparse
import re
import socket


def probe(listener: socket.socket, sid: str, timeout: float = 30) -> str:
    """Send one SID and report only whether the peer sent any bytes."""
    if not re.fullmatch(r"\[[A-Za-z0-9.$-]{1,75}\]", sid):
        raise ValueError("SID must be a short bracketed ASCII identifier")
    listener.settimeout(timeout)
    with listener.accept()[0] as peer:
        peer.settimeout(timeout)
        peer.sendall((sid + "\rFCMS SID PROBE >\r").encode("ascii"))
        try:
            return "client sent data" if peer.recv(1) else "client disconnected"
        except socket.timeout:
            return "client timed out"
        except ConnectionResetError:
            return "client disconnected"


def main() -> None:
    parser = argparse.ArgumentParser(description="One-connection loopback SID diagnostic; no authentication or mailbox")
    parser.add_argument("--sid", required=True)
    parser.add_argument("--port", type=int, default=63192)
    args = parser.parse_args()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", args.port))
        listener.listen(1)
        print(f"SID probe listening on 127.0.0.1:{listener.getsockname()[1]}", flush=True)
        try:
            print(probe(listener, args.sid))
        except (TimeoutError, ValueError) as exc:
            parser.exit(2, f"probe failed: {exc}\n")


if __name__ == "__main__":
    main()
