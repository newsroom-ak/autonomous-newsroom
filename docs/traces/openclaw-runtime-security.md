# OpenClaw Runtime Security Verification

## Assessment

- Project: Autonomous Newsroom
- Assessment: Confer Inc. AI/ML Take-Home
- Repository: newsroom-ak/autonomous-newsroom
- Verification date: October 11, 2026
- Environment: Local macOS development; Docker Desktop
- OpenClaw version: 2026.10.1
- Status: Baseline runtime isolation verified; additional controls pending

## 1. Purpose

Verify that the OpenClaw agent runtime uses authenticated,
localhost-only gateway access and that its Docker sandbox applies
the configured network, filesystem, privilege, and resource limits.

These tests verify observed configuration and runtime behavior.
They do not constitute a complete security audit.

## 2. Gateway Security

The dedicated OpenClaw profile is `confer-newsroom`.

Verified controls:

| Control | Observed result | Status |
|---|---|---|
| Gateway mode | local | PASS |
| Network binding | 127.0.0.1:54963 and [::1]:54963 | PASS |
| Authentication | Token authentication enabled | PASS |
| Invalid token | Rejected with token mismatch | PASS |
| Valid token | Gateway health returned ok: true | PASS |

An unauthenticated WebSocket connection received an initial
connection challenge but did not establish an authenticated session.
The test client closed the connection after five seconds.

The gateway is not directly listening on external network interfaces.

## 3. Local Model Execution

Provider: Ollama

Model: qwen2.5-coder:7b

Verified:

- Ollama server listening on 127.0.0.1:11434.
- Model successfully generated a response directly through Ollama.
- OpenClaw recognized the local model.
- OpenClaw successfully completed a harmless agent response.
- The model was configured with num_ctx = 32768.

These tests establish basic model connectivity, not the reliability
of autonomous tool use or newsroom-specific tasks.

## 4. OpenClaw-Managed Docker Sandbox

The following container was created by OpenClaw:

openclaw-sbx-workspace-9c89fa7112ad4128af8a3c1911b20dda

Docker inspection produced these results:

| Control | Observed result | Status |
|---|---|---|
| Backend | Docker | PASS |
| Network mode | none | PASS |
| Read-only root filesystem | true | PASS |
| Memory limit | 2147483648 bytes (2 GiB) | PASS |
| CPU limit | 1 CPU | PASS |
| Process limit | 128 | PASS |
| Dropped Linux capabilities | ALL | PASS |
| Container user | 1000:1000 | PASS |

The configured sandbox isolation controls were observed on the
actual OpenClaw-managed container, rather than only in a manually
created Docker test container.

## 5. Filesystem Mount Verification

Docker reported two mounts:

| Destination | Source category | Access |
|---|---|---|
| /workspace | Dedicated OpenClaw sandbox directory | Read/write |
| /workspace/skills | Subdirectory of sandbox workspace | Read-only |

Both sources are under:

~/.openclaw-confer-newsroom/sandboxes/

No direct bind mounts of the Mac home directory, GitHub
credentials, SSH credentials, or Oracle Cloud credentials
were observed.

Checks performed inside the running container:

| Path | Observed result |
|---|---|
| /Users/ankita/.ssh | NOT PRESENT |
| /Users/ankita/.oci | NOT PRESENT |
| /Users/ankita/.config/gh | NOT PRESENT |
| /workspace/skills | PRESENT |

These are targeted access checks, not proof that all possible
credential-access paths have been eliminated.

## 6. Additional Runtime Restrictions

The dedicated OpenClaw configuration includes:

- Elevated tools disabled.
- Tool command execution configured as denied.
- Browser control disabled.
- Docker sandbox enabled for all sessions.

Configuration validation passed.

The full set of agent tool permissions and resistance to
prompt injection have not yet been independently tested.

## 7. Remaining Risks and Limitations

1. Five npm dependency vulnerabilities were identified during
   installation: three high and two moderate. They remain unresolved.

2. GitHub credentials have not been provided to OpenClaw.
   Agent-specific GitHub permissions and runtime credential
   boundaries still require end-to-end testing.

3. The GitHub agent tokens use the same underlying machine account.
   Separate tokens alone do not guarantee independent agent identities
   or prevent misuse of status contexts by another token with
   commit-status write permission.

4. Docker sandbox network access is disabled. News retrieval,
   external APIs, and GitHub operations will require a separately
   designed and tested access mechanism.

5. Agent tool-use restrictions, prompt-injection handling,
   credential-exfiltration resistance, and application-level
   authorization require further verification.

6. The sandbox image is a locally built Debian-based image, not a
   verified official OpenClaw release image.

7. Oracle Cloud VM provisioning remains blocked by A1 instance
   capacity. Five networking resources were created and are tracked
   in the separate, local Terraform state.

## 8. Conclusion

The OpenClaw gateway and Docker sandbox passed the documented
baseline isolation checks in the local development environment.

The environment is suitable for continued restricted development
and testing, but it is not yet approved for unsupervised repository
modification, unrestricted network access, or production deployment.
