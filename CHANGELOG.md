## TerminalDriver Security Refactor

### [v0.10.1-alpha] - The Baseline
* **Architecture:** Executed raw LLM outputs directly using `shell=True` without validation.
* **Vulnerabilities:** Exposed the system to critical Command Injection risks, allowing the agent to accidentally or maliciously compromise the host environment.
* **Limitations:** Lacked Path Traversal protection, ignored exit codes, hardcoded timeouts, and had zero safeguards against destructive commands (e.g., `rm -rf`, fork bombs)

### [v0.11.0-alpha] - Security & Infrastructure Upgrade
* **Architecture:** Replaced the raw execution with a strictly sandboxed `TerminalDriver` featuring 3 configurable security tiers (Strict/Moderate/Disabled).
* **Security Fixes:** Introduced robust regex filtering for dangerous patterns, permanently blocked critical commands (e.g., `mkfs`, `dd`), and implemented built-in Path Traversal protection.
* **Reliability:** Failed or blocked executions now return context-aware system feedback (e.g., `[SECURITY BLOCKED]`), enabling the LLM agent to perform deterministic fault recovery.
* **Business Value:** Provides enterprise-grade protection against RCE (Remote Code Execution), fulfilling critical compliance requirements for air-gapped and secure infrastructure deployments.


## FileSystemDriver Security Refactor & Shadow Protocol

### [v0.10.1-alpha] - The Baseline
* **Architecture:** Raw, unrestricted file access given directly to the LLM agent.
* **Vulnerabilities:** Exposed to critical Path Traversal (`../`) attacks, allowing the agent to read, overwrite, or delete sensitive host system files outside the working directory.
* **Limitations:** Lacked audit logging, file size limits, automatic backups, and fail-safes for irreversible file modifications.

### [v0.11.0-alpha] - Security & Infrastructure Upgrade
* **Architecture:** Implemented `FileSystemDriver` with strict path resolution, sandboxing all operations within a dedicated `working_dir`.
* **Security Fixes:** Blocked absolute paths and Path Traversal attempts (`../`). Introduced `DANGEROUS_EXTENSIONS` blacklist and optional whitelist strictness to prevent malicious executable creation.
* **Shadow Protocol (Safe Mode):** Critical writes are now intercepted. Instead of directly overwriting files, the system generates a secure `.patch` with a unified diff and a unique SHA-256 hash. The agent must explicitly execute a `PATCH` command to apply changes, ensuring zero destructive accidents.
* **Reliability:** Added automatic `.bak` generation before any file modification, strict file size limits, and a comprehensive timestamped `.exarchon_fs_audit.log` for full state traceability.
* **Business Value:** Upgrades the runtime to enterprise compliance by guaranteeing completely deterministic file state changes, auditability, and protection against accidental or malicious host file corruption.


## UNMS (Unified Neural Memory System) v2.0 Integration

### [v0.10.1-alpha] - The Baseline
* **Architecture:** Ephemeral, dict-based memory runtime.
* **Limitations:** Context lost on restart/crash, zero long-term retrieval, token-window bloating due to lack of summarization or filtering.

### [v0.11.0-alpha] - Persistent Memory Upgrade
* **Architecture:** Implemented native SQLite backend with zero external dependencies, perfect for air-gapped deployments.
* **Retrieval & Context:** Integrated FTS5 (Full-Text Search) for semantic-like retrieval without heavy vector DBs. Created a Hybrid Context engine combining automatic summaries, importance-based long-term retention, and short-term sliding windows.
* **Business Value:** Guarantees absolute memory persistence across redeploys or system failures. Ensures predictable token usage and dramatically improves the agent's long-term reasoning capabilities in continuous enterprise workflows.

## Core State & Lifecycle Refactor

### [v0.10.1-alpha] - The Baseline
* **Architecture:** Relied on volatile global variables for core systems (Kernel, ACL, EventBus)
* **Limitations:** Hardcoded configurations, abrupt process terminations (`os._exit(0)`), and potential thread-safety/state corruption issues during API multi-worker scaling.

### [v0.11.0-alpha] - Production Lifecycle Upgrade
* **Architecture:** Engineered a centralized `KernelManager` for deterministic state handling and integrated proper dependency injection via FastAPI `lifespan` events.
* **Configuration:** Implemented type-safe environment management via `ExArchonConfig` (`.env` integration)
* **Reliability:** Replaced abrupt process kills with `asyncio.Event`-driven graceful shutdowns, strictly protecting the new UNMS SQLite database from corruption during container restarts.
* **Business Value:** Transforms the core engine from a local script into a robust, cloud-ready asynchronous service, laying the groundwork for scalable enterprise deployments.

## Cognitive Logic & Planning Refactor
[v0.10.1-alpha] - The Baseline
Architecture: Relied on an ephemeral, JSON-based planner that attempted to generate complete multi-step execution graphs in a single LLM pass.

Limitations: Highly fragile due to JSON formatting errors on local 7B models (missing brackets, hallucinations). The agent could not adapt if step 1 failed, resulting in broken execution loops and a lack of true autonomy.

Performance: Re-evaluated the same tasks from scratch every time, resulting in slow response times and wasted compute.

[v0.11.1-alpha] - ReAct Engine Upgrade
Architecture: Replaced the JSON planner with a robust ReActEngine (core/kernel/cortex/react_engine.py), implementing a step-by-step Thought → Action → Observation loop parsed via strict regex.

Reliability: The agent now reacts to environmental feedback dynamically. If a command fails or is intercepted by the Shadow Protocol, the agent receives it as an Observation and immediately course-corrects without crashing.

Business Value: Provides true 24/7 autonomous runtime capabilities, transforming the system from a static command executor into a resilient, self-correcting agent capable of navigating unpredictable air-gapped environments.

## Cognitive Muscle Memory & Speculative Branching
[v0.10.1-alpha] - The Baseline
Architecture: Zero task retention. The system possessed no mechanism to learn from successful executions.

Limitations: Single-threaded, linear problem-solving. Forced the LLM to waste time and tokens deducing solutions for recurring, identical operational tasks.

[v0.11.1-alpha] - Self-Learning Infrastructure Upgrade
Architecture: Engineered a multi-tiered execution loop (loop.py) integrating Reflex (0ms), Skill Retrieval (50ms), Speculative Branching, and ReAct Fallback.

Cognitive Muscle Memory: Implemented an SQLite-backed Skill Library (skills.db). Successful ReAct execution traces are automatically compiled into deterministic graphs. Recurring tasks bypass the LLM entirely, executing in ~50ms.

Speculative Branching: Introduced brancher.py to handle unknown tasks by spawning 3 parallel Agent-to-Agent (A2A) hypothesis branches. The first successful branch is compiled into a new skill, drastically accelerating complex problem resolution.

Business Value: Establishes a unique, self-improving infrastructure that becomes faster and cheaper to operate over time. Drastically cuts LLM token overhead for routine enterprise tasks while offering advanced, parallelized problem-solving that outperforms standard agent frameworks.