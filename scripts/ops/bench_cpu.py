"""CPU 속도 짧은 측정 — 로컬과 서버 비율을 잰다(읽기만 · 쓰기 없음 · 합 약 1 초).

`bash scripts/ops/remote.sh scripts/ops/bench_cpu.py`
"""

import time
from decimal import Decimal


def work() -> None:
    total = Decimal(0)
    for i in range(1, 60_000):
        total += Decimal(i) / Decimal(7)
    values = [float(i) for i in range(40_000)]
    for start in range(0, 200):
        min(values[start : start + 200])


times = []
for _ in range(5):
    began = time.perf_counter()
    work()
    times.append(time.perf_counter() - began)
print("바퀴(초)", [round(t, 3) for t in times], "· 최소", round(min(times), 3))
