import os
import sys
import asyncio
import requests
import time
import random
import logging
import argparse
import signal
from dotenv import load_dotenv
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict

from rich.console import Console
from rich.panel import Panel
from openai import AsyncOpenAI
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from drivers.terminal import TerminalDriver
from drivers.file_system import FileSystemDriver
from kernel.unms.memory import UNMSController
from kernel.runtime.loop import KernelRuntime
from kernel.state_machine import StateMachine, State
from kernel.notice_system import NoticeSystem, NoticeBoard, NoticeSeverity, thermal_aggregator, skill_aggregator
from kernel.watchdog import SystemWatchdog
from kernel.security.capabilities import CapabilityManager, make_terminal_caps, make_filesystem_caps

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"


# ==========================================
# 0. LOGGING & CONFIG
# ==========================================
def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )


@dataclass
class ExArchonConfig:
    openrouter_api_key: Optional[str] = None
    ollama_model: str = "qwen2.5:7b"
    ollama_base_url: str = "http://localhost:11434"
    working_dir: str = "./kernel_workspace"
    log_level: str = "INFO"
    enable_sensory_loop: bool = True
    enable_watchdog: bool = True
    state_journal_path: str = "./kernel_workspace/state.journal"

    @classmethod
    def from_env(cls) -> "ExArchonConfig":
        load_dotenv()
        wd = os.getenv("WORKING_DIR", "./kernel_workspace")
        return cls(
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            working_dir=wd,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            enable_sensory_loop=os.getenv("ENABLE_SENSORY_LOOP", "true").lower() == "true",
            enable_watchdog=os.getenv("ENABLE_WATCHDOG", "true").lower() == "true",
            state_journal_path=os.getenv("STATE_JOURNAL", os.path.join(wd, "state.journal")),
        )


# ==========================================
# 1. LLM PROVIDERS (ACL)
# ==========================================
class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        pass


class OpenRouterProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "openai/gpt-4o-mini"):
        self.client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        self.model = model
        self.logger = logging.getLogger("OpenRouterProvider")

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        try:
            completion = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ]
            )
            return completion.choices[0].message.content
        except Exception as e:
            self.logger.warning(f"OpenRouter error: {e}")
            return f"Kernel Error: {str(e)}"


class OllamaProvider(LLMProvider):
    def __init__(self, model="qwen2.5:7b", base_url="http://localhost:11434"):
        self.model = model
        self.base_url = f"{base_url}/api/generate"
        self.logger = logging.getLogger("OllamaProvider")

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        full_prompt = f"System Context: {system_prompt}\n\nUser Command: {prompt}" if system_prompt else prompt
        payload = {"model": self.model, "prompt": full_prompt, "stream": False, "options": {"temperature": 0.2}}
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(self.base_url, json=payload, timeout=60)
            )
            response.raise_for_status()
            return response.json().get("response", "Error: Empty response.")
        except requests.Timeout:
            self.logger.warning("Ollama request timed out after 60s")
            return "Kernel Error: Ollama timeout (60s). Model may be too slow or overloaded."
        except Exception as e:
            self.logger.warning(f"Ollama error: {e}")
            return f"Kernel Error: {str(e)}"


