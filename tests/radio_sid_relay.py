"""One-shot loopback SID adapter for the operator-controlled radio lab."""
import argparse
import asyncio

SID = b'[FCMS-0.1-B2FWHM$]\r'
ORIGINAL = b'[FCMS-0.1-B2FHM$]\r'


async def handle(reader, writer, target_port):
    upstream = None
    try:
        source, upstream = await asyncio.open_connection('127.0.0.1', target_port, limit=512)
        if await asyncio.wait_for(source.readuntil(b'\r'), 5) != ORIGINAL:
            raise ValueError('unexpected lab SID')
        writer.write(SID)
        await writer.drain()

        async def copy(src, dest):
            while data := await src.read(4096):
                dest.write(data)
                await dest.drain()

        tasks = [asyncio.create_task(copy(reader, upstream)), asyncio.create_task(copy(source, writer))]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        writer.close()
        if upstream:
            upstream.close()


async def run(port, target_port, seconds):
    # Fail before arming the radio if the loopback mailbox is already down.
    # The probe reads only the public SID and never attempts authentication.
    reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', target_port, limit=512), 5)
    try:
        if await asyncio.wait_for(reader.readuntil(b'\r'), 5) != ORIGINAL:
            raise ValueError('unexpected lab SID')
    finally:
        writer.close()
        await writer.wait_closed()
    done = asyncio.Event()
    accepted = False
    failure = None

    async def accept(reader, writer):
        nonlocal accepted, failure
        if accepted:
            writer.close()
            return
        accepted = True
        try:
            await handle(reader, writer, target_port)
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError) as exc:
            failure = type(exc).__name__
        finally:
            done.set()

    server = await asyncio.start_server(accept, '127.0.0.1', port)
    try:
        print(f'Loopback SID relay ready on port {port} for one session.', flush=True)
        await asyncio.wait_for(done.wait(), seconds)
        if failure:
            raise ConnectionError(failure)
    finally:
        server.close()
        await server.wait_closed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=63195)
    parser.add_argument('--b2f-port', type=int, required=True)
    parser.add_argument('--seconds', type=int, default=600, choices=range(1, 3601), metavar='1..3600')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.port, args.b2f_port, args.seconds))
    except (OSError, TimeoutError, ValueError) as exc:
        parser.exit(1, f'SID relay stopped: {type(exc).__name__}\n')
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
