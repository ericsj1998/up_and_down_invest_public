"""이의제기 — 손으로 고친 그림을 **정답이 아니라 버그 리포트로** 기록한다 (타입 B).

## 왜 「정답지」가 아니라 「이의제기」인가

사람이 화면에서 추세선을 끌어 고치면, 그 선은 **어떤 규칙도 만들지 않은 선**이다. 다음
봉이 오면 아무도 그 선을 다시 못 긋는다. 그것을 정답으로 놓고 알고리즘을 채점하면:

    1. 코드로 환원되지 않는다 → 절대 규칙 #11 의 기준을 통과 못 한다
    2. 그 라벨에 파라미터를 맞추게 된다 → §5.6.2 자동조율
    3. 🔴 결과를 알고 그은 선은 항상 잘 맞는다 → 사후 편향. 의지가 아니라 인지의
       문제라 눈으로 못 이긴다

그래서 손그림을 **좌표가 붙은 이의제기**로 저장하고, 시스템이 되묻는다 —
*이 그림을 만들어내는 파라미터가 존재하는가?* (`reproduce.py`)

    존재한다     → 파라미터 문제다. 축 후보로 올려 out-of-sample 이 판정한다
                   (규칙 #12 그대로. 눈은 후보를 **제안**했을 뿐 확정하지 않았다)
    존재 안 한다 → 🔴 더 값진 발견이다. 정의 자체가 사람이 생각하는 규칙과 다르다

## ⛔ 이 로그는 채점 분모가 될 수 없다

"알고리즘 정확도 = 이의제기 안 받은 비율" 같은 지표를 만드는 순간 눈이 정답이 된다.
그런 집계 함수를 **여기에 만들지 않았고**, 만들면 안 된다.

## 오염 표시

이의제기는 미래를 모르는 상태에서 받는 것이 원칙이다. 결과를 먼저 본 뒤 낸 것은
`saw_outcome=True` 로 남는다 — 막지 않고 **표시한다**. 막으면 우회하게 되고, 우회한
기록은 남지 않는다. 남은 기록이 거짓인 것보다 낫다.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from updown.common.paths import under

if TYPE_CHECKING:
    from collections.abc import Mapping

STORE = under("objections")
"""이의제기가 쌓이는 곳.

⚠️ `run_matrix.py` 의 `PURGE_DIRS` 에 **없다** — 매트릭스 재실행이 사람의 검토 기록을
지우면 안 된다.
"""

Kind = Literal["added", "removed", "moved"]
"""무엇을 했는가.

    added   알고리즘이 못 찾은 것을 사람이 그렸다  → 탐지가 너무 빡빡한가
    removed 알고리즘이 찾은 것을 사람이 지웠다     → 탐지가 너무 헐거운가 (과탐지)
    moved   위치·기울기를 사람이 고쳤다            → 정의가 어긋나는가