class ACLController:
    def __init__(self):
        self.providers: Dict[str, LLMProvider] = {}
        self.primary: Optional[str] = None
        self.backup: Optional[str] = None
        self.primary_status = "UNKNOWN"
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger("ACLController")
        self._fallback_logged = False

    def register_provider(self, name: str, provider: LLMProvider, is_primary=False):
        self.providers[name] = provider
        if is_primary:
            self.primary = name
        elif not self.backup:
            self.backup = name

    async def health_check(self) -> bool:
        if not self.primary:
            return False
        test_response = await self.providers[self.primary].generate("ping", "Reply with pong")
        if "Kernel Error" in test_response:
            self.primary_status = "FAILED"
            self.logger.warning(f"Primary provider {self.primary} health check failed")
            return False
        self.primary_status = "ONLINE"
        self._fallback_logged = False
        return True

    async def execute(self, prompt: str, system_prompt: str = "") -> str:
        sys_prompt = system_prompt or "You are ExArchon, a Cognitive OS."
        async with self._lock:
            if self.primary and self.primary_status == "ONLINE":
                result = await self.providers[self.primary].generate(prompt, sys_prompt)
                if "Kernel Error" not in result:
                    return result
                self.logger.warning(f"Primary {self.primary} failed, trying backup...")

            if self.backup and self.backup != self.primary:
                if not self._fallback_logged:
                    self.logger.info(f"Falling back to backup: {self.backup}")
                    self._fallback_logged = True
                return await self.providers[self.backup].generate(prompt, sys_prompt)

            if self.backup and self.backup == self.primary:
                if not self._fallback_logged:
                    self.logger.info(f"Using single provider: {self.backup}")
                    self._fallback_logged = True
                return await self.providers[self.backup].generate(prompt, sys_prompt)

            return "CRITICAL ERROR: All cognitive centers offline."


# ==========================================
# 2. KERNEL MANAGER (v4 Integrated)
# ==========================================
class KernelManager:
    def __init__(self, config: ExArchonConfig):
        self.config = config
        self.acl: Optional[ACLController] = None
        self.kernel: Optional[KernelRuntime] = None
        self.event_bus = EventBus()
        self.logger = logging.getLogger("KernelManager")
        self._initialized = False
        self.notice_system = NoticeSystem(db_path=os.path.join(config.working_dir, "notices.db"))
        self.notice_system.register_aggregator("sensory", thermal_aggregator)
        self.notice_system.register_aggregator("cortex", skill_aggregator)
        self.watchdog: Optional[SystemWatchdog] = None
        self.cap_manager = CapabilityManager()

    async def init(self) -> str:
        if self._initialized:
            return "ALREADY INITIALIZED"

        self.acl = ACLController()
        api_key = self.config.openrouter_api_key

        if api_key:
            self.acl.register_provider(
                "OpenRouter Cloud", OpenRouterProvider(api_key=api_key), is_primary=True
            )
            self.acl.register_provider(
                "Llama Edge",
                OllamaProvider(model=self.config.ollama_model, base_url=self.config.ollama_base_url),
                is_primary=False
            )
            cloud_ok = await self.acl.health_check()
            status_acl = "HYBRID (Cloud Active)" if cloud_ok else "HYBRID DOWN -> EDGE ONLY"
        else:
            self.acl.register_provider(
                "Llama Edge",
                OllamaProvider(model=self.config.ollama_model, base_url=self.config.ollama_base_url),
                is_primary=True
            )
            status_acl = "EDGE ONLY (Ollama)"

        memory = UNMSController(db_path=os.path.join(self.config.working_dir, "unms.db"))

        # Drivers with Capability Manager
        file_system = FileSystemDriver(
            working_dir=self.config.working_dir,
            capability_manager=self.cap_manager,
        )
        terminal = TerminalDriver(
            working_dir=self.config.working_dir,
            capability_manager=self.cap_manager,
        )

        # Register components in capability system
        self.cap_manager.register_component("file_system", make_filesystem_caps(self.config.working_dir))
        self.cap_manager.register_component("terminal", make_terminal_caps(self.config.working_dir))

        self.kernel = KernelRuntime(
            self.acl, memory,
            drivers={"terminal": terminal, "file_system": file_system},
            skill_db_path=os.path.join(self.config.working_dir, "skills.db"),
            state_journal_path=self.config.state_journal_path,
        )
        # Attach capability manager to kernel runtime
        if hasattr(self.kernel, 'cap_manager'):
            self.kernel.cap_manager = self.cap_manager

        # Attach notice system
        self.kernel.notice_system = self.notice_system

        # Watchdog
        if self.config.enable_watchdog:
            self.watchdog = SystemWatchdog(timeout_seconds=30.0)
            self.watchdog.set_panic_handler(self._on_watchdog_panic)
            self.watchdog.start()
            self.logger.info("Hardware/Software watchdog started.")

        self._initialized = True
        self.logger.info(f"Kernel initialized. ACL: {status_acl}")
        return status_acl

    def _on_watchdog_panic(self):
        """Emergency recovery triggered by watchdog."""
        try:
            if self.kernel and hasattr(self.kernel, 'state_machine'):
                self.kernel.state_machine.transition(State.RECOVERY)
            self.notice_system.post(
                title="Watchdog Triggered",
                message="Kernel was unresponsive for >30s. Entering recovery mode.",
                severity="critical",
                source="watchdog",
            )
        except Exception as e:
            self.logger.error(f"Watchdog panic handler failed: {e}")

    async def shutdown(self):
        self.logger.info("Kernel manager shutting down...")
        if self.watchdog:
            self.watchdog.stop()
        if self.kernel:
            if hasattr(self.kernel, "shutdown"):
                try:
                    self.kernel.shutdown()
                except Exception as e:
                    self.logger.warning(f"Kernel runtime shutdown error: {e}")
            if hasattr(self.kernel, "memory"):
                try:
                    await self.kernel.memory.close()
                except Exception as e:
                    self.logger.warning(f"Memory close error: {e}")
            if hasattr(self.kernel, "skill_library"):
                try:
                    self.kernel.skill_library.close()
                except Exception as e:
                    self.logger.warning(f"Skill library close error: {e}")
        if self.notice_system and hasattr(self.notice_system, 'close'):
            try:
                self.notice_system.close()
            except Exception as e:
                self.logger.warning(f"Notice system close error: {e}")
        self._initialized = False
        self.logger.info("Shutdown complete.")

    def ensure_ready(self):
        if not self._initialized or not self.kernel:
            raise RuntimeError("Kernel not initialized. Call init() first.")


