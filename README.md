<div align="center">
  <a href="https://github.com/Manikse/kernel">
    <img src="docs/logo.png" alt="EXARCHON Logo" width="200">
  </a>

  <h1>EXARCHON</h1>

  <p>
    <b>The Distributed Cognitive Operating System Layer for Autonomous AI Agents.</b><br>
    Bridging probabilistic reasoning with deterministic execution, from the Cloud to the Edge.
  </p>
</div>

---
## Core Architecture

> 📖 **Deep Dive:** For an in-depth technical breakdown of the system's cognitive framework and execution loop, please refer to the [Architecture Documentation](docs/architecture.md).

<p align="center">
  <img src="https://img.shields.io/badge/version-v0.11.0--alpha-blue">
  <img src="https://img.shields.io/badge/status-bootstrapping-orange">
  <img src="https://img.shields.io/badge/status-alpha--hybrid-orange">
  <img src="https://img.shields.io/badge/license-MIT-green">
</p>

---

## Vision

The next evolution of artificial intelligence requires moving beyond conversational interfaces. EXARCHON is designed as a foundational Cognitive OS layer that bridges Large Language Models with independent, real-world execution.

It is not an API wrapper. It is the core infrastructure for an AI-native operating system capable of reasoning, persistent memory, and autonomous action—whether running as a highly scalable Cloud API or a completely offline Edge Node.

---

## Core Architecture

EXARCHON is built upon a distributed architecture designed for maximum survivability and autonomy:

### 1. Dual-Strategy ACL (Agent Control Layer)
The cognitive routing engine of the system. It handles high-level reasoning and intelligently routes logic based on network conditions:
* **Cloud Nexus Strategy:** Routes complex tasks to high-performance cloud models (Google Gemini, Groq) for maximum intelligence.
* **Edge Node Strategy:** Automatically detects network latency or rate limits (e.g., 429 Error) and falls back to a locally hosted LLM (Ollama) for uninterrupted, offline execution.

### 2. UNMS (Unified Neural Memory System)
The persistent state management system. EXARCHON retains context, learned data, and project objectives across execution cycles using a unified storage layer, supporting multi-tenant isolation for SaaS integrations.

### 3. A2E Drivers (Agent-to-Environment)
The execution interfaces allowing the Kernel to interact with the host environment. Protected by the **Shadow Protocol**, EXARCHON can analyze your file system securely without destructive overrides.
* **WebSearch Driver:** Real-time data retrieval.
* **Terminal Driver:** Execution of native OS commands (PowerShell/Bash/Linux).
* **FileSystem Driver:** Autonomous file management with Diff/Patch proposal mechanics.

---

## Key Features

- **Hybrid Edge-Cloud Routing:** Zero-downtime architecture. If the Cloud API drops, the system seamlessly transitions to local hardware processing.
- **Headless REST API:** Deployable to cloud environments (like Railway) as a scalable backend for SaaS platforms (e.g., VestaStack).
- **Shadow Protocol (Safe Autonomy):** System operates in a read-only global state by default, generating "Git-style" patches for code changes that require human approval before injection.
- **Reflection Loop (Self-Correction):** Automatically detects terminal execution errors and dynamically patches its own logic during runtime.
- **Asynchronous Daemon Worker:** True multi-threading capability for background tasks (system monitoring, log generation).

---

## Deployment Modes

EXARCHON supports two distinct deployment environments:

### Mode A: Cloud Nexus (Server API)
Designed for SaaS backends. Runs the EXARCHON Core as an API endpoint.
```bash
# Deployed via Docker / Railway
URL: [https://your-kernel.up.railway.app/execute](https://your-kernel.up.railway.app/execute)
```

### Mode B: Edge Node (Local / Offline)
Designed for local development and autonomous hardware.
```bash
git clone [https://github.com/Manikse/kernel](https://github.com/Manikse/kernel)
cd exarchon-core
pip install -r requirements.txt
```

**Environment Setup (`core/.env`)**
```env
GOOGLE_API_KEY="AIzaSy-your-google-key"
NETWORK_MODE="HYBRID" # Auto-switches to localhost:11434 (Ollama) if Cloud fails
```

**Run the Local Edge Agent**
```bash
python start.py
```

---

## Roadmap

- [x] **Phase 1: Terminal Alpha** - Core logic, CLI interface, and fundamental execution drivers.
- [x] **Phase 2: Cognitive Autonomy** - A2A Protocol, Self-Correction Loop, OS Awareness.
- [x] **Phase 3: Cloud Nexus** - Headless REST API deployment via Railway.
- [ ] **Phase 4: The Hybrid Edge (Current Focus)** - Local Ollama integration and latency-based fallback routing.
- [ ] **Phase 5: Shadow Protocol** - Safe file manipulation with human-in-the-loop approvals.
- [ ] **Phase 6: VestaStack Integration** - Full UI connection and multi-tenant memory implementation

---

## Disclaimer

This software is currently in **Beta release**

The Terminal Driver executes native commands on your host machine. While operations are sandboxed locally via the Shadow Protocol, please review generated code before executing in production environments. Do not run as root/administrator unless strictly necessary

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