"""


@dataclass(frozen=True, slots=True)
class Objection:
    """이의제기 한 건.

    Attributes:
        symbol: 종목 코드.
        as_of: 이 화면의 시점. 🔴 **시드가 아니라 시점**을 저장한다 — 시드는 시간축
            조합·봉 수까지 같아야 같은 시점을 주므로 단독으로는 재현 키가 못 된다.
        timeframe: 어느 시간축 차트에서 냈는가.
        flag: 어느 플래그에 대한 이의인가.
        kind: 추가·삭제·이동.
        shape: 사람이 그린(또는 지운) 것의 좌표. 자유 형식이며 플래그마다 다르다.
        original: 알고리즘이 낸 원본. `added` 면 None.
        comment: 사람이 남긴 말. **왜 틀렸다고 보는가**가 여기 담긴다.
        overrides: 그릴 때 걸려 있던 드래프트 파라미터. 없으면 표준값이었다.
        bars: 그때 보던 **창 크기**(봉 수). 🔴 좌표를 되살리려면 반드시 있어야 한다 —
            아래 주석 참조. 0 이면 창 크기를 안 남긴 옛 기록이다.
        saw_outcome: 결과(손익·이후 전개)를 **먼저 보고** 냈는가. 사후 편향 표시.
        created_at: 기록 시각.

    Note:
        🔴 **`bars` 가 없으면 이 기록은 자기 자신을 설명하지 못한다.**

        `shape` 의 좌표는 `index`(봉 번호)인데 그 번호는 **창 안에서 몇 번째**라는 뜻
        이다. 같은 시점을 300봉으로 다시 열면 봉 199 는 전혀 다른 봉을 가리킨다. 창
        크기를 안 남기면 나중에 그 그림을 다시 그릴 때 조용히 다른 자리를 짚는다.

        그래서 좌표에 **`ts`(절대 시각)도 함께** 싣는다 (`ObjectionPanel`). 둘 중
        하나만 있어도 되살아나게 하려는 것이 아니라, `ts` 가 있으면 창이 달라져도
        정확히 같은 봉을 찾을 수 있기 때문이다.
    """

    symbol: str
    as_of: datetime
    timeframe: str
    flag: str
    kind: Kind
    shape: "Mapping[str, Any]"
    original: "Mapping[str, Any] | None" = None
    comment: str = ""
    overrides: "Mapping[str, str]" = field(default_factory=lambda: dict[str, str]())
    bars: int = 0
    saw_outcome: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def id(self) -> str:
        """내용에서 나오는 식별자.

        Returns:
            12자 해시.

        Note:
            같은 이의를 두 번 제출해도 한 건이다 (더블 클릭·새로고침). 시각은 빼고
            해싱한다 — 넣으면 같은 내용이 두 건이 되어 "몇 번 지적했나"가 부풀려진다.
        """
        payload = json.dumps(
            {
                "symbol": self.symbol,
                "as_of": self.as_of.isoformat(),
                "timeframe": self.timeframe,
                "flag": self.flag,
                "kind": self.kind,
                "shape": dict(self.shape),
                "original": None if self.original is None else dict(self.original),
                # 🔴 창 크기가 다르면 **같은 좌표가 다른 자리**다 — 같은 지적이 아니다.
                "bars": self.bars,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        """JSON 으로.

        Returns:
            직렬화용 dict.
        """
        return {
            "id": self.id,
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "timeframe": self.timeframe,
            "flag": self.flag,
            "kind": self.kind,
            "shape": dict(self.shape),
            "original": None if self.original is None else dict(self.original),
            "comment": self.comment,
            "overrides": dict(self.overrides),
            "bars": self.bars,
            "saw_outcome": self.saw_outcome,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: "Mapping[str, Any]") -> "Objection":
        """JSON 에서.

        Args:
            raw: 저장된 dict.

        Returns:
            이의제기.
        """
        return cls(
            symbol=str(raw["symbol"]),
            as_of=datetime.fromisoformat(str(raw["as_of"])),
            timeframe=str(raw["timeframe"]),
            flag=str(raw["flag"]),
            kind=raw["kind"],
            shape=raw.get("shape") or {},
            original=raw.get("original"),
            comment=str(raw.get("comment") or ""),
            overrides=raw.get("overrides") or {},
            # 0 = 창 크기를 안 남긴 옛 기록. 화면이 그 사실을 말한다 — 200 으로 채워
            # 넣으면 어긋난 좌표를 맞는 것처럼 보여 준다 (절대 규칙 #8).
            bars=int(raw.get("bars") or 0),
            saw_outcome=bool(raw.get("saw_outcome")),
            created_at=datetime.fromisoformat(str(raw["created_at"])),
        )


def save(item: Objection, root: Path | None = None) -> Path:
    """이의제기 하나를 남긴다.

    Args:
        item: 이의제기.
        root: 저장 위치. None 이면 `STORE`.

    Returns:
        쓴 파일 경로.

    Note:
        같은 id 면 덮어쓴다 — 내용 해시라 덮어써도 같은 내용이다. 다만 `saw_outcome`
        과 `comment` 는 해시에 안 들어가므로 **나중 것이 이긴다**. 결과를 본 뒤 같은
        이의를 다시 내면 오염 표시가 켜지는 쪽으로 남는 것이 맞다.
    """
    target = (root or STORE) / f"{item.id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    # 🔴 끝에 줄바꿈을 넣는다. 이 폴더는 **버전 관리 대상**이고(README 참조)
    #    `end-of-file-fixer` 훅이 없는 파일을 매번 고쳐 커밋을 막는다.
    target.write_text(
        json.dumps(item.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def load_all(root: Path | None = None) -> list[Objection]:
    """쌓인 이의제기 전부를 최신순으로.

    Args:
        root: 저장 위치.

    Returns:
        이의제기들. 폴더가 없으면 빈 목록.

    Note:
        읽을 수 없는 파일은 **건너뛰지 않고 예외**로 올린다. 조용히 빼면 "지적한 적
        없다"가 되고, 그것은 이 로그가 있는 이유를 배신한다 (절대 규칙 #8).
    """
    folder = root or STORE
    if not folder.exists():
        return []
    items = [
        Objection.from_dict(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(folder.glob("*.json"))
    ]
    return sorted(items, key=lambda item: item.created_at, reverse=True)


REVEALS = under("reveals")
"""결과를 **연 사실**이 남는 곳.

