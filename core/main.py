import os
import sys
import asyncio
import requests
import time
import random
from dotenv import load_dotenv
from abc import ABC, abstractmethod
from rich.console import Console
from rich.panel import Panel
from openai import AsyncOpenAI
from contextlib import asynccontextmanager

# FastAPI Imports
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from drivers.terminal import TerminalDriver
from drivers.web_search import WebSearchDriver
from drivers.file_system import FileSystemDriver
from kernel.unms.memory import UNMSController
from kernel.runtime.loop import KernelRuntime

# БЕЗПЕЧНИЙ ФІКС КОДУВАННЯ
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

# ==========================================
# 1. КОГНІТИВНІ ДРАЙВЕРИ (ACL)
# ==========================================
class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        pass

class OpenRouterProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "google/gemini-2.0-flash-001"):
        self.client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        self.model = model

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        try:
            completion = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
            )
            return completion.choices[0].message.content
        except Exception as e:
            return f"Kernel Error: {str(e)}"

class OllamaProvider(LLMProvider):
    def __init__(self, model="qwen2.5:7b", base_url="http://localhost:11434"):
        self.model = model
        self.base_url = f"{base_url}/api/generate"

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        full_prompt = f"System Context: {system_prompt}\n\nUser Command: {prompt}" if system_prompt else prompt
        payload = {"model": self.model, "prompt": full_prompt, "stream": False, "options": {"temperature": 0.2}}
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: requests.post(self.base_url, json=payload, timeout=300))
            response.raise_for_status()
            return response.json().get("response", "Error: Empty response.")
        except Exception as e:
            return f"Kernel Error: {str(e)}"

class ACLController:
    def __init__(self):
        self.providers = {}
        self.primary = None
        self.backup = None
        self.primary_status = "UNKNOWN"
        self._lock = asyncio.Lock()

    def register_provider(self, name: str, provider: LLMProvider, is_primary=False):
        self.providers[name] = provider
        if is_primary: self.primary = name
        elif not self.backup: self.backup = name

    async def health_check(self):
        if self.primary:
            test_response = await self.providers[self.primary].generate("ping", "Reply with pong")
            if "Kernel Error" in test_response:
                self.primary_status = "FAILED"
                return False
            self.primary_status = "ONLINE"
            return True
        return False

    async def execute(self, prompt: str, system_prompt: str = "") -> str:
        sys_prompt = system_prompt or "You are ExArchon, a Cognitive OS."
        async with self._lock:
            if self.primary and self.primary_status == "ONLINE":
                result = await self.providers[self.primary].generate(prompt, sys_prompt)
                if "Kernel Error" not in result: return result
            if self.backup:
                return await self.providers[self.backup].generate(prompt, sys_prompt)
            return "CRITICAL ERROR: Усі когнітивні центри офлайн."

# ==========================================
# 2. ГЛОБАЛЬНЕ ЯДРО ТА ІНІЦІАЛІЗАЦІЯ
# ==========================================
global_kernel = None
global_acl = None

async def init_core_systems(verbose=True):
    global global_kernel, global_acl
    load_dotenv()
    API_KEY = os.getenv("OPENROUTER_API_KEY") 
    
    global_acl = ACLController()
    if API_KEY:
        global_acl.register_provider("OpenRouter Cloud", OpenRouterProvider(api_key=API_KEY), is_primary=True)
        global_acl.register_provider("Llama Edge", OllamaProvider(), is_primary=False)
        cloud_ok = await global_acl.health_check()
        status_acl = "HYBRID (Cloud Active)" if cloud_ok else "HYBRID DOWN -> EDGE ONLY"
    else:
        global_acl.register_provider("Llama Edge", OllamaProvider(), is_primary=True)
        status_acl = "EDGE ONLY (Ollama)"

    memory = UNMSController()
    file_system = FileSystemDriver(working_dir="./kernel_workspace")
    web_search = WebSearchDriver()
    terminal = TerminalDriver(working_dir="./kernel_workspace")
    
    global_kernel = KernelRuntime(global_acl, memory, drivers={
        "web_search": web_search, "terminal": terminal, "file_system": file_system
    })
    return status_acl

# ==========================================
# 2.5 СИСТЕМА РЕФЛЕКСІВ (FAST PATH)
# ==========================================
class ReflexSystem:
    def __init__(self):
        self.triggers = {
            "привіт": "Вітаю, Founder. Ядро працює в штатному режимі. Чим можу допомогти?",
            "хто ти": "Я EXARCHON — Когнітивна Операційна Система. Ваш архітектурний шедевр.",
            "як справи": "Усі системи в нормі. Драйвери активні, пам'ять стабільна.",
            "статус": "Система онлайн. Fast Path активний. Sensory Loop у фоні.",
            "шо такоє": "Нічого особливого, чекаю на ваші накази, Архітекторе."
        }

    def check(self, prompt: str):
        clean_prompt = prompt.lower().strip()
        for key, response in self.triggers.items():
            if key in clean_prompt and len(clean_prompt) < 20:
                return response
        return None

# ==========================================
# 2.7 SENSORY LOOP ТА EVENT BUS (ТІНЬОВИЙ КОНТЕКСТ)
# ==========================================
class EventBus:
    def __init__(self): # ВИПРАВЛЕНО: Було init замість __init__
        self.events = []
        
    def log_event(self, source: str, data: str, severity: str = "INFO"):
        event = f"[{time.ctime()}] [{severity}] {source}: {data}"
        self.events.append(event)
        if len(self.events) > 50:
            self.events.pop(0)
        return event

    def get_shadow_context(self):
        return "\n".join(self.events[-10:])

