import re
from kernel.a2a.protocol import A2AController
from kernel.acl.provider import ACLController
from kernel.unms.memory import UNMSController
from kernel.runtime.planner import CognitivePlanner

class KernelRuntime:
    def __init__(self, acl, memory, drivers: dict):
        self.acl = acl
        self.memory = memory
        self.drivers = drivers
        self.planner = CognitivePlanner(acl)
        self.a2a = A2AController(acl)
        
        # ЗАПОБІЖНИК: Максимальна кількість спроб самовиправлення за один запит
        self.max_recoveries = 3 

    def _is_error(self, result: str) -> bool:
        res = str(result).lower()
        if res.startswith("failed:") or res.startswith("critical failure:"):
            return True
            
        error_signatures = [
            "traceback (most recent",
            "syntaxerror:",
            "nameerror:",
            "typeerror:",
            "indentationerror:",
            "command not found",
            "no such file or directory",
            "permission denied",
            "fatal error:"
        ]
        return any(sig in res for sig in error_signatures)

    async def step(self, user_input: str, session_id: str = "default") -> str:
        # 1. Витягуємо контекст конкретної сесії
        conversation_context = self.memory.get_context_string(session_id)
        
        # DEBUG: Перевіряємо в консолі сервера, що саме ядро "бачить" перед кроком
        print(f"\n[DEBUG UNMS] Session: {session_id} | Context Loaded: {len(conversation_context)} chars")
        
        enriched_input = f"CONTEXT:\n{conversation_context}\n\nCURRENT REQUEST:\n{user_input}"
        
        # 2. Планування
        tasks = await self.planner.create_plan(enriched_input)
        
        if not tasks:
            return "Kernel Error: Cognitive Planner failed to generate an execution sequence."

        results_summary = []
        step_outputs = {}
        current_recoveries = 0
        
        i = 0
        while i < len(tasks):
            task = tasks[i]
            
            # --- DATA PIPING MAGIC ---
            matches = re.findall(r"\{\{STEP_(\d+)_RESULT\}\}", task.description)
            for match in matches:
                step_num = int(match)
                if step_num in step_outputs:
                    task.description = task.description.replace(f"{{{{STEP_{step_num}_RESULT}}}}", step_outputs[step_num])

            print(f"\n[ExArchon EXEC] Step {task.step_id}: {task.description[:100]}... [Tool: {task.tool}]")
            
            try:
                if task.tool == "web_search" and "web_search" in self.drivers:
                    task.result = await self.drivers["web_search"].execute(task.description) 
                
                elif task.tool == "terminal" and "terminal" in self.drivers:
                    task.result = self.drivers["terminal"].execute(task.description)
                
                elif task.tool == "file_system" and "file_system" in self.drivers:
                    task.result = self.drivers["file_system"].execute(task.description)
                
                elif task.tool == "spawn_agent" and hasattr(self, 'a2a'):
                    parts = task.description.split("|")
                    if len(parts) >= 3:
                        name, role, prompt = [p.strip() for p in parts[:3]]
                        task.result = await self.a2a.delegate(name, role, prompt)
                    else:
                        task.result = "FAILED: Invalid format."
                else:
                    task.result = await self.acl.execute(task.description)
                
                task.status = "COMPLETED"
                
            except Exception as e:
                task.result = f"CRITICAL FAILURE: {str(e)}"
                task.status = "FAILED"
                
            step_outputs[task.step_id] = str(task.result)
            results_summary.append(f"Step {task.step_id} ({task.tool}) | Status: {task.status}\nData/Output: {str(task.result)[:200]}...")

            # --- REFLECTION LOOP ---
            if self._is_error(task.result):
                if current_recoveries >= self.max_recoveries:
                    print(f"\n[ExArchon REFLECTION] CRITICAL: Max retries reached!")
                    results_summary.append(f"\n[SYSTEM HALT] Max retries exceeded.")
                    break
                    
                current_recoveries += 1
                print(f"\n[ExArchon REFLECTION] Error in Step {task.step_id}! Recovery initiated...")
                
                recovery_prompt = f"CRITICAL ALERT: Previous step failed. Error: {task.result}. Analyze and return JSON RECOVERY PLAN."
                recovery_tasks = await self.planner.create_plan(recovery_prompt)
                
                if recovery_tasks:
                    tasks.extend(recovery_tasks)
                else:
                    break

            i += 1

        compiled_results = "\n---\n".join(results_summary)
        
        # 3. ФОРМУВАННЯ ФІНАЛЬНОЇ ВІДПОВІДІ (Оновлений промпт)
        final_prompt = f"""
        YOU ARE EXARCHON. OPERATING AS THE HEADLESS CORE.
        
        [UNMS CONTEXT]
        {conversation_context}
        
        [EXECUTION LOGS]
        {compiled_results}
        
        [FOUNDER INPUT]
        {user_input}
        
        INSTRUCTIONS:
        1. Prioritize information from UNMS CONTEXT to answer the Founder.
        2. If the answer is in the context (e.g., what the Founder drank), state it directly.
        3. Acknowledge execution logs ONLY if they provided new data or if a CRITICAL failure prevents the task.
        4. Maintain a visionary, efficient tone. Avoid over-explaining minor technical logs.
        """
        
        final_response = await self.acl.execute(final_prompt)
        
        # 4. Записуємо взаємодію в пам'ять сесії
        self.memory.add_interaction(session_id, user_input, final_response)
        
        return final_response