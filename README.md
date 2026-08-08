<div align="center">
  <a href="https://github.com/Manikse/ExArchon">
    <img src="docs/logo.png" alt="EXARCHON Logo" width="200">
  </a>

  <h1>EXARCHON</h1>

  <p>
    <b>The Distributed Cognitive Operating System for Autonomous AI Agents.</b><br>
    Bridging probabilistic reasoning with deterministic execution, from the Cloud to the Edge.
  </p>
</div>

---

<p align="center">
  <img src="https://img.shields.io/badge/version-v0.11.3--alpha-blue">
  <img src="https://img.shields.io/badge/status-alpha--hybrid-orange">
  <img src="https://img.shields.io/badge/license-MIT-green">
</p>

---

## Core Architecture

> 📖 For an in-depth technical breakdown of the system's cognitive framework and execution loop, please refer to the [Architecture Documentation](docs/architecture.md).

## Vision

The next evolution of artificial intelligence requires moving beyond conversational interfaces. EXARCHON is designed as a foundational Cognitive OS layer that bridges Large Language Models with independent, real-world execution.

It is not an API wrapper. It is the core infrastructure for an AI-native operating system capable of **reasoning**, **persistent memory**, **self-learning**, and **autonomous action** — whether running as a highly scalable Cloud API or a completely offline Edge Node.

---

### Three-Tier Cognitive Stack

EXARCHON processes every request through three layers of cognition:

```
┌─────────────────────────────────────────────────────────────┐
│  TIER 1: Reflex (0 ms)                                      │
│  Hardcoded responses for common greetings and status checks │
│  "привіт", "статус" → instant reply                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  TIER 2: Muscle Memory (50 ms)                              │
│  SQLite-based Skill Library with keyword retrieval          │
│  Successful executions are compiled into reusable skills    │
│  "check disk" → [terminal: df -h] → instant result          │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  TIER 3: Live Cortex (3-10 sec)                             │
│  ReAct Engine: Thought → Action → Observation loop          │
│  Speculative Branching: 3 parallel hypotheses               │
│  Successful traces auto-compile into new Skills             │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

#### 1. Dual-Strategy ACL (Agent Control Layer)
The cognitive routing engine. Intelligently routes logic based on network conditions:
* **Cloud Nexus:** Complex tasks → high-performance cloud models (OpenRouter, Gemini, Claude)
* **Edge Node:** Automatically falls back to local LLM (Ollama) on network failure or rate limits (429)

#### 2. UNMS v2 (Unified Neural Memory System)
Persistent state management with **SQLite + FTS5**:
* Multi-session isolation
* Full-text search for relevant context retrieval
* Importance-based retention
* Automatic stale session cleanup

#### 3. Muscle Memory (Skill Library)
Self-learning execution layer:
* **Compilation:** ReAct traces → deterministic Execution Graphs
* **Retrieval:** Keyword-based skill matching (Jaccard similarity)
* **Adaptation:** Success rates and usage stats auto-update
* **Persistence:** Skills survive redeploys and reboots

#### 4. A2E Drivers (Agent-to-Environment)
Sandboxed execution interfaces protected by the **Shadow Protocol**:
* **Terminal Driver:** Sandboxed OS commands (whitelist/blacklist, no `shell=True` by default)
* **FileSystem Driver:** Read-only default, Git-style diff/patch for writes, automatic backup
* **WebSearch Driver:** Real-time data retrieval

#### 5. Speculative Brancher
For novel tasks, EXARCHON doesn't guess — it **explores**:
* Generates 2-3 alternative hypotheses via LLM
* Executes all branches in parallel via A2A sub-agents
* Selects the branch with the highest success score
* Compiles the winner into a new Skill for future reuse

---

## Quick Start

### Local Edge Node (Offline / Air-gapped)

```bash
git clone https://github.com/Manikse/ExArchon.git
cd ExArchon/kernel-core
pip install -r requirements.txt
```

**Environment Setup (`core/.env`)**
```env
# Cloud API (optional — system works offline without it)
OPENROUTER_API_KEY="sk-or-v1-..."
# Or use: OPENROUTER_MODEL="anthropic/claude-3.5-sonnet"

