from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from packet_foundry_collector import (
    Collector,
    CollectorConfig,
    FileAdapter,
    Journal,
    Schedule,
    Scheduler,
    WarehouseClient,
)
from packet_foundry_contracts import ArtifactRef, Packet, ResultEnvelope, ResultPackage


def packet(**kwargs):
    return Packet(
        application_id="demo", idempotency_key="request-1",
        request={"instructions": "Create one result"}, **kwargs,
    )


def outcome(request=None, artifacts=None):
    return ResultEnvelope(
        packet=request or packet(), status="succeeded", completed_at=datetime.now(UTC),
        result=ResultPackage(text="Finished", artifacts=artifacts or []),
    )


class EmptyAdapter:
    def __init__(self):
        self.consumptions = []

    async def sources(self):
        return []

    async def prepare(self, source, client):
        raise AssertionError("no input")

    async def consume(self, envelope, client):
        self.consumptions.append(envelope.packet.packet_id)


async def test_lost_submission_response_reuses_original_manifest_after_restart(tmp_path):
    bodies = []
    original = packet()

    async def server(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(200, json={"packet": bodies[0], "status": "queued"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as transport:
        client = WarehouseClient("https://warehouse.example", "secret", client=transport)
        journal = Journal(tmp_path / "journal.sqlite3")
        collector = Collector(client, journal, EmptyAdapter())
        with pytest.raises(httpx.ReadTimeout):
            await collector.submit(original)
        journal.close()
        restarted = Journal(tmp_path / "journal.sqlite3")
        retried = Collector(client, restarted, EmptyAdapter())
        # A caller may reconstruct the logical packet with fresh generated fields.
        reconstructed = packet()
        assert reconstructed.packet_id != original.packet_id
        record = await retried.submit(reconstructed)
        assert record.packet.packet_id == original.packet_id
        assert bodies[0] == bodies[1]
        assert restarted.status()["pending_submissions"] == 0
        with pytest.raises(ValueError, match="different submission"):
            await retried.submit(packet(priority=99))
        restarted.close()


async def test_ack_failure_retries_after_restart_without_reconsuming(tmp_path):
    envelope = outcome()
    acknowledgements = 0

    async def server(request):
        nonlocal acknowledgements
        if request.url.path == "/v1/results":
            return httpx.Response(200, json=[envelope.model_dump(mode="json")])
        acknowledgements += 1
        if acknowledgements == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"acknowledged": True})

    adapter = EmptyAdapter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as transport:
        client = WarehouseClient("https://warehouse.example", "secret", client=transport)
        journal = Journal(tmp_path / "journal.sqlite3")
        collector = Collector(client, journal, adapter)
        with pytest.raises(httpx.ConnectError):
            await collector.collect()
        assert adapter.consumptions == [envelope.packet.packet_id]
        assert journal.status()["pending_acknowledgements"] == 1
        journal.close()
        journal = Journal(tmp_path / "journal.sqlite3")
        collector = Collector(client, journal, adapter)
        assert await collector.collect() == 1
        assert adapter.consumptions == [envelope.packet.packet_id]
        assert acknowledgements == 2
        assert journal.status()["acknowledged"] == 1
        journal.close()


async def test_no_ack_if_application_commit_failed(tmp_path):
    envelope = outcome()
    paths = []

    class BrokenAdapter(EmptyAdapter):
        async def consume(self, envelope, client):
            raise RuntimeError("application transaction rolled back")

    async def server(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=[envelope.model_dump(mode="json")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as transport:
        journal = Journal(tmp_path / "journal.sqlite3")
        collector = Collector(
            WarehouseClient("https://warehouse.example", "secret", client=transport),
            journal, BrokenAdapter(),
        )
        with pytest.raises(RuntimeError):
            await collector.collect()
        assert paths == ["/v1/results"]
        assert journal.status()["pending_consumption"] == 1
        journal.close()


async def test_file_adapter_replays_commit_after_journal_crash(tmp_path):
    content = b"binary output\x00\xff" * 100
    ref = ArtifactRef(
        artifact_id="output", sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content), mime_type="image/png", filename="../../escape.png",
    )
    envelope = outcome(artifacts=[ref])
    downloads = 0

    async def server(request):
        nonlocal downloads
        downloads += 1
        return httpx.Response(200, content=content)

    adapter = FileAdapter(tmp_path / "input", tmp_path / "output", "demo")
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as transport:
        client = WarehouseClient("https://warehouse.example", "secret", client=transport)
        await adapter.consume(envelope, client)
        # Simulate a process crash after application commit and before journal marker.
        await FileAdapter(tmp_path / "input", tmp_path / "output", "demo").consume(envelope, client)
    directory = adapter.result_directory(envelope.packet.packet_id)
    manifest = json.loads((directory / "result.json").read_text())
    assert downloads == 1
    assert manifest["envelope"]["packet"]["packet_id"] == envelope.packet.packet_id
    saved = directory / manifest["downloaded_artifacts"][0]["path"]
    assert saved.read_bytes() == content
    assert not (tmp_path / "escape.png").exists()
    saved.write_bytes(b"damaged")
    with pytest.raises(ValueError, match="checksum"):
        await adapter.consume(envelope, None)


async def test_corrupt_download_is_not_published_and_partial_is_removed(tmp_path):
    ref = ArtifactRef(
        artifact_id="output", sha256=hashlib.sha256(b"right").hexdigest(),
        size_bytes=5, mime_type="application/octet-stream",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"wrong"))
    ) as transport:
        client = WarehouseClient("https://warehouse.example", "secret", client=transport)
        target = tmp_path / "output.bin"
        with pytest.raises(ValueError, match="mismatch"):
            await client.download_artifact(ref, target)
        assert not target.exists()
        assert not list(tmp_path.glob("*.partial"))


