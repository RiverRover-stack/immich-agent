"""Remove the photos our probes uploaded (named ss-probe-*), and nothing else."""

from __future__ import annotations

import httpx

from shutterstorm.config import Settings
from shutterstorm.workload.probes import UPLOAD_PREFIX

PAGE = 200


def find_probe_assets(client: httpx.Client) -> list[str]:
    """Ids of assets whose original filename starts with our prefix. Never matches seed photos."""
    ids: list[str] = []
    page = 1
    while True:
        r = client.post(
            "/api/search/metadata",
            json={"originalFileName": UPLOAD_PREFIX, "size": PAGE, "page": page},
        )
        r.raise_for_status()
        assets = r.json()["assets"]
        for item in assets["items"]:
            # The server filter is a substring match; re-check the prefix ourselves.
            if str(item.get("originalFileName", "")).startswith(UPLOAD_PREFIX):
                ids.append(item["id"])
        nxt = assets.get("nextPage")
        if not nxt:
            return ids
        page = int(nxt)


def cleanup(settings: Settings, dry_run: bool = False) -> dict[str, int]:
    with httpx.Client(
        base_url=settings.immich_url, headers={"x-api-key": settings.api_key}, timeout=30
    ) as client:
        ids = find_probe_assets(client)
        deleted = 0
        if ids and not dry_run:
            for i in range(0, len(ids), 100):
                chunk = ids[i : i + 100]
                r = client.request("DELETE", "/api/assets", json={"ids": chunk, "force": True})
                r.raise_for_status()
                deleted += len(chunk)
        return {"found": len(ids), "deleted": deleted}
