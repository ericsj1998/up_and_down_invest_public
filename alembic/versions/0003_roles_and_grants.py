"""DB 롤 분리 + append-only 권한 강제 (P0-4-4b · spec §1.2.1, §8).

**애플리케이션 코드의 선의에 의존하지 않는다.** "수정 API 를 안 만들면 되지 않나"는
누군가 만드는 순간 무너진다. DB 권한으로 막으면 코드가 뭘 하든 막힌다.

append-only 대상 2종:

- `event_logs` — 감사 로그 (spec §8, §1.2.1, 절대 규칙 #8-2)
- `risk_plan_revisions` — **스탑 하향 금지의 증거** (spec §6.9, 절대 규칙 #3).
  직전 값을 고칠 수 있으면 "상향만 했다"는 증명이 무의미해진다. 손절은 무슨 일이
  있어도 지킨다는 최상위 원칙의 물리적 뒷받침이다.

롤을 **NOLOGIN 그룹 롤**로 만드는 이유: 비밀번호를 마이그레이션에 넣지 않기 위해서다
(spec §8). 실제 로그인 계정은 배포 시 만들고 이 롤들의 멤버로 넣는다. 테스트는
superuser 세션에서 `SET ROLE` 로 권한을 검증한다.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

APP_ROLE = "updown_app"
MIGRATOR_ROLE = "updown_migrator"
LOG_RECOVERY_ROLE = "updown_logrecovery"

APPEND_ONLY_TABLES = ("event_logs", "risk_plan_revisions")


def upgrade() -> None:
    """롤 3종을 만들고 권한을 배분한다."""
    for role in (APP_ROLE, MIGRATOR_ROLE, LOG_RECOVERY_ROLE):
        # CREATE ROLE 에는 IF NOT EXISTS 가 없다 → DO 블록으로 멱등하게.
        op.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} NOLOGIN;
                END IF;
            END
            $$
        """)

    # ── 런타임 롤 ────────────────────────────────────────────
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")

    # ── append-only 강제: UPDATE/DELETE 회수 ──────────────────
    for table in APPEND_ONLY_TABLES:
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {APP_ROLE}")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")

    # ── 폴백 복구 배치 전용 롤 ────────────────────────────────
    # 폴백 파일 → event_logs 이관만 한다 (spec §1.2.1, P0-6-3c).
    op.execute(f"GRANT USAGE ON SCHEMA public TO {LOG_RECOVERY_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON event_logs TO {LOG_RECOVERY_ROLE}")

    # ── 마이그레이션 롤 ───────────────────────────────────────
    op.execute(f"GRANT ALL ON SCHEMA public TO {MIGRATOR_ROLE}")
    op.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {MIGRATOR_ROLE}")

    # ── 앞으로 만들어질 테이블에도 같은 규칙 ────────────────────
    # 이것이 없으면 다음 마이그레이션이 만든 테이블에 app 롤이 접근하지 못한다.
    # append-only 테이블을 새로 추가할 때는 이 마이그레이션과 같은 REVOKE 를 함께 넣는다.
    op.execute(f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}
    """)
    op.execute(f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}
    """)


def downgrade() -> None:
    """권한을 회수한다. 롤 자체는 지우지 않는다.

    Note:
        롤을 DROP 하지 않는 이유: 다른 DB 나 로그인 계정이 이 롤의 멤버일 수 있고,
        그 경우 DROP 이 실패하거나 남의 접근을 끊는다. 권한만 되돌리는 것이 안전하다.
    """
    op.execute(f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}
    """)
    op.execute(f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE}
    """)
    for role in (APP_ROLE, LOG_RECOVERY_ROLE, MIGRATOR_ROLE):
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL ON SCHEMA public FROM {role}")
