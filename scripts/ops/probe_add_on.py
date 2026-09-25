"""배포 뒤 불타기(T308)가 서버 선언에 켜졌는가 — api 컨테이너 안에서 선언을 읽어 찍는다.

    bash scripts/ops/remote.sh scripts/ops/probe_add_on.py

값만 찍는다(시크릿 없음). 왜: 1.18.0 은 실계좌 다리 `private_strategy` ·
`private_strategy` 에 `add_on` 을 켰고 선언 버전은 0.1.0 그대로다(펀드 다리 귀속 키).
서버가 새 설정을 읽었는지 · 귀속 키가 그대로인지 · 반익 없는 판인지를 한 번에 본다.
"""

from updown.analysis.playbook.select import load_playbooks

WANT = ("private_strategy", "private_strategy", "private_strategy")
books = {book.playbook_id: book for book in load_playbooks()}
for name in WANT:
    book = books.get(name)
    if book is None:
        print(f"{name}: 선언 없음 🔴")
        continue
    rule = book.add_on
    print(
        f"{book.attribution}: add_on="
        + ("없음" if rule is None else f"문턱 {rule.confirm_pct} · 크기 {rule.frac}")
        + f" · full_ride={book.full_ride} · timeframe={book.timeframe.value}"
    )
