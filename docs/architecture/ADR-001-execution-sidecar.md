# ADR-001: Execution Sidecar Design

## Status: Proposed (spike/design only)

## Context

Agent Life Space currently runs all execution — subprocess calls, git operations,
filesystem reads, sandbox brokering — directly in the Python process. As the system
moves toward Builder/Operator modes, these operations need stronger isolation:

- **Security boundary**: subprocess execution should be minimally privileged
- **Resource isolation**: long-running builds shouldn't block the control plane
- **Audit granularity**: every host operation should be individually traced
- **Language fit**: Python is good for orchestration but not ideal for
  high-throughput, low-latency subprocess management

## Decision

Design a contract-first boundary for a future **execution sidecar** that handles:
- subprocess execution (git, lint, test, build)
- filesystem read operations (for review)
- sandbox broker (Docker container lifecycle)
- git operations (clone, diff, status, log)

The sidecar is NOT being built now. This ADR defines the boundary so that:
1. Python code can be refactored toward the boundary incrementally
2. When the sidecar is built, the contract is already defined
3. No premature rewrite happens

## What stays in Python

| Component | Reason |
|-----------|--------|
| Control plane (orchestration, routing, job lifecycle) | Python is the right fit |
| LLM integration (provider abstraction, prompting) | Python ecosystem is strongest |
| Memory/storage (SQLite, provenance, consolidation) | No performance pressure |
| Policy engine (tool policy, approvals, audit) | Deterministic, well-tested |
| Review analyzers (pattern matching, security scan) | Pure Python, fast enough |
| Channel adapters (Telegram, API) | I/O bound, Python is fine |

## What would move to sidecar

| Component | Current location | Reason to extract |
|-----------|-----------------|-------------------|
| `subprocess.run()` calls | `analyzers.py`, `agent_loop.py`, `programmer.py`, `learning.py` | Security boundary, isolation |
| Git operations | `analyzers.py:analyze_diff()` | Subprocess + auth concerns |
| Docker sandbox broker | `sandbox.py`, `sandbox_executor.py` | Resource isolation, lifecycle |
| Filesystem guard rails | Scattered across modules | Centralized access control |

## Contract Design

### Request Schema

```json
{
  "request_id": "uuid",
  "operation": "git_diff | subprocess_run | fs_read | sandbox_exec",
  "params": {
    "command": ["git", "diff", "HEAD~1..HEAD"],
    "cwd": "/path/to/repo",
    "timeout_seconds": 30,
    "max_output_bytes": 50000
  },
  "policy": {
    "allow_network": false,
    "allow_write": false,
    "resource_limits": {
      "memory_mb": 256,
      "cpu_seconds": 30
    }
  },
  "audit": {
    "job_id": "review-job-id",
    "requester": "review-service",
    "execution_mode": "read_only_host"
  }
}
```

### Response Schema

```json
{
  "request_id": "uuid",
  "status": "completed | failed | denied | timeout",
  "output": {
    "stdout": "...",
    "stderr": "...",
    "exit_code": 0,
    "duration_ms": 150
  },
  "denial": {
    "code": "policy_violation | resource_exceeded | not_allowed",
    "reason": "Write access denied in read_only mode"
  },
  "audit": {
    "started_at": 1711454400.0,
    "completed_at": 1711454400.15,
    "bytes_read": 1024,
    "bytes_written": 0
  }
}
```

### Allowed Operation Classes

| Operation | Read | Write | Network | Subprocess |
|-----------|------|-------|---------|------------|
| `fs_read` | yes | no | no | no |
| `git_diff` | yes | no | no | yes (git only) |
| `git_clone` | yes | yes (workspace) | yes (fetch) | yes |
| `subprocess_run` | yes | configurable | no | yes |
| `sandbox_exec` | yes | sandbox only | configurable | yes (Docker) |

### Denial Model

```
POLICY_VIOLATION    — operation not allowed by execution mode
RESOURCE_EXCEEDED   — timeout, memory, or output limit hit
NOT_ALLOWED         — operation class not permitted for this job type
AUTH_FAILED         — sidecar authentication failed
INTERNAL_ERROR      — sidecar crashed or unavailable
```

## Go vs Rust Evaluation

### Go

**Pros:**
- Excellent subprocess management (os/exec, context cancellation)
- Fast compilation, single binary deployment
- Strong concurrency (goroutines for parallel execution)
- Good Docker SDK (docker/docker client)
- Simpler to learn and maintain for ops team
- gRPC ecosystem is mature

**Cons:**
- GC pauses (not critical for this use case)
- No ownership guarantees for filesystem operations

### Rust

**Pros:**
- Zero-cost abstractions, no GC
- Strong safety guarantees (ownership, lifetimes)
- Better for filesystem guard rails (compile-time safety)
- Lower memory footprint

**Cons:**
- Steeper learning curve
- Slower compilation
- Smaller Docker SDK ecosystem
- Harder to iterate quickly during development

### Recommendation

**Go for initial sidecar.**

Reasoning:
1. Subprocess management is the primary use case — Go excels here
2. Development speed matters more than micro-optimization at this stage
3. Single binary deployment aligns with self-hosted operator model
4. Docker SDK in Go is production-tested
5. Can be replaced with Rust later if performance demands it

Rust would be better for:
- filesystem guard rails (if building a FUSE layer or syscall filter)
- extremely high-throughput sandbox broker
- embedding as a library rather than a service

## Communication Protocol

**Recommended: Unix socket + JSON-RPC or gRPC**

For single-machine deployment (self-hosted operator):
- Unix domain socket (no network overhead, permissions-based access)
- JSON-RPC for simplicity, gRPC for schema enforcement

For future multi-machine:
- gRPC over TLS with mTLS authentication

## Migration Path

1. **Now**: Define contracts (this ADR)
2. **Phase 1**: Extract Python subprocess calls behind an interface
   (`ExecutionClient` that calls subprocess directly)
3. **Phase 2**: Build Go sidecar implementing the same interface
4. **Phase 3**: Switch `ExecutionClient` from direct calls to sidecar RPC
5. **Phase 4**: Remove direct subprocess from Python

Each phase is independently deployable. No big bang.

## What this ADR does NOT decide

- Deployment topology (same host vs container vs separate machine)
- Authentication mechanism details
- Monitoring/metrics surface
- Specific gRPC proto definitions (defer to implementation phase)

## References

- `MASTER_SOURCE_OF_TRUTH.md` — Execution Plane (line 123)
- `THEMES_EPICS_STORIES.md` — T8-E1 Contract-First Boundaries
- Current subprocess usage: `agent/review/analyzers.py`, `agent/core/sandbox.py`,
  `agent/core/agent_loop.py`, `agent/brain/programmer.py`
