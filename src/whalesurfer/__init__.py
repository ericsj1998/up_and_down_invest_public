"""WhaleSurfer — 유명 13F 보고자의 보유 지도 (T442 · 업앤다운의 둘째 제품 후보).

업앤다운 본체(코인 자동매매 · 실계좌 서버)와 **떨어진 패키지**다 (2026-10-09 분리 · T442 §1-1 ②).

- 본체는 이 패키지를 import 하지 않는다 — import-linter 계약(`.importlinter`)이 막는다.
- 이 패키지는 본체에서 `updown.common`(설정 · 로깅 · HTTP 바깥 길 · 캐시)과
  `updown.marketdata`(EDGAR 클라이언트 획득 지점 · 재무 오류형)만 빌려 쓴다.
- 배포 이미지에 안 들어간다(`.dockerignore`) — 실계좌 서버에는 아예 없다(D7).
- 저장소를 떼어 낼 때 옮길 것: `src/whalesurfer/` · `whalesurfer/`(화면 · 스크립트) ·
  `config/whalesurfer/` · `tests/test_whalesurfer.py` · `docs/planning/tasks/T442*` · `T443*`.
"""
