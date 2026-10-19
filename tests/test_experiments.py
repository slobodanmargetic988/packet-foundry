import json

import httpx
import pytest
from packet_foundry_contracts import (
    ApplicationCreate,
    ClaimRequest,
    Completion,
    ExecutionProfile,
    ExperimentMetadata,
    GenerationRequest,
    Material,
    Packet,
    ResultPackage,
)
from packet_foundry_warehouse.db import BatchRow
from packet_foundry_warehouse.experiments import export_completed
from packet_foundry_warehouse.repository import WarehouseError


def finished_batch(repo, name):
    for i in range(2):
        p = Packet(application_id="demo", idempotency_key=name + str(i), batch_id=name,
            experiment=ExperimentMetadata(dataset_id="stable-suite", case_id=str(i)),
            request=GenerationRequest(instructions="Prompt variant " + name),
            materials=[Material(text="source " + str(i))])
        repo.submit("demo", p)
        job = repo.claim(ClaimRequest(factory_id="factory", profiles=[ExecutionProfile(
            profile_id="text", model="test-model", backend="fake")], resource_groups={"default": 1}))
        repo.complete(job.attempt_id, Completion(factory_id="factory", fencing_token=job.fencing_token,
                                               result=ResultPackage(text="Output " + name)))
    repo.batch("demo", name, seal=True)


async def test_native_experiment_batches_align_case_ids(repository):
    repository.create_application(ApplicationCreate(application_id="demo"))
    from pydantic import SecretStr
    repository.settings.langsmith_api_key = SecretStr("testing-key")
    finished_batch(repository, "first")
    finished_batch(repository, "second")
    bodies = []

    async def handler(request):
        assert request.url.path == "/api/v1/datasets/upload-experiment"
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json={"experiment": {"id": body["experiment_name"]},
                                        "dataset": {"id": body["dataset_id"]}})

    result = await export_completed(repository, transport=httpx.MockTransport(handler))
    assert result["exported"] == 2
    assert bodies[0]["dataset_id"] == bodies[1]["dataset_id"]
    assert {r["row_id"] for r in bodies[0]["results"]} == {r["row_id"] for r in bodies[1]["results"]}
    assert bodies[0]["results"][0]["inputs"] == bodies[1]["results"][0]["inputs"]
    assert (await export_completed(repository, transport=httpx.MockTransport(handler)))["exported"] == 0


async def test_uncertain_export_is_reconciled_without_reupload(repository):
    from pydantic import SecretStr
    repository.settings.langsmith_api_key = SecretStr("testing-key")
    repository.create_application(ApplicationCreate(application_id="demo"))
    finished_batch(repository, "first")
    posted = []

    async def handler(request):
        if request.method == "POST":
            posted.append(json.loads(request.content))
            raise httpx.ReadTimeout("synthetic lost response", request=request)
        body = posted[0]
        return httpx.Response(200, json=[{"id": "existing", "name": body["experiment_name"],
            "extra": {"metadata": body["experiment_metadata"]}}])

    transport = httpx.MockTransport(handler)
    assert (await export_completed(repository, transport=transport))["uncertain"] == 1
    assert (await export_completed(repository, transport=transport))["exported"] == 1
    assert len(posted) == 1
    with repository.sessions() as s:
        assert s.get(BatchRow, ("demo", "first")).export_state == "exported"


def test_comparison_cases_freeze_materials_across_batches(repository):
    repository.create_application(ApplicationCreate(application_id="demo"))
    finished_batch(repository, "first")
    changed = Packet(application_id="demo", idempotency_key="changed", batch_id="second",
        experiment=ExperimentMetadata(dataset_id="stable-suite", case_id="0"),
        request=GenerationRequest(instructions="Different prompt is allowed"),
        materials=[Material(text="changed source")])
    with pytest.raises(WarehouseError, match="source changed"):
        repository.submit("demo", changed)
