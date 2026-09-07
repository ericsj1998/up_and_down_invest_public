"""프로세스 진입점 — `api`(웹)와 `engine`(감시 루프).

**동일 코드베이스·다른 커맨드**로 실행된다(spec §2.1). 패키지 안에 두었기 때문에
Docker 가 같은 이미지에서 `python -m updown.apps.api` / `python -m updown.apps.engine`
두 프로세스를 띄울 수 있다 (plan D-5).
"""
