"""Собрать имена упавших тестов в одну строку для имени артефакта.

Логи прогонов CI закрыты без доступа к репозиторию, а имена артефактов
видны публично. Это единственный канал, по которому падение доезжает до
того, кто чинит.

Отдельным скриптом, а не однострочником в YAML: кавычки и переводы строк
внутри шелл-блока уже один раз сломали разбор workflow целиком, и
падение выглядело так, будто сломались тесты.
"""

import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
names = re.findall(r"^FAILED (\S+)", text, flags=re.M)

clean = []
for name in names[:10]:
    short = name.replace("tests/", "").replace(".py", "").replace("::", "-")
    clean.append(re.sub(r"[^A-Za-z0-9_.-]", "_", short))

print("list=" + (".".join(clean) if clean else "neizvestno"))
