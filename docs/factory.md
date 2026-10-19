# Running a factory

The factory is a native Python worker for Windows, macOS and Linux. It opens only
outbound connections to its configured private registry. It never connects directly
to Warehouse or reads another service's database. Model servers remain local or
provider-hosted; installation does not download models or call paid inference.

## Install and configure

From a checkout with Python 3.12+ and uv:

```sh
uv sync --package packet-foundry-factory
uv run foundry-factory setup --config factory.json
uv run foundry-factory doctor --config factory.json
uv run foundry-factory run --config factory.json
```

Enroll the factory in the registry first and use the returned factory ID. Put the
issued token in `FOUNDRY_FACTORY_TOKEN`, or set `registry_token_env` to `null` and
`registry_token_file` to a private local file containing only that token. Every
factory has its own enrollment credential. Provider credentials use `api_key_env`
or `api_key_file`; never put literal tokens in JSON. Each computer needs its own
configuration and writable `data_dir`. Run `doctor --network` for registry health
and local Ollama/ComfyUI readiness probes; it still makes no inference calls.

The questionnaire asks for backend choices, models, resource groups/capacities,
live capability, idle experiment permission, and optional LangSmith/content capture.
Review input/output capabilities and approved workflow templates before running it.
Model IDs are operator choices, not silently selected fallbacks.
See [factory.example.json](../examples/factory.example.json).

Profiles advertise capabilities to the registry. Multiple profiles sharing a GPU
must use the same resource group with capacity one unless the operator has tested
parallel residency. Separate groups can run concurrently. Long polling requests a
job only when capacity is available; inference, polling, heartbeat and lease renewal
run independently. Scheduling and lease ownership remain Warehouse decisions.

## Backends

| Kind | Protocol and outputs | Relevant settings |
| --- | --- | --- |
| `openai` | Responses API, text/JSON; image/PDF references | `api_key_env`, optional `base_url` ending in `/v1` |
| `anthropic` | Messages API, text/JSON; image/PDF references | `api_key_env`, optional `base_url` ending in `/v1` |
| `openai_compatible` | Chat Completions, text/JSON and image references | explicit `base_url`, `auth_mode=none` for unauthenticated local servers |
| `ollama` | native `/api/chat`, text/JSON and image references | `base_url`, normally `auth_mode=none` |
| `codex`, `claude` | one fresh local CLI invocation, text/JSON | `executable`, `auth_mode=subscription` or explicit API credential reference |
| `gemini_image` | GenerateContent, image bytes, reference images | Google credential reference, explicit image model |
| `veo` | PredictLongRunning, video; optional single starting image | Google credential reference, timeout/poll interval |
| `openai_speech` | `/audio/speech`, MP3/WAV/Opus/AAC/FLAC/PCM | voice and format in `defaults` or allowed packet parameters |
| `comfyui` | approved API workflow, image/audio/video artifacts | local template graph, input mappings and explicit output nodes |

Text adapters reject tool calls and truncated output. JSON is parsed and validated
against the resolved immutable output schema. Packet parameters cannot change
credentials, provider endpoint, model, tools or local execution policy. Unsupported
parameters fail explicitly. Match advertised `input_kinds` to the chosen model:
model support can be narrower than the adapter's protocol support. No automatic
provider substitution occurs.

Codex and Claude use stdin for source material, isolated working directories, fresh
sessions and a small replacement system prompt. Codex ignores user configuration
while retaining its login, disables external/native tool features, and uses a
read-only sandbox. Claude uses `--tools ""`, one turn, empty strict MCP configuration,
disabled hooks/skills and no session persistence. Subscription mode removes ambient
provider API keys to prevent unintended API billing. It does not copy login files.
Run each CLI's normal login as the same OS user that runs the factory. `doctor`
checks required CLI flags. Old or incompatible versions fail rather than switching
to an unrestricted invocation. These CLI controls are not an OS security boundary;
only the installed provider executable runs, and unsupported tool events are errors.
Supported Codex overrides still retain built-in harness overhead: one local smoke
counted roughly 7,800 input tokens for a tiny request. A replacement prompt does not
guarantee zero hidden context. A single factory invocation also does not guarantee
exactly one internal HTTP call by the provider CLI; its own processing and transport
retries remain outside this adapter's control.

Provider parameter names follow their APIs. OpenAI accepts `temperature`, `top_p`,
`max_output_tokens`; compatible chat accepts `temperature`, `top_p`, `max_tokens`,
`seed`; Anthropic accepts `temperature`, `top_p`, `top_k`, `max_tokens`. Ollama accepts
`temperature`, `top_p`, `top_k`, `num_predict`, `num_ctx`, `seed`, `repeat_penalty`.
Gemini image accepts `aspectRatio`, `imageSize`, `seed`; Veo accepts `aspectRatio`,
`durationSeconds`, `resolution`, `negativePrompt`, `seed`, `personGeneration`.
Speech accepts `voice`, `response_format`, `speed`, `instructions`. Backend
`defaults` supply operator defaults; packet values override only these allowlists.
CLI runtime flags are never accepted from packets.

### Approved ComfyUI workflows

Export an API-format graph from ComfyUI and put it under a named local backend
`templates` entry. The packet sends only that template's identifier. It cannot send
a replacement graph. [comfyui-image.backend.json](../examples/comfyui-image.backend.json)
contains a complete standard Stable Diffusion graph with sampler, decoder and image
output. Replace its checkpoint placeholder with an installed compatible checkpoint
and place the backend object under a named factory backend. Advertise template
`sd-image-v1` on its execution profile. The example remains GPU-unverified; it neither
installs weights nor assumes a checkpoint is present.

