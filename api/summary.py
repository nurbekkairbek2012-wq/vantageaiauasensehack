"""Бессерверная функция `/api/summary` — живые агрегаты для лендинга.

Отдаёт только числа, без координат: житель видит зону, служба — точку.
"""

import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path

DATA = Path(__file__).with_name("candidates.json")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        items = json.loads(DATA.read_text(encoding="utf-8"))
        dumps = [c for c in items if c.get("visual_check") == "landfill"]
        body = json.dumps({
            "objects": len(items),
            "confirmed": len(dumps),
            "damage_p50_kzt": sum(c.get("damage_p50", 0) for c in dumps),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
