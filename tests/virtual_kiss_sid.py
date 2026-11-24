"""One-shot virtual TCP KISS network for SID classification experiments.

The only transports are TCP sockets created here; no modem is contacted.
"""
import argparse
import asyncio
import re
import socket
from types import SimpleNamespace

from federated_cms.kiss_lab import run_lab
from tests.sid_probe import probe


async def run(host, port, sid, station, peer, seconds=300, b2f_port=None):
    if not re.fullmatch(r'\[[A-Za-z0-9.$-]{1,75}\]', sid):
        raise ValueError('invalid SID')
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

    probe_socket = None
    proxy = None
    if b2f_port is None:
        probe_socket = socket.socket()
        probe_socket.bind(('127.0.0.1', 0))
        probe_socket.listen(1)
        target_port = probe_socket.getsockname()[1]
    else:
        async def relay(reader, writer):
            try:
                while data := await reader.read(4096):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()

        async def proxy_session(reader, writer):
            upstream = None
            try:
                source, upstream = await asyncio.open_connection('127.0.0.1', b2f_port, limit=512)
                original = await asyncio.wait_for(source.readuntil(b'\r'), 5)
                if original != b'[FCMS-0.1-B2FHM$]\r':
                    raise ValueError('unexpected lab SID')
                writer.write(sid.encode('ascii') + b'\r')
                await writer.drain()
                await asyncio.gather(relay(reader, upstream), relay(source, writer))
            finally:
                writer.close()
                if upstream:
                    upstream.close()

        proxy = await asyncio.start_server(proxy_session, '127.0.0.1', 0)
        target_port = proxy.sockets[0].getsockname()[1]
    internal = await asyncio.start_server(lambda r, w: join('internal', r, w), '127.0.0.1', 0)
    external = None
    tasks = []
    try:
        external = await asyncio.start_server(lambda r, w: join('client', r, w), host, port)
        args = SimpleNamespace(kiss_host='127.0.0.1', kiss_port=internal.sockets[0].getsockname()[1],
                               channel=0, station=station, peer=peer,
                               b2f_port=target_port, max_frames=250,
                               seconds=seconds, enable_tx=True)
        tasks = [asyncio.create_task(run_lab(args))]
        if probe_socket:
            tasks.insert(0, asyncio.create_task(asyncio.to_thread(probe, probe_socket, sid, seconds)))
        print(f"Virtual KISS ready at {host}:{external.sockets[0].getsockname()[1]} (one session)", flush=True)
        done, _ = await asyncio.wait(tasks, timeout=seconds + 2, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            raise TimeoutError('no completed session')
        for task in done:
            task.result()
        if probe_socket and tasks[0] in done and not tasks[1].done():
            # Give the station time to deliver AX.25 DISC before stopping the
            # virtual KISS link.  Do not leave the diagnostic running forever.
            await asyncio.wait_for(tasks[1], 10)
        print('Virtual KISS session finished.', flush=True)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if probe_socket:
            probe_socket.close()
        for writer in peers.values():
            writer.close()
        internal.close()
        if external:
            external.close()
        if proxy:
            proxy.close()
        await internal.wait_closed()
        if external:
            await external.wait_closed()
        if proxy:
            await proxy.wait_closed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1', help='explicit local bind address')
    parser.add_argument('--port', type=int, default=63193)
    parser.add_argument('--sid', default='[FCMS-0.1-B2FHM$]')
    parser.add_argument('--station', default='KC1JMH-10')
    parser.add_argument('--peer', default='KC1JMH')
    parser.add_argument('--b2f-port', type=int, help='loopback-only lab mailbox; omit for SID probe')
    parser.add_argument('--seconds', type=int, default=300, choices=range(1, 601), metavar='1..600')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.host, args.port, args.sid, args.station, args.peer, args.seconds, args.b2f_port))
    except (OSError, TimeoutError, ValueError) as exc:
        parser.exit(1, f'Virtual KISS stopped: {exc}\n')
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
