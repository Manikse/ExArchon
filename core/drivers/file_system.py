import os

class FileSystemDriver:
    """
    Драйвер файлової системи. Дозволяє Ядру безпечно створювати та читати файли.
    """
    def __init__(self, working_dir="./kernel_workspace"):
        self.name = "FileSystem"
        self.working_dir = working_dir
        if not os.path.exists(self.working_dir):
            os.makedirs(self.working_dir)

    def execute(self, command: str) -> str:
        print(f"\n[Driver: {self.name}]  Processing file operation...")
        
        # Розбиваємо команду на перший рядок (дія + файл) і решту (вміст)
        lines = command.split('\n', 1)
        first_line = lines[0].strip()
        
        if first_line.startswith("WRITE "):
            filename = first_line.replace("WRITE ", "").strip()
            content = lines[1] if len(lines) > 1 else ""
            
            filepath = os.path.join(self.working_dir, filename)
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                return f"SUCCESS: File '{filename}' successfully written. ({len(content)} bytes)"
            except Exception as e:
                return f"FAILED to write file: {str(e)}"
                
        elif first_line.startswith("READ "):
            filename = first_line.replace("READ ", "").strip()
            filepath = os.path.join(self.working_dir, filename)
            try:
                if not os.path.exists(filepath):
                    return f"FAILED: File '{filename}' does not exist."
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                return f"SUCCESS: File '{filename}' content:\n{content}"
            except Exception as e:
                return f"FAILED to read file: {str(e)}"
        else:
            return "FAILED: Invalid format. Use 'WRITE filename.ext\\n<content>' or 'READ filename.ext'."