# ==========================================
# 2.5 REFLEX SYSTEM
# ==========================================
class ReflexSystem:
    def __init__(self):
        self.triggers = {
            "привіт": "Вітаю, Founder. Ядро працює в штатному режимі. Чим можу допомогти?",
            "хто ти": "Я EXARCHON — Когнітивна Операційна Система. Ваш архітектурний шедевр.",
            "як справи": "Усі системи в нормі. Драйвери активні, пам'ять стабільна.",
            "статус": "Система онлайн. Fast Path активний. Sensory Loop у фоні.",
            "шо такоє": "Нічого особливого, чекаю на ваші накази, Архітекторе.",
            "exit": None,
            "вихід": None,
        }

    def check(self, prompt: str):
        clean_prompt = prompt.lower().strip()
        for key, response in self.triggers.items():
            if key in clean_prompt and len(clean_prompt) < 25:
                return response
        return None


# ==========================================
# 2.7 EVENT BUS & SENSORY LOOP
# ==========================================
class EventBus:
    def __init__(self):
        self.events = []

    def log_event(self, source: str, data: str, severity: str = "INFO"):
        event = f"[{time.ctime()}] [{severity}] {source}: {data}"
        self.events.append(event)
        if len(self.events) > 50:
            self.events.pop(0)
        return event

    def get_shadow_context(self):
        return "\n".join(self.events[-10:])


async def sensory_loop(manager: KernelManager, console, shutdown_event: asyncio.Event):
    os.makedirs(manager.config.working_dir, exist_ok=True)
    logger = logging.getLogger("SensoryLoop")

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=10)
            break
        except asyncio.TimeoutError:
            pass

        sensor_temp = 20 + random.randint(-5, 15)
        raw_event = f"Core temp: {sensor_temp}C"
        manager.notice_system.feed_raw("sensory", raw_event)

        if manager.watchdog:
            manager.watchdog.pet()

        try:
            telemetry_path = os.path.join(manager.config.working_dir, "shadow_telemetry.log")
            with open(telemetry_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.ctime()}] SENSOR_THERMAL: {raw_event}\n")
        except Exception as e:
            logger.warning(f"Telemetry write failed: {e}")

    logger.info("Sensory loop terminated gracefully.")


