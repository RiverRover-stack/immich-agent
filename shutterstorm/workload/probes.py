"""Journey probes: tiny fake users that exercise Immich and record what happened.

Each simple journey is one entry in JOURNEYS. The upload journey is stateful (it uploads a photo,
then tracks it until processed) so it has its own loop. Everything appends CSV rows that are
flushed immediately, so a crash loses nothing.
"""

from __future__ import annotations

import asyncio
import csv
import itertools
import secrets
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from shutterstorm.config import ROOT, Settings
from shutterstorm.workload.stats import ProbeResult, RollingStats

PROBE_TIMEOUT_S = 5.0
UPLOAD_TIMEOUT_S = 30.0
QUEUE_POLL_S = 2.0
UPLOAD_INTERVAL_S = 10.0
UPLOAD_TRACK_POLL_S = 1.0
UPLOAD_GIVE_UP_S = 180.0  # an upload not fully processed by now is recorded as stalled
UPLOAD_PREFIX = "ss-probe-"  # filename prefix so probe uploads are identifiable and removable
DEFAULT_SAMPLE_DIR = ROOT / "data" / "sample_images"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
WATCHED_QUEUES = (
    "thumbnailGeneration",
    "metadataExtraction",
    "smartSearch",
    "faceDetection",
    "facialRecognition",
    "ocr",
)
SEARCH_TERMS = ("a dog", "a person", "food", "a building", "a car", "sunset", "a tree")
PROBES_HEADER = ["timestamp", "journey", "ok", "status", "latency_ms"]
QUEUES_HEADER = ["timestamp", "queue", "active", "waiting", "failed"]
UPLOADS_HEADER = [
    "asset_id",
    "uploaded_at",
    "accepted_status",
    "upload_ms",
    "thumbnail_s",
    "indexed_s",
    "final",
]


