# EXARCHON: Core Architecture and Cognitive Framework

**Version:** v0.11.1-alpha  
**Status:** Active Development  
**Last Updated:** 2026-08-01

---

## 1. Abstract

EXARCHON is a **local-first cognitive OS kernel** for autonomous agents. Unlike frameworks that treat LLMs as oracles for every decision, EXARCHON implements a **three-tier cognitive stack** that separates reflexive response, learned execution, and live reasoning.

The kernel compiles successful agent executions into deterministic, reusable **skills** (Muscle Memory). For novel situations, it falls back to a **ReAct loop** with **speculative branching** — exploring multiple hypotheses in parallel via sub-agents.

Designed for air-gapped, offline, and critical infrastructure environments where cloud API dependency is a liability.

---

## 2. Design Principles

| Principle | Rationale |
|-----------|-----------|
| **Local-first** | No cloud required. Runs on CPU, Raspberry Pi, embedded hardware. |
| **Deterministic by default** | Repeated tasks execute via compiled skills, not LLM inference. |
| **Self-learning** | Successful traces auto-compile into reusable execution graphs. |
| **Sandboxed** | All environment interaction is read-only by default; changes require explicit approval. |
| **Failure is feedback** | Errors are captured, analyzed, and used to patch execution logic autonomously. |

---

## 3. Three-Tier Cognitive Stack

```
┌─────────────────────────────────────────────────────────────────┐
│ TIER 1: REFLEX (0 ms)                                          │
│ Hardcoded triggers for greetings, status checks, and system    │
│ diagnostics. Zero latency, 100% reliability.                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ TIER 2: MUSCLE MEMORY (50 ms)                                  │
│ SQLite-based Skill Library with keyword retrieval.              │
│ Successful executions are compiled into Execution Graphs.       │
│ No LLM call. No API cost. Deterministic replay.               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ TIER 3: LIVE CORTEX (3-10 sec)                                  │
│ ReAct Engine: Thought → Action → Observation loop.            │
│ Speculative Branching: 3 parallel hypotheses via A2A.         │
│ Successful traces auto-compile into new Skills.               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Core Components

### 4.1. Agent Control Layer (ACL)

The cognitive routing engine. Dynamically selects inference providers based on availability and latency.

- **Cloud Nexus:** OpenRouter (Gemini, Claude, Llama) for complex reasoning.
- **Edge Node:** Local Ollama instance. Zero network dependency.
- **Hot-failover:** Automatic fallback on 429/5xx. No human intervention.

### 4.2. Muscle Memory — Skill Library

Self-learning execution layer built on SQLite.

**Compilation:** A successful ReAct trace is transformed into an `ExecutionGraph` — a list of deterministic steps (`tool`, `action_input`).

**Retrieval:** Keyword-based Jaccard similarity matching against user input. Threshold: 0.55.

**Adaptation:** Each skill tracks `success_rate`, `usage_count`, and `avg_time_ms`. Low-performing skills are candidates for pruning.

**Persistence:** Skills survive process restarts, redeploys, and reboots.

### 4.3. Live Cortex — ReAct Engine

For situations where no skill exists, the kernel engages live reasoning.

**ReAct Loop:**
1. LLM generates a single `Thought`.
2. LLM selects an `Action` (tool name).
3. LLM provides `Action Input`.
4. Kernel executes and returns `Observation`.
5. Repeat until `Action: respond`.

**Prompt Sanitization:** User input is wrapped in delimiters to prevent injection attacks.

**Parsing:** Regex-based extraction of `Thought`, `Action`, `Action Input`. No JSON parsing — significantly more reliable than structured output on 7B local models.

### 4.4. Speculative Brancher

When facing a novel problem, the kernel does not guess sequentially. It **explores**.

1. LLM generates 2-3 alternative hypotheses (different strategies).
2. Each hypothesis spawns an A2A sub-agent with its own ReAct loop.
3. All branches execute in parallel.
4. The branch with the highest success score is selected.
5. The winning trace is compiled into a new Skill.

**Result:** 2-3x faster resolution for novel tasks compared to sequential ReAct.

### 4.5. Unified Neural Memory System (UNMS v2)

Persistent state management via SQLite + FTS5.

- **Multi-session isolation:** Each session has independent history.
- **Full-text search:** Relevant past interactions retrieved via FTS5.
- **Importance-based retention:** Critical interactions marked with higher importance scores.
- **Automatic cleanup:** Stale sessions purged after configurable TTL (default: 7 days).

### 4.6. Agent-to-Agent Protocol (A2A)

Decentralized task delegation. Sub-agents are ephemeral, purpose-built workers spawned for parallel execution branches.

### 4.7. Agent-to-Environment Drivers (A2E)

Sandboxed interfaces to the host system.

#### Terminal Driver
- **Sandbox levels:** `STRICT` (whitelist, no shell), `MODERATE` (blacklist patterns), `DISABLED` (unrestricted).
- **Path traversal protection:** Blocks `../`, absolute paths, and system directories.
- **Blocked commands:** `mkfs`, `fdisk`, `dd`, `format` — permanently disabled.

#### FileSystem Driver
- **Shadow Protocol:** Read-only by default. All writes generate a diff/patch requiring explicit approval.
- **Automatic backup:** Every modification creates a timestamped `.bak`.
- **Rollback:** One-command restoration to previous state.
- **Audit log:** All operations logged to `.exarchon_fs_audit.log`.

#### WebSearch Driver
Real-time data retrieval for augmenting reasoning context.

---

## 5. Execution Flow

### 5.1. Skill Hit (Typical Case)

```
User: "check disk space"
  ↓
