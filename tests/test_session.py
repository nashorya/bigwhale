import asyncio
import os
os.environ["SHORE_USER_SALT"] = "test_salt_for_unit_test_only"

import pytest
from datetime import datetime, timedelta
from plugins.shore.core.session import UserSession, SessionManager

@pytest.fixture(autouse=True)
def clear_sessions():
    SessionManager.clear_all()

@pytest.mark.asyncio
async def test_session_created_on_first_get():
    s = await SessionManager.get("uid_aaa")
    assert s.user_id == "uid_aaa"
    assert s.active_persona == "kitty"

@pytest.mark.asyncio
async def test_session_isolation():
    s1 = await SessionManager.get("uid_aaa")
    s2 = await SessionManager.get("uid_bbb")
    s1.active_persona = "makoto"
    assert s2.active_persona == "kitty"

@pytest.mark.asyncio
async def test_session_cache_hit():
    s1 = await SessionManager.get("uid_aaa")
    s1.companion_mode = True
    s2 = await SessionManager.get("uid_aaa")
    assert s2.companion_mode is True

@pytest.mark.asyncio
async def test_load_persona_fn():
    async def mock_load(uid): return "himiko"
    s = await SessionManager.get("uid_aaa", load_persona_fn=mock_load)
    assert s.active_persona == "himiko"

@pytest.mark.asyncio
async def test_evict_idle():
    s = await SessionManager.get("uid_aaa")
    s.last_active = datetime.now() - timedelta(minutes=130)
    removed = await SessionManager.evict_idle()
    assert "uid_aaa" in removed
    assert "uid_aaa" not in SessionManager._sessions

@pytest.mark.asyncio
async def test_evict_keeps_active():
    await SessionManager.get("uid_aaa")
    removed = await SessionManager.evict_idle()
    assert "uid_aaa" not in removed

@pytest.mark.asyncio
async def test_active_count():
    await SessionManager.get("uid_aaa")
    await SessionManager.get("uid_bbb")
    assert SessionManager.active_count() == 2