class ProbeFailure(Exception):
    """A journey step answered, but not with success."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


@dataclass
class ProbeContext:
    client: httpx.AsyncClient
    asset_id: str
    _terms: Iterator[str] = field(default_factory=lambda: itertools.cycle(SEARCH_TERMS))

    def next_term(self) -> str:
        # Immich caches query embeddings, so a repeated query can be served while ML is down.
        # A unique suffix forces every probe through the ML service (verified 10 Oct 2026).
        return f"{next(self._terms)} {secrets.token_hex(3)}"


async def _check(response: httpx.Response) -> httpx.Response:
    if response.status_code >= 400:
        raise ProbeFailure(response.status_code)
    return response


async def ping(ctx: ProbeContext) -> int:
    return (await _check(await ctx.client.get("/api/server/ping"))).status_code


async def auth_read(ctx: ProbeContext) -> int:
    return (await _check(await ctx.client.get("/api/users/me"))).status_code


async def browse(ctx: ProbeContext) -> int:
    await _check(await ctx.client.get("/api/timeline/buckets", params={"size": "MONTH"}))
    r = await ctx.client.get(f"/api/assets/{ctx.asset_id}/thumbnail", params={"size": "thumbnail"})
    return (await _check(r)).status_code


async def search(ctx: ProbeContext) -> int:
    r = await ctx.client.post("/api/search/smart", json={"query": ctx.next_term(), "size": 5})
    return (await _check(r)).status_code


@dataclass(frozen=True)
class Journey:
    name: str
    run: Callable[[ProbeContext], Awaitable[int]]
    interval_s: float
    needs: tuple[str, ...]  # components this journey depends on (feeds the dependency map)


JOURNEYS: tuple[Journey, ...] = (
    Journey("ping", ping, 1.0, ("immich_server",)),
    Journey("auth_read", auth_read, 1.0, ("immich_server", "immich_postgres")),
    Journey("browse", browse, 1.0, ("immich_server", "immich_postgres")),
    Journey("search", search, 2.0, ("immich_server", "immich_machine_learning", "immich_postgres")),
)
# The upload journey is stateful and lives in ProbeRunner._upload_loop / _track_loop.
# "upload" = the request is accepted (HTTP 201). "upload_processing" = it is then thumbnailed and
# search-indexed, which runs as background jobs. They fail independently (observations O-2).
UPLOAD_NEEDS = ("immich_server", "immich_postgres")
UPLOAD_PROCESSING_NEEDS = (
    "immich_server",
    "immich_postgres",
    "immich_redis",
    "immich_machine_learning",
)


@dataclass
class TrackedUpload:
    asset_id: str
    uploaded_at: float
    accepted_status: int
    upload_ms: float
    thumbnail_s: float | None = None  # seconds until the thumbnail existed (server job path)
    indexed_s: float | None = None  # seconds until searchable by content (needs ML)
    final: str = ""  # "" while in flight, then processed | stalled | pending

    @property
    def processed(self) -> bool:
        return self.thumbnail_s is not None and self.indexed_s is not None


class CsvSink:
    """Append-only CSV that flushes every row."""

    def __init__(self, path: Path, header: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow(header)
        self._file.flush()

    def write(self, row: list) -> None:
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def default_out_dir() -> Path:
    return ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")


def mutate(data: bytes) -> bytes:
    """Same picture, new file hash: Immich de-duplicates by checksum, so each upload must differ.

    Trailing bytes after the JPEG end marker are ignored by decoders (verified on v3.3.1).
    """
    return data + b"\x00" + secrets.token_bytes(16)


class ProbeRunner:
    def __init__(
        self,
        settings: Settings,
        journeys: tuple[Journey, ...] = JOURNEYS,
        out_dir: Path | None = None,
        sample_dir: Path | None = DEFAULT_SAMPLE_DIR,
        upload_interval_s: float = UPLOAD_INTERVAL_S,
    ) -> None:
        self.settings = settings
        self.journeys = journeys
        self.out_dir = out_dir or default_out_dir()
        self.sample_dir = sample_dir  # None disables the upload journey
        self.upload_interval_s = upload_interval_s
        self.stats = RollingStats(window_s=20.0)  # >= 2x the slowest journey interval
        self.queues: dict[str, dict] | None = None  # latest snapshot; None = unreachable
        self.uploads: list[TrackedUpload] = []
        self.upload_enabled = False
        self.started_at: float | None = None
        self.done = False
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def stop(self) -> None:
        """Ask the runner to finish. Safe to call from any thread."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._stop.set)
        else:
            self._stop.set()

    # ------------------------------------------------------------ helpers
    @property
    def journey_names(self) -> list[str]:
        return [j.name for j in self.journeys] + (["upload"] if self.upload_enabled else [])

    def upload_summary(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        pending = [u for u in self.uploads if not u.final]
        return {
            "accepted": sum(1 for u in self.uploads if u.accepted_status in (200, 201)),
            "thumbnails": sum(1 for u in self.uploads if u.thumbnail_s is not None),
            "indexed": sum(1 for u in self.uploads if u.indexed_s is not None),
            "processed": sum(1 for u in self.uploads if u.processed),
            "stalled": sum(1 for u in self.uploads if u.final == "stalled"),
            "oldest_pending_s": max((now - u.uploaded_at for u in pending), default=0.0),
        }

    def _record(self, sink: CsvSink, result: ProbeResult) -> None:
        self.stats.add(result)
        sink.write(
            [
                f"{result.ts:.3f}",
                result.journey,
                int(result.ok),
                result.status,
                f"{result.latency_ms:.1f}",
            ]
        )

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), max(0.0, seconds))
        except TimeoutError:
            pass

    async def _pick_asset(self, client: httpx.AsyncClient) -> str:
        r = await client.post("/api/search/metadata", json={"size": 1})
        r.raise_for_status()
        items = r.json()["assets"]["items"]
        if not items:
            raise RuntimeError("the library is empty; run: make seed")
        return items[0]["id"]

    # -------------------------------------------------------------- loops
    async def _journey_loop(self, journey: Journey, ctx: ProbeContext, sink: CsvSink) -> None:
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                status = await asyncio.wait_for(journey.run(ctx), PROBE_TIMEOUT_S)
                ok = True
            except ProbeFailure as e:
                status, ok = e.status, False
            except (httpx.HTTPError, TimeoutError):
                status, ok = 0, False
            latency_ms = (time.perf_counter() - t0) * 1000
            self._record(sink, ProbeResult(time.time(), journey.name, ok, status, latency_ms))
            await self._wait(journey.interval_s - (time.perf_counter() - t0))

    async def _upload_loop(
        self, client: httpx.AsyncClient, files: list[Path], sink: CsvSink
    ) -> None:
        n = 0
        while not self._stop.is_set():
            path = files[n % len(files)]
            n += 1
            t0 = time.perf_counter()
            status, asset_id = 0, None
            try:
                now_iso = datetime.now(UTC).isoformat()
                name = UPLOAD_PREFIX + path.name
                r = await client.post(
                    "/api/assets",
                    data={"fileCreatedAt": now_iso, "fileModifiedAt": now_iso, "filename": name},
                    files={"assetData": (name, mutate(path.read_bytes()))},
                    timeout=UPLOAD_TIMEOUT_S,
                )
                status = r.status_code
                if status in (200, 201):
                    asset_id = r.json()["id"]
            except (httpx.HTTPError, KeyError, ValueError):
                status = 0
            latency_ms = (time.perf_counter() - t0) * 1000
            self._record(
                sink, ProbeResult(time.time(), "upload", asset_id is not None, status, latency_ms)
            )
            if asset_id:
                self.uploads.append(TrackedUpload(asset_id, time.time(), status, latency_ms))
            await self._wait(self.upload_interval_s - (time.perf_counter() - t0))

    async def _check_stage(self, client: httpx.AsyncClient, upload: TrackedUpload) -> None:
        """Poll the not-yet-complete stages of one upload."""
        now = time.time()
        try:
            if upload.thumbnail_s is None:
                r = await client.get(
                    f"/api/assets/{upload.asset_id}/thumbnail",
                    params={"size": "thumbnail"},
                    timeout=PROBE_TIMEOUT_S,
                )
                if r.status_code == 200:
                    upload.thumbnail_s = now - upload.uploaded_at
            if upload.indexed_s is None:
                # 400 until the smart-search embedding exists (needs the ML service)
                r = await client.post(
                    "/api/search/smart",
                    json={"queryAssetId": upload.asset_id, "size": 1},
                    timeout=PROBE_TIMEOUT_S,
                )
                if r.status_code == 200:
                    upload.indexed_s = now - upload.uploaded_at
        except httpx.HTTPError:
            pass  # server unreachable: simply not ready yet

    def _finalize(self, upload: TrackedUpload, sink: CsvSink, final: str) -> None:
        upload.final = final
        sink.write(
            [
                upload.asset_id,
                f"{upload.uploaded_at:.3f}",
                upload.accepted_status,
                f"{upload.upload_ms:.1f}",
                "" if upload.thumbnail_s is None else f"{upload.thumbnail_s:.1f}",
                "" if upload.indexed_s is None else f"{upload.indexed_s:.1f}",
                final,
            ]
        )

    async def _track_loop(self, client: httpx.AsyncClient, sink: CsvSink) -> None:
        while not self._stop.is_set():
            pending = [u for u in self.uploads if not u.final]
            if pending:
                await asyncio.gather(*(self._check_stage(client, u) for u in pending))
                now = time.time()
                for u in pending:
                    if u.processed:
                        self._finalize(u, sink, "processed")
                    elif now - u.uploaded_at > UPLOAD_GIVE_UP_S:
                        self._finalize(u, sink, "stalled")
            await self._wait(UPLOAD_TRACK_POLL_S)

    async def _queue_loop(self, client: httpx.AsyncClient, sink: CsvSink) -> None:
        while not self._stop.is_set():
            ts = f"{time.time():.3f}"
            try:
                r = await client.get("/api/jobs", timeout=PROBE_TIMEOUT_S)
                r.raise_for_status()
                data = r.json()
                snapshot = {q: data[q]["jobCounts"] for q in WATCHED_QUEUES if q in data}
                self.queues = snapshot
                for name, c in snapshot.items():
                    sink.write([ts, name, c["active"], c["waiting"], c["failed"]])
            except (httpx.HTTPError, KeyError, ValueError):
                self.queues = None
                sink.write([ts, "*", "", "", ""])  # blank row = queue endpoint unreachable
            await self._wait(QUEUE_POLL_S)

    # ---------------------------------------------------------------- run
    def _sample_files(self) -> list[Path]:
        if self.sample_dir is None or not self.sample_dir.is_dir():
            return []
        return sorted(p for p in self.sample_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)

    async def run(self, seconds: float | None = None) -> Path:
        """Run until `seconds` elapse, stop() is called, or the task is cancelled."""
        self._loop = asyncio.get_running_loop()
        probes_sink = CsvSink(self.out_dir / "probes.csv", PROBES_HEADER)
        queues_sink = CsvSink(self.out_dir / "queues.csv", QUEUES_HEADER)
        uploads_sink = CsvSink(self.out_dir / "uploads.csv", UPLOADS_HEADER)
        files = self._sample_files()
        self.upload_enabled = bool(files)
        headers = {"x-api-key": self.settings.api_key}
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.immich_url, headers=headers, timeout=PROBE_TIMEOUT_S
            ) as client:
                ctx = ProbeContext(client=client, asset_id=await self._pick_asset(client))
                self.started_at = time.time()
                tasks = [
                    asyncio.create_task(self._journey_loop(j, ctx, probes_sink))
                    for j in self.journeys
                ]
                tasks.append(asyncio.create_task(self._queue_loop(client, queues_sink)))
                if files:
                    tasks.append(asyncio.create_task(self._upload_loop(client, files, probes_sink)))
                    tasks.append(asyncio.create_task(self._track_loop(client, uploads_sink)))
                try:
                    if seconds is not None:
                        await asyncio.wait_for(self._stop.wait(), seconds)
                    else:
                        await self._stop.wait()
                except TimeoutError:
                    pass
                finally:
                    self._stop.set()
                    await asyncio.gather(*tasks, return_exceptions=True)
                for u in self.uploads:
                    if not u.final:
                        self._finalize(u, uploads_sink, "pending")
        finally:
            self.done = True
            for sink in (probes_sink, queues_sink, uploads_sink):
                sink.close()
        return self.out_dir
