import asyncio
import re

class A2AController:
    """
    Agent-to-Agent Protocol with built-in Middleware.
    Allows the Kernel (ExArchon) to spawn specialized sub-agents and ensures clean code output.
    """
    def __init__(self, acl):
        self.acl = acl

    async def delegate(self, agent_name: str, role: str, task: str) -> str:
        print(f"\n[A2A Protocol]  Spawning Sub-Agent '{agent_name}'...")
        print(f"[A2A Protocol]  Role: {role}")
        
        system_prompt = f"""
        You are an autonomous sub-agent operating under the ExArchon Kernel.
        Your Name: {agent_name}
        Your Role: {role}
        
        CRITICAL RULES:
        1. Complete the task assigned by the Kernel. Focus ONLY on your specific role.
        2. IF YOUR TASK INVOLVES WRITING CODE OR A SCRIPT: You MUST wrap the entire code inside markdown code blocks.
        3. Do NOT provide human explanations. Return ONLY the code inside the block.
        """
        
        raw_result = await self.acl.execute(task, system_prompt=system_prompt)
        
        # --- MIDDLEWARE: Clean Code Extraction ---
        # Використовуємо chr(96) * 3, щоб інтерфейс чату не зламався при копіюванні
        backticks = chr(96) * 3
        pattern = f'{backticks}(?:[a-zA-Z0-9]+)?\n(.*?){backticks}'
        
        code_blocks = re.findall(pattern, raw_result, re.DOTALL)
        
        if code_blocks:
            # If the Sub-Agent generated a code block, extract ONLY the code
            final_output = code_blocks[0].strip()
            print(f"[A2A Protocol] 🧹 Middleware successfully extracted clean code.")
        else:
            # Fallback for plain text tasks (e.g., summarizing, writing text)
            final_output = raw_result.strip()
            
        return final_output
