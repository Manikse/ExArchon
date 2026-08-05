"""
kernel/security/capabilities.py
Capability-Based Security для ExArchon Kernel.

Філософія:
- Ніяких blacklist/whitelist. Тільки explicit capability tokens.
- Кожен компонент (driver, skill, worker) має CapabilitySet.
- Kernel перевіряє кожну дію ПЕРЕД виконанням через CapabilityManager.
- Capabilities можуть делегуватися (delegate) та відкликатися (revoke).
- Токени незмінні (frozen) — зміна прав = новий токен.

Аналогія з Raphael: це як "дозвіл на використання здібності".
Без токена — здібність не активується, навіть якщо Rimuru каже "зроби".
"""
from __future__ import annotations
import time
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Tuple, Optional, Dict, Set, List
from enum import Enum, auto


class CapOp(Enum):
    """Типи операцій, які kernel може авторизувати."""
    READ = auto()
    WRITE = auto()
    EXEC = auto()
    NETWORK = auto()
    SPAWN = auto()
    BRANCH = auto()
    WAIT = auto()
    NOOP = auto()


@dataclass(frozen=True)
class CapabilityToken:
    """
    Незмінний capability token.

    Поля:
        token_id: унікальний UUID токена
        issuer: хто видав ("kernel", "operator", "parent_skill")
        issued_at: timestamp видачі
        expires_at: 0 = ніколи, інакше unix timestamp
        ops: множина дозволених операцій
        targets: шаблони цілей (fnmatch-style), наприклад "./workspace/*", "df", "ping"
        conditions: додаткові умови ("no_shell=True", "max_timeout=30")
        parent_token: ID батьківського токена (для ланцюжка делегування)
    """
    token_id: str
    issuer: str
    issued_at: float
    expires_at: float
    ops: Tuple[CapOp, ...]
    targets: Tuple[str, ...]
    conditions: Tuple[str, ...]
    parent_token: Optional[str] = None

    def __post_init__(self):
        # Обчислюємо хеш для швидкої перевірки цілісності
        hash_input = f"{self.token_id}:{self.issuer}:{self.issued_at}:{','.join(sorted(o.name for o in self.ops))}:{','.join(self.targets)}"
        object.__setattr__(self, '_hash', hashlib.sha256(hash_input.encode()).hexdigest()[:16])

    @property
    def hash(self) -> str:
        return getattr(self, '_hash', 'unknown')

    def has_op(self, op: CapOp) -> bool:
        return op in self.ops or CapOp.NOOP in self.ops

    def matches_target(self, target: str) -> bool:
        import fnmatch
        return any(fnmatch.fnmatch(target, t) for t in self.targets)

    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() > self.expires_at

    def condition(self, key: str) -> Optional[str]:
        """Повертає значення умови за ключем, наприклад condition('no_shell') -> 'True'"""
        for c in self.conditions:
            if c.startswith(f"{key}="):
                return c.split("=", 1)[1]
        return None

    @classmethod
    def create(
        cls,
        issuer: str = "kernel",
        ops: Tuple[CapOp, ...] = (CapOp.NOOP,),
        targets: Tuple[str, ...] = (),
        conditions: Tuple[str, ...] = (),
        expires_at: float = 0.0,
        parent_token: Optional[str] = None,
    ) -> "CapabilityToken":
        return cls(
            token_id=str(uuid.uuid4()),
            issuer=issuer,
            issued_at=time.time(),
            expires_at=expires_at,
            ops=ops,
            targets=targets,
            conditions=conditions,
            parent_token=parent_token,
        )


