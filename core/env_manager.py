import subprocess
import shutil
import os
import time

class EnvManager:
    @staticmethod
    def check_ollama():
        print(" [ENV] Запуск багаторівневої діагностики системи...")
        
        # Перевірка 1: Системний шлях (PATH) або Фізичний шлях
        print("   ├─ [1/3] Перевірка системних шляхів (Binary)...")
        
        ollama_path = shutil.which("ollama")
        
        # ХИТРІСТЬ: Якщо в PATH немає, шукаємо фізично там, куди її ставить WinGet
        if not ollama_path:
            local_app_data = os.environ.get('LOCALAPPDATA', '')
            fallback_path = os.path.join(local_app_data, 'Programs', 'Ollama', 'ollama.exe')
            if os.path.exists(fallback_path):
                ollama_path = fallback_path
                # Додаємо шлях у пам'ять нашого скрипта, щоб наступні команди працювали
                os.environ["PATH"] += os.pathsep + os.path.dirname(fallback_path)

        if not ollama_path:
            return False, "Ollama not installed"

        # Перевірка 2: Відгук CLI (перевірка цілісності)
        print("   ├─ [2/3] Перевірка відгуку ядра (CLI Ping)...")
        try:
            cli_check = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=3)
            if cli_check.returncode != 0:
                print("   ❌ CLI не відповідає або пошкоджений.")
                return False, "CLI error"
        except Exception:
            return False, "CLI error"

        # Перевірка 3: Фоновий сервер та база моделей
        print("   ├─ [3/3] Перевірка сервера та когнітивних баз (Daemon)...")
        try:
            result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=8)
            
            if "could not connect" in result.stderr.lower() or "error" in result.stderr.lower():
                print("\n⚠️ [ENV] Сервер Ollama встановлений, але вимкнений!")
                print("💡 Знайдіть 'Ollama' в меню Пуск Windows і запустіть її, потім повертайтеся.")
                return False, "Server down"

            if "llama3" in result.stdout:
                print(" [ENV] Діагностика успішна. Усі системи в нормі.")
                return True, "Ready"
            else:
                return False, "Model missing"
                
        except subprocess.TimeoutExpired:
            print("    Сервер Ollama завис (Timeout).")
            return False, "Server down"
        except Exception:
            return False, "Server down"

    @staticmethod
    def install_ollama():
        print("\n [INSTALLER] Ollama не знайдена в системі.")
        print("Бажаєте встановити її автоматично через WinGet? [y/N]")
        choice = input("> ").lower()
        if choice == 'y':
            print(" Починаю встановлення...")
            try:
                subprocess.run(["winget", "install", "-e", "--id", "Ollama.Ollama"], check=True)
                print(" [INSTALLER] Ollama встановлена! ПЕРЕЗАПУСТИ ТЕРМІНАЛ, щоб система її побачила.")
                return True
            except Exception as e:
                print(f" [INSTALLER] Не вдалося встановити: {e}")
                return False
        else:
            print(" Встановлення скасовано користувачем.")
            return False

    @staticmethod
    def provision_llama():
        print("\n🤖 [MODEL] Завантажити модель Llama 3 (4.7 GB)? [y/N]")
        choice = input("> ").lower()
        if choice == 'y':
            print(" Починаю завантаження 'llama3'... Це займе час.")
            try:
                subprocess.run(["ollama", "pull", "llama3"], check=True)
                return True
            except Exception:
                print(" [MODEL] Помилка під час завантаження.")
        return False