async def test_file_prepare_uploads_binary_registers_specifications_and_is_staged(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    material = b"pdf-reference"
    (input_dir / "source.pdf").write_bytes(material)
    document = {
        "packet": {"request": {"instructions": "Summarize"}},
        "files": [{"path": "source.pdf", "kind": "document"}],
        "specification_documents": [{"spec_id": "voice", "version": "1", "instructions": "Brief"}],
    }
    (input_dir / "request.json").write_text(json.dumps(document))
    calls = []

    async def server(request):
        calls.append(request.url.path)
        assert request.headers["authorization"] == "Bearer secret"
        if request.url.path == "/v1/artifacts":
            body = await request.aread()
            assert body == material
            assert request.headers["x-content-sha256"] == hashlib.sha256(material).hexdigest()
            return httpx.Response(201, json={
                "artifact_id": "source", "sha256": hashlib.sha256(material).hexdigest(),
                "size_bytes": len(material), "mime_type": "application/pdf",
            })
        if request.url.path == "/v1/specifications":
            return httpx.Response(201, json={"spec_id": "voice", "version": "1", "sha256": "a" * 64})
        submitted = json.loads(request.content)
        assert submitted["materials"][0]["artifact"]["artifact_id"] == "source"
        assert submitted["specifications"][0]["version"] == "1"
        return httpx.Response(202, json={"packet": submitted, "status": "queued"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as transport:
        journal = Journal(tmp_path / "journal.sqlite3")
        collector = Collector(
            WarehouseClient("https://warehouse.example", "secret", client=transport), journal,
            FileAdapter(input_dir, tmp_path / "output", "demo"),
        )
        assert await collector.send() == 1
        assert await collector.send() == 0
        assert calls == ["/v1/artifacts", "/v1/specifications", "/v1/packets"]
        journal.close()


@pytest.mark.parametrize("reference", ["../secret.txt", "/etc/passwd"])
async def test_file_prepare_rejects_paths_outside_input(tmp_path, reference):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (tmp_path / "secret.txt").write_text("secret")
    (input_dir / "bad.json").write_text(json.dumps({
        "packet": {"request": {"instructions": "bad"}},
        "files": [{"path": reference, "kind": "document"}],
    }))
    adapter = FileAdapter(input_dir, tmp_path / "output", "demo")
    with pytest.raises(ValueError):
        await adapter.prepare((await adapter.sources())[0], None)


async def test_symlink_material_escape_is_rejected(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    secret = tmp_path / "private"
    secret.write_text("not an input")
    try:
        (input_dir / "link").symlink_to(secret)
    except OSError:
        pytest.skip("symlink privilege unavailable on this platform")
    (input_dir / "bad.json").write_text(json.dumps({
        "packet": {"request": {"instructions": "bad"}},
        "files": [{"path": "link", "kind": "document"}],
    }))
    adapter = FileAdapter(input_dir, tmp_path / "output", "demo")
    with pytest.raises(ValueError):
        await adapter.prepare((await adapter.sources())[0], None)


async def test_wait_terminal_without_ack_and_times_out_without_cancel(tmp_path):
    request = packet(live=True)
    calls = []
    records = iter(["queued", "leased", "succeeded"])

    async def server(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={"packet": request.model_dump(mode="json"), "status": next(records)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as transport:
        journal = Journal(tmp_path / "journal.sqlite3")
        collector = Collector(
            WarehouseClient("https://warehouse.example", "secret", client=transport),
            journal, EmptyAdapter(),
        )
        assert (await collector.wait(request.packet_id, poll_interval=0.001)).status == "succeeded"
        assert len(calls) == 3
        assert all(path == f"/v1/packets/{request.packet_id}" for path in calls)
        journal.close()


async def test_schedule_independence_coalescing_and_persistent_progress(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    calls = []

    async def send():
        calls.append("send")

    async def collect():
        calls.append("collect")

    def scheduler(journal):
        return Scheduler(journal, {
            "send": (Schedule(interval_seconds=3600), send),
            "collect": (Schedule(interval_seconds=10), collect),
        }, clock=lambda: now)

    journal = Journal(tmp_path / "journal.sqlite3")
    assert await scheduler(journal).run_pending() == {"send": True, "collect": True}
    now += timedelta(seconds=11)
    assert await scheduler(journal).run_pending() == {"collect": True}
    journal.close()
    journal = Journal(tmp_path / "journal.sqlite3")
    now += timedelta(days=3)
    assert await scheduler(journal).run_pending() == {"send": True, "collect": True}
    assert calls == ["send", "collect", "collect", "send", "collect"]
    assert await scheduler(journal).run_pending() == {}
    journal.close()


async def test_failed_send_does_not_prevent_collection_or_advance_progress(tmp_path):
    calls = []

    async def broken():
        calls.append("send")
        raise RuntimeError("failed")

    async def collect():
        calls.append("collect")

    journal = Journal(tmp_path / "journal.sqlite3")
    scheduler = Scheduler(journal, {
        "send": (Schedule(interval_seconds=60), broken),
        "collect": (Schedule(interval_seconds=60), collect),
    })
    assert await scheduler.run_pending() == {"send": False, "collect": True}
    assert await scheduler.run_pending() == {"send": False}
    assert calls == ["send", "collect", "send"]
    journal.close()


def test_cron_handles_dst_gap_fold_and_minutely_order():
    daily = Schedule(cron="30 2 * * *", timezone="Europe/Belgrade")
    assert daily.next_after(datetime(2026, 3, 28, 2, tzinfo=UTC)) == datetime(2026, 3, 30, 0, 30, tzinfo=UTC)
    assert daily.next_after(datetime(2026, 10, 25, 0, 29, tzinfo=UTC)) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert daily.next_after(datetime(2026, 10, 25, 0, 30, tzinfo=UTC)) == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    every_minute = Schedule(cron="* * * * *", timezone="Europe/Belgrade")
    assert every_minute.next_after(datetime(2026, 10, 25, 0, 50, tzinfo=UTC)) == datetime(2026, 10, 25, 0, 51, tzinfo=UTC)


def test_config_relative_paths_secrets_and_url_validation(tmp_path, monkeypatch):
    config_path = tmp_path / "collector.json"
    config_path.write_text(json.dumps({"application_id": "demo", "warehouse_url": "https://warehouse.example"}))
    config = CollectorConfig.load(config_path)
    assert config.journal == tmp_path / "state" / "collector.sqlite3"
    monkeypatch.delenv("FOUNDRY_APPLICATION_TOKEN", raising=False)
    with pytest.raises(ValueError, match="environment variable"):
        config.token()
    monkeypatch.setenv("FOUNDRY_APPLICATION_TOKEN", "secret")
    assert config.token() == "secret"
    assert "secret" not in config.model_dump_json()
    with pytest.raises(ValueError):
        CollectorConfig(application_id="demo", warehouse_url="https://user:secret@example.com")


async def test_collect_schedule_progresses_while_send_is_still_running(tmp_path):
    import asyncio

    started = asyncio.Event()
    collected_twice = asyncio.Event()
    count = 0

    async def slow_send():
        started.set()
        await asyncio.Event().wait()

    async def collect():
        nonlocal count
        await started.wait()
        count += 1
        if count >= 2:
            collected_twice.set()

    journal = Journal(tmp_path / "journal.sqlite3")
    scheduler = Scheduler(journal, {
        "send": (Schedule(interval_seconds=10), slow_send),
        "collect": (Schedule(interval_seconds=0.001), collect),
    })
    runner = asyncio.create_task(scheduler.run(tick_seconds=0.001))
    try:
        await asyncio.wait_for(collected_twice.wait(), 1)
        assert count >= 2
    finally:
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner
        journal.close()


def test_invalid_or_impossible_schedule_is_rejected():
    for fields in (
        {"cron": "0 0 31 2 *"},
        {"cron": "* * * * * *"},
        {"interval_seconds": 0},
        {"interval_seconds": 1, "cron": "* * * * *"},
        {"interval_seconds": 1, "timezone": "missing/timezone"},
    ):
        with pytest.raises(ValueError):
            Schedule(**fields)


async def test_client_rejects_path_injection():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: pytest.fail("unsafe request must not reach the server")
    )) as transport:
        client = WarehouseClient("https://warehouse.example", "secret", client=transport)
        for identifier in ("../results", "..", ".", "a/b?token=secret"):
            with pytest.raises(ValueError):
                await client.status(identifier)


def test_cli_status_uses_no_network_or_credential(tmp_path, monkeypatch):
    from packet_foundry_collector.cli import app
    from typer.testing import CliRunner

    config_path = tmp_path / "collector.json"
    config_path.write_text(json.dumps({
        "application_id": "demo", "warehouse_url": "https://warehouse.example",
    }))
    monkeypatch.delenv("FOUNDRY_APPLICATION_TOKEN", raising=False)
    result = CliRunner().invoke(app, ["status", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["pending_submissions"] == 0
    send_result = CliRunner().invoke(app, ["send", "--config", str(config_path)])
    assert send_result.exit_code == 1
    assert "set the FOUNDRY_APPLICATION_TOKEN environment variable" in send_result.output
