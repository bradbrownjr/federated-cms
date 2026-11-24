"""One-shot virtual TCP KISS network for SID classification experiments.

The only transports are TCP sockets created here; no modem is contacted.
"""
import argparse
import asyncio
import socket
from types import SimpleNamespace

from federated_cms.kiss_lab import run_lab
from tests.sid_probe import probe


async def run(host, port, sid, station, peer, seconds=300):
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

    probe_socket = socket.socket()
    probe_socket.bind(('127.0.0.1', 0))
    probe_socket.listen(1)
    internal = await asyncio.start_server(lambda r, w: join('internal', r, w), '127.0.0.1', 0)
    external = None
    tasks = []
    try:
        external = await asyncio.start_server(lambda r, w: join('client', r, w), host, port)
        args = SimpleNamespace(kiss_host='127.0.0.1', kiss_port=internal.sockets[0].getsockname()[1],
                               channel=0, station=station, peer=peer,
                               b2f_port=probe_socket.getsockname()[1], max_frames=250,
                               seconds=seconds, enable_tx=True)
        tasks = [asyncio.create_task(asyncio.to_thread(probe, probe_socket, sid, seconds)),
                 asyncio.create_task(run_lab(args))]
        print(f"Virtual KISS ready at {host}:{external.sockets[0].getsockname()[1]} (one session)", flush=True)
        done, _ = await asyncio.wait(tasks, timeout=seconds + 2, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            raise TimeoutError('no completed session')
        for task in done:
            task.result()
        if tasks[0] in done and not tasks[1].done():
            # Give the station time to deliver AX.25 DISC before stopping the
            # virtual KISS link.  Do not leave the diagnostic running forever.
            await asyncio.wait_for(tasks[1], 10)
        print('SID probe finished; no mailbox or authentication was used.', flush=True)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        probe_socket.close()
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
    parser.add_argument('--sid', default='[FCMS-0.1-B2FHM$]')
    parser.add_argument('--station', default='KC1JMH-10')
    parser.add_argument('--peer', default='KC1JMH')
    parser.add_argument('--seconds', type=int, default=300, choices=range(1, 601), metavar='1..600')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.host, args.port, args.sid, args.station, args.peer, args.seconds))
    except (OSError, TimeoutError, ValueError) as exc:
        parser.exit(1, f'Virtual KISS stopped: {exc}\n')
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