# ==========================================
# 3. FASTAPI SERVER
# ==========================================
class ExecuteRequest(BaseModel):
    task: str
    session_id: str = "founder_remote"


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[SYSTEM] Підняття Хмарного Ядра Ексархона...")
    app.state.manager = KernelManager(ExArchonConfig.from_env())
    status = await app.state.manager.init()
    print(f"[SYSTEM] ACL Status: {status}")
    print("[SYSTEM] EXARCHON Nexus API is ready!")
    yield
    print("[SYSTEM] Shutting down ExArchon Core...")
    await app.state.manager.shutdown()


app = FastAPI(title="EXARCHON Nexus API", lifespan=lifespan)


@app.get("/")
async def root():
    manager = getattr(app.state, 'manager', None)
    state_name = manager.kernel.state_machine.state.name if manager and manager.kernel else "unknown"
    return {"status": "online", "kernel_state": state_name, "message": "EXARCHON Cloud Core is running."}


@app.post("/execute")
async def execute_task(req: ExecuteRequest):
    manager: KernelManager = app.state.manager
    if not manager or not manager.kernel:
        raise HTTPException(status_code=500, detail="Kernel not initialized.")
    try:
        if manager.watchdog:
            manager.watchdog.pet()
        result = await manager.kernel.step(req.task, req.session_id)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def get_stats():
    manager: KernelManager = app.state.manager
    if not manager or not manager.kernel:
        raise HTTPException(status_code=500, detail="Kernel not initialized.")
    return manager.kernel.get_stats()


@app.get("/capabilities")
async def get_capabilities():
    manager: KernelManager = app.state.manager
    if not manager or not manager.cap_manager:
        raise HTTPException(status_code=500, detail="Capability system not initialized.")
    return {
        "components": manager.cap_manager.list_components(),
        "audit_preview": manager.cap_manager.get_audit_log(limit=20),
    }


# ==========================================
# 4. LOCAL INTERACTIVE CLI
# ==========================================
async def interactive_repl(console, manager: KernelManager, shutdown_event: asyncio.Event):
    reflexes = ReflexSystem()
    await asyncio.sleep(1)

    while not shutdown_event.is_set():
        try:
            user_input = await asyncio.to_thread(input, "\033[1;32m> [Founder]: \033[0m")
            if not user_input.strip() or ("Activate.ps1" in user_input):
                continue

            if user_input.lower() in ["exit", "quit", "вихід"]:
                console.print("\n[bold red][SYSTEM] Відключення систем. До зустрічі, Архітекторе.[/]")
                shutdown_event.set()
                break

            if user_input.lower() in ["notices", "нотиси", "статус"]:
                board_str = manager.notice_system.get_board().to_console_string(count=10)
                console.print("\n")
                console.print(Panel(
                    board_str,
                    title="[bold bright_yellow]Notice Board[/]",
                    border_style="bright_yellow",
                    padding=(1, 2)
                ))
                console.print("\n")
                continue

            if user_input.lower() in ["bg", "background", "фон"]:
                if manager.kernel and hasattr(manager.kernel, "get_background_status"):
                    bg_status = manager.kernel.get_background_status()
                    console.print("\n")
                    console.print(Panel(
                        bg_status,
                        title="[bold bright_magenta]Background Workers[/]",
                        border_style="bright_magenta",
                        padding=(1, 2)
                    ))
                    console.print("\n")
                continue

            if user_input.lower() in ["caps", "capabilities", "права"]:
                if manager.cap_manager:
                    audit = manager.cap_manager.get_audit_log(limit=15)
                    lines = [f"[{a['event']:8}] {a['source']:12} → {a.get('target', '-'):12} | {a['detail']}" for a in audit]
                    console.print("\n")
                    console.print(Panel(
                        "\n".join(lines) if lines else "No recent capability events.",
                        title="[bold bright_cyan]Capability Audit Log[/]",
                        border_style="bright_cyan",
                        padding=(1, 2)
                    ))
                    console.print("\n")
                continue

            fast_response = reflexes.check(user_input)
            if fast_response:
                console.print("\n")
                console.print(Panel(
                    fast_response,
                    title="[bold bright_green]Fast Path (Reflex)[/]",
                    border_style="bright_green",
                    padding=(1, 2)
                ))
                console.print("\n")
                continue

            with console.status("[bold cyan]ExArchon is processing (Deep Path)...", spinner="bouncingBar"):
                manager.ensure_ready()
                if manager.watchdog:
                    manager.watchdog.pet()
                response = await manager.kernel.step(user_input)

            console.print("\n")
            console.print(Panel(
                response,
                title="[bold bright_blue]Kernel (Deep Path)[/]",
                border_style="bright_blue",
                padding=(1, 2)
            ))
            console.print("\n")

        except KeyboardInterrupt:
            console.print("\n[bold yellow][SYSTEM] Interrupted. Shutting down...[/]")
            shutdown_event.set()
            break
        except EOFError:
            shutdown_event.set()
            break

    shutdown_event.set()