## 🔴 이 저장소가 없으면 오염 표시가 영원히 안 켜진다

처음엔 `saw_outcome_at` 을 이의제기 목록에서 찾게 만들었다. 그런데 그 플래그를 켜는
유일한 경로가 "이미 켜진 이의제기가 있는가"였다 — **순환이라 첫 불이 안 붙는다.**
실제로 결과를 열고 이의제기를 내 봤더니 `saw_outcome=False` 로 남았다.

공개는 이의제기와 **다른 사건**이므로 다른 기록이 필요하다. 여는 순간 여기 남고,
이후 그 시점에 낸 이의제기가 그것을 읽는다.
"""


def _reveal_key(symbol: str, as_of: datetime) -> str:
    """공개 기록 파일 이름.

    Args:
        symbol: 종목.
        as_of: 시점.

    Returns:
        파일 이름 (확장자 제외).
    """
    return sha1(f"{symbol}|{as_of.isoformat()}".encode(), usedforsecurity=False).hexdigest()[:12]


def mark_seen(symbol: str, as_of: datetime, root: Path | None = None) -> bool:
    """이 시점의 결과를 열었다고 남긴다.

    Args:
        symbol: 종목.
        as_of: 시점.
        root: 저장 위치. None 이면 `REVEALS`.

    Returns:
        **이번 호출 전에** 이미 본 적이 있었으면 True.

    Note:
        되돌리는 함수를 만들지 않았다. 본 것을 안 본 것으로 만들 수 없기 때문이고,
        그 비대칭이 이 기록의 요점이다.
    """
    folder = root or REVEALS
    target = folder / f"{_reveal_key(symbol, as_of)}.json"
    already = target.exists()
    folder.mkdir(parents=True, exist_ok=True)
    if not already:
        target.write_text(
            json.dumps(
                {
                    "symbol": symbol,
                    "as_of": as_of.isoformat(),
                    "seen_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
            # 여기도 버전 관리 대상이다 (`save` 의 주석 참조).
            + "\n",
            encoding="utf-8",
        )
    return already


def saw_outcome_at(symbol: str, as_of: datetime, root: Path | None = None) -> bool:
    """이 시점에서 이미 결과를 본 적이 있는가.

    Args:
        symbol: 종목.
        as_of: 시점.
        root: 공개 기록 위치. None 이면 `REVEALS`.

    Returns:
        본 적이 있으면 True.

    Note:
        이의제기 목록이 아니라 **공개 기록**을 본다 (위 `REVEALS` 참조).
    """
    return ((root or REVEALS) / f"{_reveal_key(symbol, as_of)}.json").exists()