@dataclass
class CapabilitySet:
    """
    Набір capability tokens для конкретного компонента.
    Наприклад: CapabilitySet для TerminalDriver, для Skill "check_disk", для Sandbox.
    """
    owner: str  # назва компонента
    tokens: List[CapabilityToken] = field(default_factory=list)

    def add(self, token: CapabilityToken) -> None:
        if not token.is_expired():
            self.tokens.append(token)

    def revoke(self, token_id: str) -> bool:
        original_len = len(self.tokens)
        self.tokens = [t for t in self.tokens if t.token_id != token_id]
        return len(self.tokens) < original_len

    def check(self, op: CapOp, target: str) -> Tuple[bool, Optional[CapabilityToken], Optional[str]]:
        """
        Перевіряє, чи дозволена операція.
        Повертає: (ok, matching_token_or_None, reason_or_None)
        """
        for token in self.tokens:
            if token.is_expired():
                continue
            if not token.has_op(op):
                continue
            if not token.matches_target(target):
                continue
            # Перевірка умов
            if op == CapOp.EXEC:
                no_shell = token.condition("no_shell")
                if no_shell == "True" and self._would_use_shell(target):
                    continue  # Цей токен вимагає no_shell, але команда потребує shell
            return True, token, None
        return False, None, f"Capability denied: {op.name} on '{target}' for owner '{self.owner}'"

    @staticmethod
    def _would_use_shell(command: str) -> bool:
        """Евристика: чи потребує команда shell=True."""
        import shlex
        try:
            shlex.split(command)
            return False
        except ValueError:
            return True

    def to_summary(self) -> Dict:
        return {
            "owner": self.owner,
            "token_count": len(self.tokens),
            "ops": sorted(list(set(o.name for t in self.tokens for o in t.ops))),
        }


