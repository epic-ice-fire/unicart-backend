import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-unicart-tests")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("FLW_SECRET_KEY", "test-secret")
os.environ.setdefault("FLW_SECRET_HASH", "test-webhook-hash")

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import User
from app.routers import auth
from app.schemas import UniversitySelectRequest


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_new_account_can_choose_a_university_without_school_email(db_session):
    user = User(
        email="new-user@example.com",
        password_hash="x",
        university_code=None,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    assert user.university_code is None

    response = await auth.select_university(
        payload=UniversitySelectRequest(university_code="augustine"),
        user=user,
        db=db_session,
    )

    await db_session.refresh(user)
    assert response.university_code == "augustine"
    assert user.university_code == "augustine"
    assert user.student_pau_email is None
    assert user.is_student_verified is False


@pytest.mark.asyncio
async def test_user_can_switch_campus_without_email_verification(db_session):
    user = User(
        email="switching-user@example.com",
        password_hash="x",
        university_code=None,
    )
    db_session.add(user)
    await db_session.commit()

    for campus in ("pau", "babcock", "augustine"):
        response = await auth.select_university(
            payload=UniversitySelectRequest(university_code=campus),
            user=user,
            db=db_session,
        )
        assert response.university_code == campus
        await db_session.refresh(user)
        assert user.university_code == campus


def test_university_selection_accepts_only_supported_campuses():
    for campus in ("pau", "augustine", "babcock"):
        assert UniversitySelectRequest(university_code=campus).university_code == campus

    with pytest.raises(ValidationError):
        UniversitySelectRequest(university_code="unknown-school")


def test_pau_email_verification_endpoints_are_removed():
    paths = {getattr(route, "path", "") for route in auth.router.routes}
    assert "/auth/pau/request" not in paths
    assert "/auth/pau/verify" not in paths
    assert "/auth/university/select" in paths


@pytest.mark.asyncio
async def test_campus_admin_cannot_switch_to_another_campus(db_session):
    admin = User(
        email="augustine-admin@example.com",
        password_hash="x",
        is_admin=True,
        university_code="augustine",
    )
    db_session.add(admin)
    await db_session.commit()

    with pytest.raises(Exception) as exc:
        await auth.select_university(
            payload=UniversitySelectRequest(university_code="pau"),
            user=admin,
            db=db_session,
        )

    assert getattr(exc.value, "status_code", None) == 403
    await db_session.refresh(admin)
    assert admin.university_code == "augustine"
