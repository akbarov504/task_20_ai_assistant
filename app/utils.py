import asyncio
from typing import Dict, Set, List, Any

_active_clients: Dict[str, Set[Any]] = {}
_registry_lock = asyncio.Lock()

async def register_client(client: Any):
    async with _registry_lock:
        cdl = client.cfg.driver_cdl
        if cdl not in _active_clients:
            _active_clients[cdl] = set()
        _active_clients[cdl].add(client)

async def unregister_client(client: Any):
    async with _registry_lock:
        cdl = client.cfg.driver_cdl
        if cdl in _active_clients:
            _active_clients[cdl].discard(client)
            if not _active_clients[cdl]:
                del _active_clients[cdl]

def get_active_clients(cdl: str = None) -> List[Any]:
    if cdl is not None:
        return list(_active_clients.get(cdl.strip(), set()))
    all_clients = []
    for clients in _active_clients.values():
        all_clients.extend(clients)
    return all_clients

async def close_call_by_cdl(cdl: str, reason: str = "CANCELLED_BY_OPERATOR") -> bool:
    if not cdl or not isinstance(cdl, str):
        return False

    cdl_clean = cdl.strip()
    clients = get_active_clients(cdl_clean)

    if not clients:
        print(f"[UTILS] No active call found for CDL: {cdl_clean}")
        return False

    print(f"[UTILS] Closing {len(clients)} call(s) for CDL: {cdl_clean} immediately...")
    closed_any = False
    for client in clients:
        try:
            await client.end(reason=reason)
            await client.close()
            closed_any = True
        except Exception as exc:
            print(f"[UTILS] Error closing client for CDL {cdl_clean}: {exc}")

    return closed_any

close_client_by_cdl = close_call_by_cdl