# Local LLM (required for offline mode)
OLLAMA_MODEL="qwen2.5:7b"
OLLAMA_BASE_URL="http://localhost:11434"

# Working directory
WORKING_DIR="./kernel_workspace"
LOG_LEVEL="INFO"
```

**Run**
```bash
python start.py
```

### Cloud Nexus (Docker / Railway)

```bash
docker-compose up
```

API endpoint: `POST /execute`
```json
{
  "task": "check disk space",
  "session_id": "user_001"
}
```

---

## How Muscle Memory Works

### First Encounter (Slow)
```
> check disk space
[Deep Path] ReAct + Speculative Branching...
Thought: I need to check available disk space
Action: terminal
Action Input: df -h
Observation: /dev/sda1  100G  45G  55G  45% /
Action: respond
Action Input: Disk usage: 45% used, 55% free.
[Skill compiled and saved]
```

### Second Encounter (Instant)
```
> how much disk space is left
[Skill: check disk space]
/dev/sda1  100G  45G  55G  45% /
```

**No LLM call. No API cost. 50 milliseconds.**

---

## Roadmap

- [x] **Phase 1: Terminal Alpha** — Core logic, CLI, sandboxed execution drivers
- [x] **Phase 2: Cognitive Autonomy** — A2A Protocol, Self-Correction, OS Awareness
- [x] **Phase 3: Cloud Nexus** — Headless REST API, Docker deployment
- [x] **Phase 4: Muscle Memory** — Self-learning Skill Library, ReAct Engine, Speculative Branching
- [x] **Phase 5: Shadow Protocol** — Safe file manipulation with human-in-the-loop approvals
- [ ] **Phase 6: Deterministic Compiler** — Conditional logic and loops in Execution Graphs
- [ ] **Phase 7: Edge Mesh** — Distributed agent clusters across multiple offline nodes

---

## Security & Safety

EXARCHON is designed for **air-gapped and critical infrastructure** environments:

* **Sandboxed Terminal:** Commands run with configurable safety levels (STRICT/MODERATE/DISABLED)
* **Path Traversal Protection:** FileSystem driver blocks `../`, absolute paths, and system directories
* **Shadow Protocol:** All writes are read-only by default; changes require explicit patch approval
* **Automatic Backups:** Every file modification creates a timestamped backup with rollback support
* **Audit Logging:** All operations are logged to `.exarchon_fs_audit.log`

> ⚠️ **Beta Release:** The Terminal Driver executes native OS commands. While sandboxed, review generated code before production use. Do not run as root/administrator unless strictly necessary.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Runtime | Python 3.11, asyncio, FastAPI |
| Memory | SQLite + FTS5 |
| Embeddings | `sentence-transformers` (optional, 22MB) |
| LLM Routing | OpenRouter (cloud) / Ollama (local) |
| CLI | Rich (colors, panels, spinners) |
| Deployment | Docker, Docker Compose, Railway |

---

## Author & Support

Created by **Manikse** — Building the distributed infrastructure of the future

<div align="center"> 
  <a href="https://ko-fi.com/manikse"> 
    <img src="https://storage.ko-fi.com/cdn/kofi3.png?v=3" width="200"/> 
  </a> 
</div>

<div align="center">
  <a href="https://github.com/sponsors/Manikse">
    <img src="https://img.shields.io/badge/-Sponsor%20EXARCHON-ea4aaa?style=for-the-badge&logo=github&logoColor=white" width="220" alt="Sponsor EXARCHON"/>
  </a>
</div>



ExArchon - Autonomous engineering system kernel
Copyright (C) 2026 Manikse (Pavlo Blaida)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.