Mapping source `prompt` joins the resolved shared/packet system rules with the
resolved instructions and inline materials, omitting blank blocks. Use it for the
usual positive-prompt node so shared generation rules are included. Existing
`instructions` preserves instructions/materials without system rules, and
`system_prompt` maps only those rules for workflows with a separate system input.
`parameter:<name>` and `material:<name>` map individual values. Only mapped
parameters are accepted.
Image materials are uploaded through `/upload/image`; inline text/JSON maps
directly into the approved node input. Workflow input node/field targets must
already exist. Outputs are fetched only from configured output nodes via the
configured server's `/view` route. Absolute paths, traversal, other output types
and arbitrary URLs are rejected. ComfyUI and any custom nodes are trusted local
operator software; review templates and node implementations before enabling them.

## Recovery and lifecycle

`journal.sqlite3` uses WAL and full synchronous commits. It stores attempt/fencing
identity, received assignments, provider operation IDs, local output manifests and
the exact pending completion. The spool uses portable hashed directory names.
Only one daemon may own a data directory. Protect it: inputs and generated content
are private material. `status` reports counts without exposing their contents.

Veo operation IDs and ComfyUI prompt IDs are committed **before the first poll**.
Restart resumes those operations while the lease remains valid. Network retries do
not resubmit a recorded operation. Async operation deadlines survive restart.
Completed output is persisted before upload, uploaded references are saved between
files, and exact completion bodies are retried until acknowledgement. Local files
remain after acknowledgement for operator-controlled retention; v1 performs no
automatic local deletion. Ensure the spool has adequate disk space and back it up
only with the same access controls as source material.

On cancellation, lost/expired lease or fencing conflict, processing stops and the
spool remains locally available. Stale work cannot overwrite an accepted outcome.
A crash between sending a provider request and recording its response is inherently
ambiguous when that provider offers no idempotency/lookup key. Such attempts fail
with `execution_uncertain` and require an explicit application retry; the factory
does not silently repeat an unknown paid generation. The system guarantees one
accepted outcome, not exactly-once provider execution.

`install-startup --config /absolute/path/factory.json` installs one current-user
startup entry: systemd user service on Linux, LaunchAgent on macOS, or an ONLOGON
Scheduled Task on Windows. Run it explicitly after `doctor` succeeds. Use absolute
paths and credential files for startup services, whose environment differs from
interactive shells. Provider CLI login must belong to this same account. Linux
startup before login additionally requires the operator's user-lingering setup.
Windows startup is at login; it does not require or install WSL. Use native provider
executables on Windows. `uninstall-startup` removes only this factory's entry.

CLI subprocesses run without shell interpolation, receive bounded stdin/stdout,
and are killed as a process group (POSIX) or process tree (`taskkill`, Windows) on
timeout/cancellation. Native OS installation/startup and real provider availability
must still be verified on each target machine; local mocked tests do not prove them.

For ephemeral cloud workers, provide nonsecret `FOUNDRY_FACTORY_CONFIG_JSON` and the
enrollment token separately as `FOUNDRY_FACTORY_TOKEN`. `foundry-factory run` without
`--config` reads the JSON. `FOUNDRY_FACTORY_ID` and `FOUNDRY_FACTORY_REGISTRY_URL`
can override their fields during registry bootstrap. Provider credentials stay in
separate environment variables referenced by that JSON. A factory does not provision
or stop cloud instances: registry owns cloud lifecycle.

## Optional LangSmith

Install `uv sync --package packet-foundry-factory --extra tracing`, enable
`tracing.enabled` and configure `tracing.api_key_env`. Set `capture_content=true`
only when inputs and outputs may be sent to the selected LangSmith endpoint; the
default sends identifiers and timing metadata without source content. Both factories
can use one project and retain distinct factory/profile/attempt identifiers.

With content capture enabled, input files and generated image/audio/video files are
uploaded through the SDK's supported attachments interface. File reads/export happen
off the execution loop. `tracing.max_attachment_bytes` bounds each input/output
attachment export (64 MiB by default); larger files are omitted with a local warning.
Content capture disabled sends no attachments. Result metadata includes the trace ID
so batch experiment manifests can link to the original media trace. Actual LangSmith
media rendering remains a live deployment check: mocked attachment-contract tests
establish the upload arguments, not its UI behavior. Warehouse's native experiment
export keeps artifact manifests; it does not itself upload binary attachments into
the comparison grid. Inspect those media outputs in their linked factory traces.

Batch, case, dataset, labels and tags are attached as run metadata. These are
application-owned comparison IDs even when they look like UUIDs; factories always
trace into the configured project without treating them as native LangSmith IDs.
Warehouse's optional completed-batch exporter uploads sealed batch outcomes as native
LangSmith experiments for comparison, linking back to the original factory trace.
Generation failures close traces with sanitized error codes. Tracing export
is asynchronous and fail-open: it never delays renewals or changes job success.
Missing credentials/dependency or an unavailable trace service is reported locally.

## Verification

```sh
uv run --all-packages pytest packages/factory/tests
uv run ruff check packages/factory
```

Tests use fake HTTP transports/executables and cover provider contracts, schema
validation, workflow approval/traversal rejection, secret isolation, interrupted
operation recovery, completion retries, resource concurrency and lease loss.
They neither spend provider credits nor establish real model compatibility.
Paid provider smoke tests and native Windows installation are deliberate opt-in
deployment checks, with evidence reported separately.

Provider protocol references: [OpenAI Codex configuration](https://developers.openai.com/codex/config-reference),
[Claude CLI](https://code.claude.com/docs/en/cli-reference),
[Veo operations](https://ai.google.dev/gemini-api/docs/veo),
[ComfyUI routes](https://docs.comfy.org/development/comfyui-server/comms_routes).
