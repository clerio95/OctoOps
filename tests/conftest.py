"""Shared fixtures: a minimal AppConfig and a built Registry/ModuleContext."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from octoops.core.config import (
    AppConfig,
    CoreConfig,
    TelegramConfig,
    TransportConfig,
)
from octoops.core.event_bus import EventBus
from octoops.core.permissions import Permissions
from octoops.core.registry import ModuleConfig, ModuleContext, Registry
from octoops.core.scheduler import Scheduler
from octoops.shared.models import Role

TZ = "America/Sao_Paulo"


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig(
        telegram=TelegramConfig(bot_token="tok", admin_chat_id="1"),
        transport=TransportConfig(),
        core=CoreConfig(
            timezone=TZ,
            allowed_user_ids=["100"],
            operator_user_ids=["200"],
            admin_user_ids=["300"],
            default_role=Role.Viewer,
        ),
        enabled_modules=["status"],
    )


@pytest.fixture
def permissions(app_config: AppConfig) -> Permissions:
    c = app_config.core
    return Permissions(
        allowed_user_ids=c.allowed_user_ids,
        operator_user_ids=c.operator_user_ids,
        admin_user_ids=c.admin_user_ids,
        default_role=c.default_role,
    )


@pytest.fixture
def registry(app_config: AppConfig, permissions: Permissions) -> Registry:
    return Registry(
        config=app_config,
        event_bus=EventBus(),
        scheduler=Scheduler(timezone=TZ),
        permissions=permissions,
        start_time=datetime.now(ZoneInfo(TZ)),
    )


@pytest.fixture
def module_ctx(registry: Registry) -> ModuleContext:
    return ModuleContext(
        name="test",
        config=ModuleConfig({}),
        registry=registry,
        event_bus=registry.event_bus,
        scheduler=registry.scheduler,
    )
