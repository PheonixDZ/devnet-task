import os
import time
import json
import asyncio
import signal
import itertools
from typing import Optional

import aiohttp
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    start_http_server,
)

# ----------------- Config -----------------

RPC_URL = os.getenv("RPC_URL", "http://geth-dev:8545")
FROM_ADDR = os.getenv("FROM_ADDR", "0x71562b71999873DB5b286dF957af199Ec94617F7")
TO_ADDR = os.getenv("TO_ADDR", "0x62358b29b9e3e70ff51D88766e41a339D3e8FFff")

TARGET_TPS = float(os.getenv("TARGET_TPS", "20"))
CONCURRENCY = int(os.getenv("CONCURRENCY", "10"))
PROM_PORT = int(os.getenv("PROM_PORT", "8000"))

TX_VALUE_WEI = int(os.getenv("TX_VALUE_WEI", "0"))
GAS_LIMIT = int(os.getenv("GAS_LIMIT", "21000"))
GAS_PRICE_WEI = int(os.getenv("GAS_PRICE_WEI", "1"))

# ----------------- Metrics -----------------

TX_TOTAL = Counter("loadgen_tx_total", "Total tx sent", ["status"])
RPC_TOTAL = Counter("loadgen_rpc_requests_total", "Total JSON-RPC calls")
RPC_ERRORS = Counter("loadgen_rpc_errors_total", "RPC errors by method", ["method"])

LATENCY = Histogram("loadgen_rpc_latency_seconds", "JSON-RPC latency (s)")
TX_SEND_LATENCY = Histogram(
    "loadgen_tx_send_latency_seconds",
    "Latency of eth_sendTransaction",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]
)
RECEIPT_LATENCY = Histogram(
    "loadgen_receipt_wait_seconds",
    "Latency waiting for receipt",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]
)

MGAS_TOTAL = Counter("loadgen_mgas_total", "Total MGas used")
IN_FLIGHT = Gauge("loadgen_in_flight", "In-flight RPC requests")

# ----------------- JSON-RPC Client -----------------

class JsonRpcClient:
    def __init__(self, url: str):
        self.url = url
        self._id_iter = itertools.count(1)

    async def call(self, session: aiohttp.ClientSession, method: str, params):
        req_id = next(self._id_iter)
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        t0 = time.perf_counter()
        IN_FLIGHT.inc()

        try:
            async with session.post(self.url, json=payload) as resp:
                RPC_TOTAL.inc()
                resp.raise_for_status()
                data = await resp.json()
        except Exception:
            RPC_ERRORS.labels(method=method).inc()
            raise
        finally:
            IN_FLIGHT.dec()
            LATENCY.observe(time.perf_counter() - t0)

        if "error" in data:
            RPC_ERRORS.labels(method=method).inc()
            raise RuntimeError(f"RPC error: {data['error']}")

        return data["result"]

# ----------------- Load generator -----------------

async def send_tx_and_measure(client: JsonRpcClient, session: aiohttp.ClientSession):
    tx = {
        "from": FROM_ADDR,
        "to": TO_ADDR,
        "value": hex(TX_VALUE_WEI),
        "gas": hex(GAS_LIMIT),
        "gasPrice": hex(GAS_PRICE_WEI),
    }

    # Measure TX send latency
    send_t0 = time.perf_counter()
    try:
        tx_hash = await client.call(session, "eth_sendTransaction", [tx])
        TX_SEND_LATENCY.observe(time.perf_counter() - send_t0)
        TX_TOTAL.labels(status="success").inc()
    except Exception:
        TX_TOTAL.labels(status="failed").inc()
        return

    # Measure receipt wait latency
    receipt_t0 = time.perf_counter()
    try:
        gas_used = await wait_for_gas_used(client, session, tx_hash)
        RECEIPT_LATENCY.observe(time.perf_counter() - receipt_t0)
        if gas_used is not None:
            MGAS_TOTAL.inc(gas_used / 1_000_000)
    except Exception:
        pass

async def wait_for_gas_used(client, session, tx_hash, timeout=10.0, poll_interval=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            receipt = await client.call(session, "eth_getTransactionReceipt", [tx_hash])
        except Exception:
            await asyncio.sleep(poll_interval)
            continue

        if receipt:
            gas_used_hex = receipt.get("gasUsed")
            if gas_used_hex:
                return int(gas_used_hex, 16)
            return None
        await asyncio.sleep(poll_interval)
    return None

async def scheduler_loop(client: JsonRpcClient):
    queue = asyncio.Queue()

    async def worker(worker_id: int):
        async with aiohttp.ClientSession() as session:
            while True:
                await queue.get()
                try:
                    await send_tx_and_measure(client, session)
                finally:
                    queue.task_done()

    workers = [asyncio.create_task(worker(i)) for i in range(CONCURRENCY)]

    interval = 1.0 / TARGET_TPS
    try:
        while True:
            await queue.put(None)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass
    finally:
        for w in workers:
            w.cancel()

# ----------------- Entrypoint -----------------

stop_event = asyncio.Event()

def _handle_sigterm():
    stop_event.set()

async def main():
    print(f"[loadgen] RPC={RPC_URL} TPS={TARGET_TPS} CONC={CONCURRENCY}")
    client = JsonRpcClient(RPC_URL)

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
    loop.add_signal_handler(signal.SIGINT, _handle_sigterm)

    load_task = asyncio.create_task(scheduler_loop(client))
    await stop_event.wait()
    load_task.cancel()
    await asyncio.gather(load_task, return_exceptions=True)

if __name__ == "__main__":
    start_http_server(PROM_PORT)
    asyncio.run(main())