global_event_bus = EventBus()

async def sensory_loop(acl, console):
    os.makedirs("kernel_workspace", exist_ok=True)
    
    while True:
        await asyncio.sleep(10) 
        
        sensor_temp = 20 + random.randint(-5, 15) 
        
        if sensor_temp > 33:
            severity = "CRITICAL"
            msg = f"Температура ядра перевищила норму: {sensor_temp} C"
        else:
            severity = "INFO"
            msg = f"Температура в нормі: {sensor_temp} C"
            
        event_log = global_event_bus.log_event("SENSOR_THERMAL", msg, severity)
        
        with open("kernel_workspace/shadow_telemetry.log", "a", encoding="utf-8") as f:
            f.write(event_log + "\n")

        # АЛЕРТИ ТА ДІП-ПАС ТИМЧАСОВО ВІДКЛЮЧЕНІ ДЛЯ ДЕМО
        # if severity == "CRITICAL":
        #     console.print(f"\n[bold red][ALERT] АНОМАЛІЯ СЕНСОРА: {msg}[/]")
        #     console.print("[dim yellow][SYSTEM] Автономна ініціалізація Deep Path...[/]")
        #     crisis_prompt = f"Системна криза. Останні дані з сенсорів:\n{global_event_bus.get_shadow_context()}\nДій швидко, напиши коротке рішення проблеми."
        #     try:
        #         response = await acl.execute(crisis_prompt, "You are an autonomous crisis manager.")
        #         console.print(Panel(response, title="[bold red]Autonomic Crisis Response (Deep Path)[/]", border_style="red"))
        #         await asyncio.sleep(10) 
        #     except Exception as e:
        #         console.print(f"[bold red][ERROR] Помилка кризового менеджера: {e}[/]")

# ==========================================
# 3. FASTAPI SERVER (ДЛЯ RAILWAY / CLOUD)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[SYSTEM] Підняття Хмарного Ядра Ексархона...")
    status = await init_core_systems(verbose=False)
    print(f"[SYSTEM] ACL Status: {status}")
    print("[SYSTEM] EXARCHON Nexus API is ready!")
    yield 
    print("[SYSTEM] Shutting down ExArchon Core...")

app = FastAPI(title="EXARCHON Nexus API", lifespan=lifespan)

class ExecuteRequest(BaseModel):
    task: str
    session_id: str = "founder_remote" 

@app.get("/")
async def root():
    return {"status": "online", "message": "EXARCHON Cloud Core is running."}

@app.post("/execute")
async def execute_task(req: ExecuteRequest):
    if not global_kernel: raise HTTPException(status_code=500, detail="Kernel not initialized.")
    try:
        result = await global_kernel.step(req.task, req.session_id)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# 4. LOCAL INTERACTIVE CLI 
# ==========================================
async def interactive_repl(console):
    reflexes = ReflexSystem()
    await asyncio.sleep(1)
    
    while True:
        try:
            user_input = await asyncio.to_thread(input, "\033[1;32m> [Founder]: \033[0m")
            if not user_input.strip() or ("Activate.ps1" in user_input): continue
            
            if user_input.lower() in ["exit", "quit", "вихід"]:
                console.print("\n[bold red][SYSTEM] Відключення систем. До зустрічі, Архітекторе.[/]")
                os._exit(0)
                
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
                response = await global_kernel.step(user_input)
                
            console.print("\n")
            console.print(Panel(
                response, 
                title="[bold bright_blue]Kernel (Deep Path)[/]", 
                border_style="bright_blue", 
                padding=(1, 2)
            ))
            console.print("\n")
            
        except KeyboardInterrupt: 
            os._exit(0)

async def local_cli_main():
    console = Console()
    with console.status("[bold blue]Performing Diagnostics...", spinner="dots"):
        status_acl = await init_core_systems(verbose=True)
        
    logo = r"""
    [bold cyan]
     _____  __   __   ___    ____    ____   _   _    ___    _   _
    | ____| \ \ / /  / _ \  |  _ \  / ___| | | | |  / _ \  | \ | |
    |  _|    \ V /  / /_\ \ | |_) | | |    | |_| | | | | | |  \| |
    | |___    > <   |  _  | |  _ <  | |___ |  _  | | |_| | | |\  |
    |_____|  /_/ \_\|_| |_| |_| \_\  \____||_| |_|  \___/  |_| \_|
    [white]EXARCHON COGNITIVE OS LAYER // ALPHA v0.9.5[/white]
    [/bold cyan]
    """
    console.print(logo)
    console.print(Panel(f"● [bold white]ACL Layer:[/] {status_acl}\n● [bold white]Kernel:[/] READY\n● [bold white]Reflex System:[/] ONLINE\n● [bold white]Sensory Loop:[/] ACTIVE", title="[bold white]System Status[/]", border_style="dim", expand=False))
    
    sensory_task = asyncio.create_task(sensory_loop(global_acl, console))
    repl_task = asyncio.create_task(interactive_repl(console))
    await asyncio.gather(sensory_task, repl_task)

# ==========================================
# 5. ДИСПЕТЧЕР ЗАПУСКУ 
# ==========================================
if __name__ == "__main__":
    if "PORT" in os.environ or "RAILWAY_ENVIRONMENT" in os.environ:
        port = int(os.environ.get("PORT", 8000))
        print(f"[BOOT] Хмарне середовище виявлено. Запуск Uvicorn на порту {port}...")
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        try:
            asyncio.run(local_cli_main())
        except KeyboardInterrupt:
            pass