async def local_cli_main():
    console = Console()
    config = ExArchonConfig.from_env()
    setup_logging(config.log_level)

    with console.status("[bold blue]Performing Diagnostics...", spinner="dots"):
        manager = KernelManager(config)
        status_acl = await manager.init()

    logo = r"""
    [bold cyan]
     _____  __   __   ___    ____    ____   _   _    ___    _   _
    | ____| \ \ / /  / _ \  |  _ \  / ___| | | | |  / _ \  | \ | |
    |  _|    \ V /  / /_\ \ | |_) | | |    | |_| | | | | | |  \| |
    | |___    > <   |  _  | |  _ <  | |___ |  _  | | |_| | | |\  |
    |_____|  /_/ \__\|_| |_| |_| \__\  \____||_| |_|  \___/  |_| \_|
    [white]EXARCHON COGNITIVE OS LAYER // ALPHA v0.9.5[/white]
    [/bold cyan]
    """
    console.print(logo)
    console.print(Panel(
        f"● [bold white]ACL Layer:[/] {status_acl}\n"
        f"● [bold white]Kernel:[/] READY (Raphael Edition)\n"
        f"● [bold white]Reflex System:[/] ONLINE\n"
        f"● [bold white]Notice System:[/] ACTIVE\n"
        f"● [bold white]Background Workers:[/] ACTIVE\n"
        f"● [bold white]Sensory Loop:[/] ACTIVE\n"
        f"● [bold white]Capability System:[/] ACTIVE\n"
        f"● [bold white]Watchdog:[/] {'ACTIVE' if manager.watchdog else 'DISABLED'}",
        title="[bold white]System Status[/]",
        border_style="dim",
        expand=False
    ))

    shutdown_event = asyncio.Event()

    # Cross-platform signal handlers
    try:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: shutdown_event.set())
    except (NotImplementedError, AttributeError):
        # Windows ProactorEventLoop: add_signal_handler not supported
        # Fallback: KeyboardInterrupt is handled in interactive_repl
        pass

    tasks = []
    if config.enable_sensory_loop:
        tasks.append(asyncio.create_task(sensory_loop(manager, console, shutdown_event)))
    tasks.append(asyncio.create_task(interactive_repl(console, manager, shutdown_event)))

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        shutdown_event.set()
        console.print("\n[bold yellow][SYSTEM] KeyboardInterrupt received. Shutting down...[/]")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.getLogger("Main").error(f"Fatal error: {e}")
    finally:
        await manager.shutdown()
        console.print("\n[dim]ExArchon shutdown complete.[/dim]")


# ==========================================
# 5. ENTRY POINT
# ==========================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ExArchon Cognitive OS Kernel")
    parser.add_argument("--mode", choices=["repl", "api", "daemon"], default=None,
                        help="Run mode: repl (default local), api (FastAPI), daemon (unix socket)")
    args, unknown = parser.parse_known_args()

    if "PORT" in os.environ or "RAILWAY_ENVIRONMENT" in os.environ or args.mode == "api":
        port = int(os.environ.get("PORT", 8000))
        print(f"[BOOT] Cloud environment detected. Starting Uvicorn on port {port}...")
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        try:
            asyncio.run(local_cli_main())
        except KeyboardInterrupt:
            pass