Skill Library: keyword match (score: 0.85)
  ↓
Execution Graph: [terminal: "df -h"]
  ↓
Result: /dev/sda1  100G  45G  55G  45% /
  ↓
UNMS: log interaction
  ↓
Response time: ~50 ms
```

### 5.2. Skill Miss — Novel Task

```
User: "server is crashing, investigate"
  ↓
Skill Library: no match (best score: 0.23)
  ↓
Speculative Brancher:
  Branch A: "check RAM"      → [free, ps]          (score: 0.7)
  Branch B: "check disk"     → [df, du]            (score: 0.3)
  Branch C: "check network"  → [ping, netstat]     (score: 0.0)
  ↓
Winner: Branch A
  ↓
Compile winning trace → new Skill "investigate server crash"
  ↓
UNMS: log interaction
  ↓
Response time: ~5 sec (first time)
Next time: ~50 ms
```

---

## 6. Reflection Loop (Self-Healing)

When an A2E driver returns an error:

1. **Capture:** stderr is captured, not treated as fatal.
2. **Feedback:** Error trace is appended to the ReAct context.
3. **Mutation:** LLM generates a recovery step.
4. **Injection:** Recovery step is added to the active execution queue.
5. **Learning:** If recovery succeeds, the combined trace (original + recovery) is compiled into a Skill.

---

## 7. Deployment Architecture

### Local Edge Node
```bash
python start.py
```
Interactive CLI with sensory loop, reflex system, and full cognitive stack.

### Cloud Nexus (Headless)
```bash
docker-compose up
```
FastAPI server with `/execute` endpoint. Stateless requests, persistent memory via volume-mounted SQLite.

### Embedded / Air-gapped
Ollama + EXARCHON kernel. Zero network dependency. All reasoning happens locally.

---

## 8. Performance Characteristics

| Metric | Value | Notes |
|--------|-------|-------|
| Skill execution | ~50 ms | No LLM call |
| ReAct step | 3-10 sec | Depends on local model speed |
| Speculative branching | 5-15 sec | 3 parallel branches |
| Memory footprint | ~50 MB | SQLite + Python runtime |
| Cold start | ~2 sec | ACL health check + driver init |

---

## 9. Security Model

| Layer | Protection |
|-------|------------|
| Terminal | Sandbox levels, command whitelist/blacklist, path traversal blocks |
| FileSystem | Shadow Protocol (read-only default), diff/patch approval, automatic backup |
| Network | No outbound required. Optional cloud fallback only. |
| Prompt | Input sanitization, delimiter wrapping, injection detection |

---

## 10. Roadmap

- [x] Three-tier cognitive stack (Reflex / Muscle Memory / Live Cortex)
- [x] ReAct Engine with regex-based parsing
- [x] Speculative Branching via A2A sub-agents
- [x] Skill Library with SQLite persistence
- [x] Shadow Protocol for safe file operations
- [x] Hybrid Edge-Cloud ACL with hot-failover
- [ ] Conditional Execution Graphs (if/else in skills)
- [ ] Vector-based skill retrieval (embeddings)
- [ ] Edge Mesh: distributed agent clusters

---

## 11. References

- Yao et al. (2022). ReAct: Synergizing Reasoning and Acting in Language Models.
- Local-first software principles: https://www.inkandswitch.com/local-first/
- SQLite FTS5: https://www.sqlite.org/fts5.html

---

*For implementation details, see source code in `kernel-core/core/`.*
*For quick start, see README.md.*
