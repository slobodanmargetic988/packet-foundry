# Security

Report suspected vulnerabilities privately to the repository owner's GitHub contact.
Avoid publishing credentials, customer materials, full provider payloads, or exploit
secrets in an issue. Until a private reporting channel is enabled, contact the owner
through their GitHub profile before sharing sensitive details.

Packet Foundry is intended for a single operator's private infrastructure. Run trusted
service code and trusted factory configuration only. Application callers supply data
and approved request parameters, not executable workflows or host commands. ComfyUI
workflows are operator-approved local templates. Installed CLI backends execute with
restricted one-shot settings; they still depend on the installed provider CLI.

Use unique scoped credentials, HTTPS/private networking, private PostgreSQL, protected
secrets, and encrypted backups where appropriate. Do not grant worker containers the
Docker socket or host administrator privileges. Keep provider and model-server ports
private. LangSmith capture, cloud provisioning, and provider integrations transmit data
to their configured third parties; enable these deliberately.

Secrets belong in environment variables, protected credential files, or a secret store,
never packet manifests, shared specifications, example files, committed configuration,
or logs. Rotate exposed credentials. Factory-side provider permissions are independent
from packet priority and do not implement spending limits.

Only the current fenced execution attempt may commit. At-least-once computation can
still occur after network failures; this is a reliability guarantee, not exactly-once
provider billing. Consumers must import results idempotently by packet ID.
