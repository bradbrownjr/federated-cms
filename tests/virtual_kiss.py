"""One-session virtual TCP KISS network in front of the lab bridge and B2F lab.

Lets a client be tested without a modem or radio. The only transports are TCP
sockets created here.
"""
import argparse
import asyncio
from types import SimpleNamespace

from federated_cms.kiss_lab import run_lab


async def run(host, port, station, peer, b2f_port, seconds=300):
    peers = {}
    ready = asyncio.Event()

    async def join(name, reader, writer):
        if name in peers:
            writer.close()
            await writer.wait_closed()
            return
        peers[name] = writer
        if len(peers) == 2:
            ready.set()
        try:
            await asyncio.wait_for(ready.wait(), seconds)
            other = peers['internal' if name == 'client' else 'client']
            while data := await reader.read(4096):
                other.write(data)
                await other.drain()
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    internal = await asyncio.start_server(lambda r, w: join('internal', r, w), '127.0.0.1', 0)
    external = None
    task = None
    try:
        external = await asyncio.start_server(lambda r, w: join('client', r, w), host, port)
        args = SimpleNamespace(kiss_host='127.0.0.1', kiss_port=internal.sockets[0].getsockname()[1],
                               channel=0, station=station, peer=peer,
                               b2f_port=b2f_port, max_frames=250,
                               seconds=seconds, enable_tx=True)
        task = asyncio.create_task(run_lab(args))
        print(f"Virtual KISS ready at {host}:{external.sockets[0].getsockname()[1]} (one session)", flush=True)
        await asyncio.wait_for(task, seconds + 2)
        print('Virtual KISS session finished.', flush=True)
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for writer in peers.values():
            writer.close()
        internal.close()
        if external:
            external.close()
        await internal.wait_closed()
        if external:
            await external.wait_closed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1', help='explicit local bind address')
    parser.add_argument('--port', type=int, default=63193)
    parser.add_argument('--station', required=True, help='lab AX.25 callsign and SSID')
    parser.add_argument('--peer', required=True, help='client AX.25 callsign and SSID')
    parser.add_argument('--b2f-port', type=int, required=True, help='loopback B2F lab port')
    parser.add_argument('--seconds', type=int, default=300, choices=range(1, 601), metavar='1..600')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.host, args.port, args.station, args.peer, args.b2f_port, args.seconds))
    except (OSError, TimeoutError, ValueError) as exc:
        parser.exit(1, f'Virtual KISS stopped: {exc}\n')
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
