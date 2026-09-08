"""图片处理固定计划的纯函数契约测试。"""

from backend.image_stage_plan import build_stage_plan


def test_full_retry_plans_every_enabled_stage() -> None:
    """完整重试不因已有产物就缩小执行范围。"""
    plan = build_stage_plan(
        "full_retry",
        auto_name=True,
        readiness={"visual": True, "agent": True, "auto_rename": True, "text_embedding": True},
    )
    assert all(planned and reason is None for planned, reason in plan.values())


def test_repair_agent_includes_direct_dependents_only() -> None:
    """Agent 未就绪时带动自动命名和文本向量，但不重跑有效视觉结果。"""
    plan = build_stage_plan(
        "repair",
        auto_name=True,
        readiness={"visual": True, "agent": False, "auto_rename": True, "text_embedding": True},
    )
    assert plan == {
        "visual": (False, "already_ready"),
        "agent": (True, None),
        "auto_rename": (True, None),
        "text_embedding": (True, None),
    }


def test_repair_warning_only_plans_auto_rename() -> None:
    """只有自动重命名 warning 时不创建其它阶段。"""
    plan = build_stage_plan(
        "repair",
        auto_name=True,
        readiness={"visual": True, "agent": True, "auto_rename": False, "text_embedding": True},
    )
    assert plan["auto_rename"] == (True, None)
    assert plan["visual"] == (False, "already_ready")
    assert plan["agent"] == (False, "already_ready")
    assert plan["text_embedding"] == (False, "already_ready")


def test_repair_visual_only_does_not_expand_to_downstream_stages() -> None:
    """视觉结果单独失效时，修复计划只包含视觉阶段。"""
    plan = build_stage_plan(
        "repair",
        auto_name=True,
        readiness={"visual": False, "agent": True, "auto_rename": True, "text_embedding": True},
    )
    assert plan == {
        "visual": (True, None),
        "agent": (False, "already_ready"),
        "auto_rename": (False, "already_ready"),
        "text_embedding": (False, "already_ready"),
    }


def test_repair_text_only_plans_text_embedding() -> None:
    """只有文本向量失效时，不重新执行视觉、Agent 或自动命名。"""
    plan = build_stage_plan(
        "repair",
        auto_name=True,
        readiness={"visual": True, "agent": True, "auto_rename": True, "text_embedding": False},
    )
    assert plan == {
        "visual": (False, "already_ready"),
        "agent": (False, "already_ready"),
        "auto_rename": (False, "already_ready"),
        "text_embedding": (True, None),
    }


def test_disabled_auto_rename_is_explicitly_skipped() -> None:
    """关闭可选阶段时仍写入有限的 disabled 原因。"""
    plan = build_stage_plan(
        "full_retry",
        auto_name=False,
        readiness={"visual": False, "agent": False, "auto_rename": False, "text_embedding": False},
    )
    assert plan["auto_rename"] == (False, "disabled")
