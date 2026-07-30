import subprocess
import os
import platform

class TerminalDriver:
    """
    Драйвер терміналу. 
    Адаптується до ОС (Windows/Linux) та надає контекст виконання.
    """
    def __init__(self, working_dir: str = "./kernel_workspace"):
        self.name = "Terminal"
        self.working_dir = os.path.abspath(working_dir)
        self.os_type = platform.system() # 'Windows', 'Linux', 'Darwin'
        
        if not os.path.exists(self.working_dir):
            os.makedirs(self.working_dir)

    def _get_system_context(self) -> str:
        """Повертає короткий опис середовища для Ядра."""
        return f"[SYSTEM INFO: OS={self.os_type}, Shell=Default, CWD={self.working_dir}]"

    def execute(self, command: str) -> str:
        print(f"\n[Driver: Terminal]  Виконую: '{command}'...")
        
        # Визначаємо оболонку залежно від ОС
        use_shell = True
        executable = None
        if self.os_type == "Windows":
            # Використовуємо powershell, якщо команда схожа на PS, інакше cmd
            executable = "powershell.exe" if "get-" in command.lower() else None

        try:
            result = subprocess.run(
                command,
                shell=use_shell,
                executable=executable,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=20,
                encoding='utf-8',
                errors='replace'
            )
            
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            
            # Формуємо відповідь з контекстом системи
            output = []
            if stdout:
                output.append(stdout)
            if stderr:
                output.append(f"STDERR: {stderr}")
                
            final_output = "\n".join(output)
            
            if not final_output:
                return f"{self._get_system_context()} Success: Executed with no output."
                
            # Додаємо контекст ОС до кожної відповіді, щоб Ядро не "забувало" де воно
            return f"{self._get_system_context()}\n{final_output[:2000]}"

        except subprocess.TimeoutExpired:
            return f"{self._get_system_context()} Error: Timeout (20s)."
        except Exception as e:
            return f"{self._get_system_context()} Execution Error: {str(e)}"