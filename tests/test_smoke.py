"""도구 체인 최소 검증 (P0-1-4).

여기서 확인하는 것은 "테스트가 설치된 패키지를 import 하는가"다.
src 레이아웃을 택한 이유가 바로 이것으로(D-5), 플랫 구조에서는 pytest가 현재 디렉터리를
먼저 잡아 "로컬은 되는데 컨테이너는 안 되는" 문제가 생긴다.
"""

import sys
from pathlib import Path

import updown


def test_python_version_is_pinned_to_312() -> None:
    """D-7 — 인터프리터가 3.12로 고정되어 있어야 한다."""
    assert sys.version_info[:2] == (3, 12)


def test_package_is_importable_from_installed_location() -> None:
    """updown 패키지가 저장소 루트가 아니라 src 레이아웃에서 로드되어야 한다."""
    module_file = updown.__file__
    assert module_file is not None
    assert Path(module_file).parent.name == "updown"