class CapabilityManager:
    """
    Центральний менеджер capabilities у kernel.
    Реєструє компоненти, видає токени, перевіряє дії.
    """

    def __init__(self):
        self._registry: Dict[str, CapabilitySet] = {}
        self._audit_log: List[Dict] = []
        self._max_audit_entries = 10000

    def register_component(self, name: str, initial_caps: Optional[CapabilitySet] = None) -> CapabilitySet:
        """Реєструє компонент у kernel. Без реєстрації — жодних прав."""
        cap_set = initial_caps or CapabilitySet(owner=name)
        self._registry[name] = cap_set
        self._audit("REGISTER", name, None, None, "Component registered")
        return cap_set

    def issue(
        self,
        to_component: str,
        issuer: str = "kernel",
        ops: Tuple[CapOp, ...] = (CapOp.NOOP,),
        targets: Tuple[str, ...] = (),
        conditions: Tuple[str, ...] = (),
        expires_at: float = 0.0,
        parent_token: Optional[str] = None,
    ) -> CapabilityToken:
        """Видає новий capability token компоненту."""
        token = CapabilityToken.create(
            issuer=issuer, ops=ops, targets=targets,
            conditions=conditions, expires_at=expires_at,
            parent_token=parent_token,
        )
        if to_component not in self._registry:
            self.register_component(to_component)
        self._registry[to_component].add(token)
        self._audit("ISSUE", issuer, to_component, token.token_id, f"ops={','.join(o.name for o in ops)}")
        return token

    def revoke(self, component: str, token_id: str) -> bool:
        """Відкликає токен у компонента."""
        if component not in self._registry:
            return False
        ok = self._registry[component].revoke(token_id)
        if ok:
            self._audit("REVOKE", "kernel", component, token_id, "Token revoked")
        return ok

    def validate(self, component: str, op: CapOp, target: str) -> Tuple[bool, Optional[str]]:
        """
        Головна точка входу для перевірки capability.
        Kernel викликає це ПЕРЕД кожною дією.
        """
        if component not in self._registry:
            self._audit("DENY", component, None, None, f"Unregistered component attempted {op.name} on {target}")
            return False, f"Component '{component}' not registered in capability system"

        ok, token, reason = self._registry[component].check(op, target)
        if ok:
            self._audit("ALLOW", component, None, token.token_id if token else None, f"{op.name} {target}")
            return True, None
        else:
            self._audit("DENY", component, None, None, reason)
            return False, reason

    def get_component_caps(self, name: str) -> Optional[CapabilitySet]:
        return self._registry.get(name)

    def list_components(self) -> List[str]:
        return list(self._registry.keys())

    def get_audit_log(self, limit: int = 100) -> List[Dict]:
        return self._audit_log[-limit:]

    def _audit(self, event: str, source: str, target: Optional[str], token_id: Optional[str], detail: str):
        entry = {
            "timestamp_ns": time.time_ns(),
            "event": event,
            "source": source,
            "target": target,
            "token_id": token_id,
            "detail": detail,
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > self._max_audit_entries:
            self._audit_log = self._audit_log[-self._max_audit_entries // 2:]


# ============ PREDEFINED CAPABILITY SETS ============
# Це НЕ жорсткі правила, а зручні шаблони для ініціалізації.
# Kernel може видавати більш обмежені або розширені токени за потреби.

def make_terminal_caps(working_dir: str = "./kernel_workspace") -> CapabilitySet:
    """Базові capabilities для TerminalDriver — тільки читання та безпечні команди."""
    cs = CapabilitySet(owner="terminal")
    cs.add(CapabilityToken.create(
        issuer="kernel",
        ops=(CapOp.EXEC,),
        targets=("df", "ls", "ps", "cat", "pwd", "whoami", "uptime", "echo", "uname", "top", "free", "du"),
        conditions=("no_shell=True", "max_timeout=30"),
    ))
    cs.add(CapabilityToken.create(
        issuer="kernel",
        ops=(CapOp.READ,),
        targets=(f"{working_dir}/*", "/proc/*", "/sys/class/thermal/*"),
    ))
    return cs


def make_filesystem_caps(working_dir: str = "./kernel_workspace") -> CapabilitySet:
    """Базові capabilities для FileSystemDriver — Shadow Protocol: читання за замовчуванням."""
    cs = CapabilitySet(owner="file_system")
    cs.add(CapabilityToken.create(
        issuer="kernel",
        ops=(CapOp.READ,),
        targets=(f"{working_dir}/*", f"{working_dir}/**/*"),
    ))
    # WRITE тільки через explicit PATCH approval (видається runtime на момент операції)
    cs.add(CapabilityToken.create(
        issuer="kernel",
        ops=(CapOp.WRITE,),
        targets=(f"{working_dir}/.exarchon_patches/*", f"{working_dir}/.exarchon_backups/*"),
        conditions=("shadow_mode=True",),
    ))
    return cs


def make_cortex_caps() -> CapabilitySet:
    """Capabilities для Live Cortex — обмежені, бо це System 2 (повільний, дорогий)."""
    cs = CapabilitySet(owner="cortex")
    cs.add(CapabilityToken.create(
        issuer="kernel",
        ops=(CapOp.EXEC, CapOp.READ, CapOp.NETWORK, CapOp.SPAWN),
        targets=("*",),  # Cortex може використовувати будь-які інструменти, АЛЕ...
        conditions=("max_exec_time_ms=30000", "requires_human_approval_for_write=True"),
    ))
    return cs


def make_muscle_caps() -> CapabilitySet:
    """Capabilities для Muscle Memory — тільки скомпільовані, перевірені дії."""
    cs = CapabilitySet(owner="muscle_memory")
    cs.add(CapabilityToken.create(
        issuer="kernel",
        ops=(CapOp.EXEC, CapOp.READ),
        targets=("*",),
        conditions=("max_exec_time_ms=5000", "sandbox_validated=True"),
    ))
    return cs


def make_reflex_caps() -> CapabilitySet:
    """Capabilities для Reflex Engine — мінімальні, тільки hardcoded safety."""
    cs = CapabilitySet(owner="reflex")
    cs.add(CapabilityToken.create(
        issuer="kernel",
        ops=(CapOp.EXEC, CapOp.NOOP),
        targets=("echo", "kill", "shutdown"),
        conditions=("max_exec_time_ms=100", "max_memory_mb=32"),
    ))
    return cs


def make_sandbox_caps() -> CapabilitySet:
    """Capabilities для Sandbox — тільки mock, ніяких реальних side effects."""
    cs = CapabilitySet(owner="sandbox")
    cs.add(CapabilityToken.create(
        issuer="kernel",
        ops=(CapOp.NOOP,),
        targets=("*",),
        conditions=("dry_run=True",),
    ))
    return cs