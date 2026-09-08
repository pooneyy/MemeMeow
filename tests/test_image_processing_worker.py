"""图片处理 Worker 的去重与 Agent grant 生命周期单元测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

import backend.image_processing as image_processing
from backend.image_processing import ImageProcessingError, ImageProcessingOptions, ImageProcessingWorker, normalize_auto_name, normalize_reverse_image_policy
from backend.operation_policy import AllowAllOperationPolicy, GrantAssociationStore, OperationPolicyGateway, Operations, PolicyDecision
from backend.persistence.models import ScopeContext
from backend.services.tasks import PostgresTaskService


class _CountingPolicy(AllowAllOperationPolicy):
    """记录 acquire/release 次数的测试 policy。"""

    def __init__(self, *, allowed: bool = True) -> None:
        super().__init__()
        self.allowed = allowed
        self.acquire_count = 0
        self.release_count = 0

    def acquire(self, request):
        """返回允许或拒绝结果，并统计真实 acquire。"""
        self.acquire_count += 1
        if not self.allowed:
            return PolicyDecision(False, "operation_forbidden")
        return super().acquire(request)

    def release(self, grant):
        """统计补偿 release 后复用 allow-all 幂等实现。"""
        self.release_count += 1
        return super().release(grant)


class _TaskService:
    """只实现 Worker 准备阶段所需的活动查询和提交协议。"""

    def __init__(self, *, fail_submit: bool = False) -> None:
        self.active: dict[str, SimpleNamespace] = {}
        self.fail_submit = fail_submit

    def find_active(self, task_type: str, dedupe_key: str):
        """返回活动任务快照。"""
        return self.active.get(dedupe_key)

    def submit(self, task_type: str, payload: dict[str, object], *, schedule: bool = True):
        """按 Worker 传入的 dedupe key 记录一个活动任务。"""
        del schedule
        if self.fail_submit:
            raise RuntimeError("submit_failed")
        dedupe_key = ImageProcessingWorker._task_dedupe_key(task_type, payload)
        task = self.active.get(dedupe_key)
        if task is None:
            task = SimpleNamespace(task_id=uuid4().hex, status="queued", payload=dict(payload), task_type=task_type)
            self.active[dedupe_key] = task
        return task


def _job() -> SimpleNamespace:
    """构造一份不依赖 ORM session 的图片 job 快照。"""
    return SimpleNamespace(
        id=uuid4(),
        meme_id=uuid4(),
        revision=1,
        claim_generation=1,
        image_sha256="a" * 64,
        reverse_image_policy="auto",
        processing_config_hash="b" * 64,
        processing_config={"agent_model": "test-model", "embedding_model": "test-embedding"},
    )


def _worker(tasks: _TaskService, policy: _CountingPolicy, grants: GrantAssociationStore | None = None) -> ImageProcessingWorker:
    """构造不启动数据库 facade 的 Worker，并替换阶段绑定写入。"""
    worker = ImageProcessingWorker(
        object(),
        scope_id="local",
        task_service=tasks,
        policy=OperationPolicyGateway(policy),
        grant_store=grants or GrantAssociationStore(),
    )
    worker.jobs.attach_task = lambda job_id, stage, task_id: True
    return worker


def test_active_agent_task_is_bound_before_policy_acquire() -> None:
    """已有活动 Agent Task 直接复用，不建立新的 policy reservation。"""
    tasks = _TaskService()
    policy = _CountingPolicy()
    worker = _worker(tasks, policy)
    try:
        job = _job()
        first = worker._prepare_task(job, "agent")
        second = worker._prepare_task(job, "agent")
        assert first == second
        assert policy.acquire_count == 1
    finally:
        worker.shutdown()


def test_image_leaf_runner_inherits_resume_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """完整图片流水线创建叶子 facade 时必须传递 session 续跑边界。"""
    captured: dict[str, object] = {}

    class _CapturingTaskRunner:
        """只记录叶子 facade 构造参数的最小替身。"""

        def __init__(self, _resources, **kwargs):
            captured.update(kwargs)

        def register(self, _task_type, _handler):
            """接受图片阶段 handler 注册。"""

        def shutdown(self):
            """模拟叶子 facade 关闭。"""

    monkeypatch.setattr("backend.pg_services.PostgresTaskService", _CapturingTaskRunner)
    task_service = SimpleNamespace(
        agent_concurrency=2,
        settings_version="settings-test",
        lease_seconds=45,
        max_attempts=4,
        resume_enabled=True,
        resume_max_attempts=3,
        resume_backoff_seconds=5,
        resume_max_backoff_seconds=90,
        resume_timeout_seconds=1200,
    )
    worker = ImageProcessingWorker(
        SimpleNamespace(factory=lambda: None),
        scope_id="local",
        task_service=task_service,
        task_handlers={"meme_context_generation": lambda *_args: None},
    )
    try:
        assert captured["resume_enabled"] is True
        assert captured["resume_max_attempts"] == 3
        assert captured["resume_backoff_seconds"] == 5
        assert captured["resume_max_backoff_seconds"] == 90
        assert captured["resume_timeout_seconds"] == 1200
    finally:
        worker.shutdown()


def test_submit_failure_releases_only_uncommitted_grant() -> None:
    """叶子 Task 未创建时才补偿释放已取得的 grant。"""
    tasks = _TaskService(fail_submit=True)
    policy = _CountingPolicy()
    worker = _worker(tasks, policy)
    try:
        with pytest.raises(RuntimeError, match="submit_failed"):
            worker._prepare_task(_job(), "agent")
        assert policy.acquire_count == 1
        assert policy.release_count == 1
    finally:
        worker.shutdown()


def test_pipeline_grant_bind_failure_releases_uncommitted_grant() -> None:
    """pipeline 叶子任务绑定失败时必须释放 acquired grant，不能遗留可执行授权。"""

    class _RejectingBindStore(GrantAssociationStore):
        """模拟持久 grant store 无法绑定新建叶子任务。"""

        def bind_task(self, _grant, _task_id: str) -> bool:
            """拒绝绑定，触发 Worker 的补偿释放路径。"""
            return False

    tasks = _TaskService()
    policy = _CountingPolicy()
    grants = _RejectingBindStore()
    worker = _worker(tasks, policy, grants)
    job = _job()
    try:
        with pytest.raises(ImageProcessingError, match="stage_grant_bind_failed"):
            worker._prepare_task(job, "agent")
        logical_key = f"agent:{job.meme_id}:{job.image_sha256}:{job.processing_config_hash}:auto:r{job.revision}"
        request = worker.policy.request(
            worker.scope,
            Operations.ANALYSIS_AGENT,
            logical_key,
            resource_id=str(job.meme_id),
            source="image-processing",
            input_digest=job.image_sha256,
        )
        association = grants.get(request)
        assert association is not None
        assert association.state == "released"
        assert policy.release_count == 1
    finally:
        worker.shutdown()


def test_task_service_commits_missing_legacy_grant_by_stable_key() -> None:
    """旧 pipeline/standalone Task 缺少关联时仍按稳定 key 获取并提交 grant。"""
    service = object.__new__(PostgresTaskService)
    service.scope = ScopeContext("local")
    service.owner = "legacy-owner"
    service._operation_policy = OperationPolicyGateway(AllowAllOperationPolicy())
    service._grant_store = GrantAssociationStore()
    service._persist_claim_payload_updates = lambda _claim, _updates: None
    claim = SimpleNamespace(id="legacy-task", task_type="meme_context_generation", claim_generation=1)
    payload = {
        "submission_mode": "standalone",
        "meme_id": "legacy-meme",
        "image_sha256": "a" * 64,
        "processing_config_hash": "b" * 64,
    }

    service._commit_agent_grant(claim, payload)

    assert payload["agent_grant_key"].startswith("standalone-agent:legacy-task:")
    request = service._operation_policy.request(
        service.scope,
        Operations.ANALYSIS_AGENT,
        payload["agent_grant_key"],
        resource_id="legacy-meme",
        task_id="legacy-task",
        source="image-processing-standalone",
        input_digest="a" * 64,
    )
    association = service._grant_store.get(request)
    assert association is not None
    assert association.state == "committed"


def test_policy_denial_blocks_stage_without_task() -> None:
    """Agent policy 拒绝时阶段进入 blocked，且不提交叶子 Task。"""
    tasks = _TaskService()
    policy = _CountingPolicy(allowed=False)
    worker = _worker(tasks, policy)
    transitions: list[dict[str, object]] = []
    worker.jobs.transition = lambda *args, **kwargs: transitions.append(kwargs) or True
    try:
        with pytest.raises(ImageProcessingError, match="blocked"):
            worker._prepare_task(_job(), "agent")
        assert policy.acquire_count == 1
        assert tasks.active == {}
        assert transitions[-1]["status"] == "blocked"
    finally:
        worker.shutdown()


def test_standalone_stage_aliases_are_canonical_and_mode_isolated() -> None:
    """公开任务类型别名必须落到固定阶段，且 standalone 不复用 pipeline key。"""
    assert ImageProcessingWorker._canonical_stage("visual_embedding_generation") == "visual"
    assert ImageProcessingWorker._canonical_stage("meme_context_generation") == "agent"
    pipeline = ImageProcessingWorker._task_dedupe_key(
        "meme_context_generation",
        {"submission_mode": "pipeline", "job_id": "job-1", "meme_id": "meme", "image_sha256": "a" * 64, "processing_config_hash": "b" * 64, "reverse_image_policy": "forbid", "job_revision": 1},
    )
    standalone = ImageProcessingWorker._task_dedupe_key(
        "meme_context_generation",
        {"submission_mode": "standalone", "meme_id": "meme", "image_sha256": "a" * 64, "processing_config_hash": "b" * 64, "reverse_image_policy": "forbid"},
    )
    assert pipeline != standalone


def test_unknown_standalone_stage_is_rejected() -> None:
    """阶段控制面拒绝未知标识，避免客户端选择落入错误处理器。"""
    with pytest.raises(ImageProcessingError, match="invalid_image_stage"):
        ImageProcessingWorker._canonical_stage("metadata_repair")


def test_reconcile_uses_active_job_listing() -> None:
    """Worker 恢复只消费活动 Job 查询，避免依赖用户工作台排序。"""
    worker = _worker(_TaskService(), _CountingPolicy())
    scheduled: list[str] = []
    worker.jobs = SimpleNamespace(
        list_active=lambda *, limit: [SimpleNamespace(job_id="active-job", status="queued")],
        list=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("不得读取用户列表")),
    )
    worker.schedule = lambda job_id: scheduled.append(str(job_id))
    try:
        assert worker.reconcile(limit=7) == 1
        assert scheduled == ["active-job"]
    finally:
        worker.shutdown()


def test_processing_options_use_safe_defaults_but_reject_explicit_empty_values() -> None:
    """缺失选项使用安全默认值，显式空字符串和非布尔值不能静默改写语义。"""
    assert ImageProcessingOptions.normalize() == ImageProcessingOptions(reverse_image_policy="forbid", auto_name=False)
    assert normalize_reverse_image_policy(None) == "forbid"
    assert normalize_auto_name(None) is False
    with pytest.raises(ImageProcessingError, match="invalid_reverse_image_policy"):
        normalize_reverse_image_policy("")
    with pytest.raises(ImageProcessingError, match="invalid_reverse_image_policy"):
        normalize_reverse_image_policy([])
    with pytest.raises(ImageProcessingError, match="invalid_reverse_image_policy"):
        normalize_reverse_image_policy({"policy": "auto"})
    with pytest.raises(ImageProcessingError, match="invalid_auto_name"):
        normalize_auto_name("")
    with pytest.raises(ImageProcessingError, match="invalid_auto_name"):
        normalize_auto_name("false")


def _run_fixed_plan_worker(
    stages: list[dict[str, object]],
    *,
    child: SimpleNamespace | None = None,
    target_valid: bool = True,
    stage_valid: object = True,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    """用最小控制面替身运行一次 Worker，返回阶段写回、失效和新建记录。"""
    job = SimpleNamespace(
        id=uuid4(),
        status="queued",
        lease_owner=None,
        lease_expires_at=None,
        claim_generation=1,
        auto_name=True,
        image_sha256="a" * 64,
        reverse_image_policy="forbid",
        meme_id=uuid4(),
        processing_config={},
    )
    transitions: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    prepared: list[str] = []

    class Jobs:
        """提供单次 Worker reconcile 所需的父 Job 控制面。"""

        def get(self, _job_id: str):
            """返回待认领 Job。"""
            return job

        def claim(self, _job_id: str, *, owner: str):
            """模拟成功认领并保留当前 generation。"""
            del owner
            job.status = "running"
            job.lease_owner = "test-owner"
            return job

        def snapshot(self, _job_id: object):
            """返回创建时已经冻结的阶段计划。"""
            return SimpleNamespace(stages=stages)

        def transition(self, _job_id: object, _stage: str, **kwargs: object):
            """记录阶段状态写回。"""
            transitions.append(kwargs)
            return True

        def fail_job(self, _job_id: object, **kwargs: object):
            """记录固定计划失效收束。"""
            failures.append(kwargs)
            return True

    resolved_child = child or SimpleNamespace(status="queued", error=None, payload={})

    class Tasks:
        """返回已经存在或新建的叶子 Task。"""

        def get(self, _task_id: str):
            """返回叶子 Task 快照。"""
            return resolved_child

    worker = ImageProcessingWorker(object(), scope_id="local", task_service=Tasks(), handlers={})
    worker.owner = "test-owner"
    worker.jobs = Jobs()
    worker._target_valid = lambda _job: target_valid
    worker._stage_valid = lambda _job, stage: stage_valid(stage) if callable(stage_valid) else bool(stage_valid)

    def prepare(_job: object, stage: str) -> str:
        """记录新叶子 Task 创建。"""
        prepared.append(stage)
        return "new-leaf"

    worker._prepare_task = prepare
    try:
        worker._run(str(job.id))
    finally:
        worker.shutdown()
    return transitions, failures, prepared


def test_worker_runs_planned_stage_even_when_existing_result_is_valid() -> None:
    """计划内阶段不能因为旧产物有效而被 Worker 运行时跳过。"""
    transitions, failures, prepared = _run_fixed_plan_worker(
        [
            {"stage": "visual", "status": "queued", "planned": True, "skip_reason": None, "task_id": None},
            {"stage": "agent", "status": "skipped", "planned": False, "skip_reason": "already_ready", "task_id": None},
            {"stage": "auto_rename", "status": "skipped", "planned": False, "skip_reason": "disabled", "task_id": None},
            {"stage": "text_embedding", "status": "skipped", "planned": False, "skip_reason": "already_ready", "task_id": None},
        ],
        stage_valid=lambda stage: stage != "visual",
    )
    assert prepared == ["visual"]
    assert failures == []
    assert transitions[-1]["status"] == "running"


def test_worker_stops_when_skip_evidence_becomes_stale() -> None:
    """跳过阶段的创建时依据失效时，Worker 返回固定计划失效。"""
    transitions, failures, prepared = _run_fixed_plan_worker(
        [
            {"stage": "visual", "status": "skipped", "planned": False, "skip_reason": "already_ready", "task_id": None},
            {"stage": "agent", "status": "queued", "planned": True, "skip_reason": None, "task_id": None},
            {"stage": "auto_rename", "status": "skipped", "planned": False, "skip_reason": "disabled", "task_id": None},
            {"stage": "text_embedding", "status": "skipped", "planned": False, "skip_reason": "already_ready", "task_id": None},
        ],
        stage_valid=False,
    )
    assert prepared == []
    assert transitions == []
    assert failures and failures[0]["error"] == {"error": "image_processing_plan_stale"}


def test_worker_reuses_existing_planned_leaf_after_restart() -> None:
    """重启恢复时已经绑定的活动叶子 Task 不会重复创建。"""
    transitions, failures, prepared = _run_fixed_plan_worker(
        [
            {"stage": "visual", "status": "queued", "planned": True, "skip_reason": None, "task_id": "existing-leaf"},
            {"stage": "agent", "status": "skipped", "planned": False, "skip_reason": "already_ready", "task_id": None},
            {"stage": "auto_rename", "status": "skipped", "planned": False, "skip_reason": "disabled", "task_id": None},
            {"stage": "text_embedding", "status": "skipped", "planned": False, "skip_reason": "already_ready", "task_id": None},
        ],
        child=SimpleNamespace(status="queued", error=None, payload={}),
        stage_valid=lambda stage: stage != "visual",
    )
    assert prepared == []
    assert failures == []
    assert transitions[-1]["status"] == "running"


def test_worker_rejects_changed_target_before_creating_leaf() -> None:
    """计划内阶段执行前发现图片目标变化时停止并报告目标错误。"""
    transitions, failures, prepared = _run_fixed_plan_worker(
        [
            {"stage": "visual", "status": "queued", "planned": True, "skip_reason": None, "task_id": None},
            {"stage": "agent", "status": "skipped", "planned": False, "skip_reason": "already_ready", "task_id": None},
            {"stage": "auto_rename", "status": "skipped", "planned": False, "skip_reason": "disabled", "task_id": None},
            {"stage": "text_embedding", "status": "skipped", "planned": False, "skip_reason": "already_ready", "task_id": None},
        ],
        target_valid=False,
        stage_valid=True,
    )
    assert prepared == []
    assert failures == []
    assert transitions[-1]["status"] == "failed"
    assert transitions[-1]["error"] == {"error": "target_changed"}


def test_stage_valid_releases_database_session_before_file_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """阶段复用检查必须在数据库 Session 退出后才解析 BlobStore 和读取文件。"""
    meme = SimpleNamespace(
        id=uuid4(),
        scope_id="remote",
        storage_key="a" * 64 + ".png",
        extension=".png",
        size_bytes=10,
        sha256="a" * 64,
    )
    expected_blob_store = object()

    class _Resources:
        """记录 Session 生命周期，验证慢文件检查不占用数据库连接。"""

        def __init__(self) -> None:
            self.active_sessions = 0

        def factory(self):
            resources = self

            class _Session:
                """返回固定 Meme 的短事务测试替身。"""

                def __enter__(self):
                    resources.active_sessions += 1
                    return self

                def __exit__(self, *_args):
                    resources.active_sessions -= 1
                    return False

                def scalar(self, _statement):
                    return meme

            return _Session()

        def blob_store_for_scope(self, _scope):
            assert self.active_sessions == 0
            return expected_blob_store

    resources = _Resources()

    def _file_check(_resources, _scope, _meme, *, blob_store: object):
        """确认文件身份校验发生在所有数据库 Session 之外。"""
        assert resources.active_sessions == 0
        assert blob_store is expected_blob_store
        return True

    monkeypatch.setattr(image_processing, "image_file_matches", _file_check)
    worker = object.__new__(ImageProcessingWorker)
    worker.resources = resources
    worker.scope = ScopeContext("remote")
    job = SimpleNamespace(meme_id=meme.id, image_sha256=meme.sha256, processing_config={})

    assert worker._stage_valid(job, "auto_rename") is False
    assert resources.active_sessions == 0


def test_create_or_reuse_releases_database_session_before_file_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功 Job 复用时，原图 SHA 校验不得占用数据库连接或行锁。"""
    from backend.image_processing import ImageProcessingRepository, processing_config_hash

    meme = SimpleNamespace(
        id=uuid4(),
        scope_id="remote",
        storage_key="a" * 64 + ".png",
        extension=".png",
        size_bytes=10,
        sha256="a" * 64,
    )
    latest = SimpleNamespace(
        id=uuid4(),
        meme_id=meme.id,
        revision=1,
        status="succeeded",
        image_sha256=meme.sha256,
        processing_config_hash=processing_config_hash({}),
        reverse_image_policy="forbid",
        metadata_hash=None,
        auto_name=False,
        processing_config={},
    )

    class _Resources:
        """提供可观察 Session 生命周期的最小资源替身。"""

        def __init__(self) -> None:
            self.active_sessions = 0
            self.session_count = 0
            self.events: list[str] = []

        def factory(self):
            resources = self
            resources.session_count += 1

            class _Bind:
                class dialect:
                    name = "sqlite"

            class _Session:
                def __enter__(self):
                    resources.active_sessions += 1
                    resources.events.append("session_enter")
                    return self

                def __exit__(self, *_args):
                    resources.active_sessions -= 1
                    resources.events.append("session_exit")
                    return False

                def get_bind(self):
                    return _Bind()

                def scalar(self, _statement):
                    return meme

                def scalars(self, _statement):
                    return [latest]

                def commit(self):
                    resources.events.append("commit")

            return _Session()

        def blob_store_for_scope(self, _scope):
            assert self.active_sessions == 0
            self.events.append("blob_store")
            return object()

    resources = _Resources()
    repository = ImageProcessingRepository(resources, "remote")
    repository._core_ready = lambda _session, _job: True

    def _file_check(_resources, _scope, _meme, *, blob_store):
        assert resources.active_sessions == 0
        assert blob_store is not None
        resources.events.append("file_check")
        return True

    monkeypatch.setattr(image_processing, "image_file_matches", _file_check)

    reused = repository.create_or_reuse(meme.id, meme.sha256)

    assert reused is latest
    assert resources.session_count == 2
    assert resources.events.index("session_exit") < resources.events.index("blob_store")
    assert resources.events.index("file_check") < resources.events.index("session_enter", 1)
    assert resources.active_sessions == 0


class _AttachSession:
    """按固定查询顺序返回 Job、阶段和叶子任务的绑定测试 session。"""

    def __init__(self, values: list[object]) -> None:
        self.values = iter(values)

    def __enter__(self):
        """返回可供 repository 使用的伪 session。"""
        return self

    def __exit__(self, *_args) -> bool:
        """结束测试事务，不吞掉异常。"""
        return False

    def scalar(self, _statement):
        """按 repository 的三次查询顺序返回固定实体。"""
        return next(self.values)

    def commit(self) -> None:
        """记录事务提交边界；测试不需要持久化。"""
        return None


def test_pipeline_task_binding_requires_current_job_lease() -> None:
    """过期 Worker 不能绑定叶子 Task，当前 claim 才能写入父阶段。"""
    from backend.image_processing import ImageProcessingRepository

    job_id = uuid4()
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id=job_id,
        status="running",
        lease_owner="current-worker",
        lease_expires_at=now + timedelta(minutes=1),
        claim_generation=4,
        meme_id=uuid4(),
        image_sha256="a" * 64,
    )
    stage = SimpleNamespace(task_id=None, updated_at=None)
    child = SimpleNamespace(
        task_type="visual_embedding_generation",
        submission_mode=None,
        processing_job_id=None,
        image_stage=None,
        payload={},
    )
    repository = ImageProcessingRepository(object(), "local")
    repository._session = lambda: _AttachSession([job, stage, child])

    assert repository.attach_task(job_id, "visual", "task-1", owner="stale-worker", claim_generation=4) is False
    assert stage.task_id is None

    repository._session = lambda: _AttachSession([job, stage, child])
    assert repository.attach_task(job_id, "visual", "task-1", owner="current-worker", claim_generation=4) is True
    assert stage.task_id == "task-1"


def test_unbound_pipeline_task_cleanup_checks_job_ownership() -> None:
    """阶段绑定失败时只收束仍归属当前 Job 的 queued 叶子任务。"""
    job_id = uuid4()
    meme_id = uuid4()
    job = SimpleNamespace(id=job_id, meme_id=meme_id, image_sha256="b" * 64)
    task = SimpleNamespace(
        task_type="visual_embedding_generation",
        submission_mode="pipeline",
        image_stage="visual",
        processing_job_id=job_id,
        status="queued",
        payload={"job_id": str(job_id), "meme_id": str(meme_id), "image_sha256": job.image_sha256},
        message=None,
        error=None,
        completed_at=None,
        updated_at=None,
    )
    session = _AttachSession([task])
    worker = ImageProcessingWorker(object(), scope_id="local", task_service=_TaskService())
    worker.resources = SimpleNamespace(factory=lambda: session)
    try:
        assert worker._fail_unbound_pipeline_task("task-2", job, "visual", "stage_task_bind_failed") is True
        assert task.status == "failed"
        assert task.error == {"error": "stage_task_bind_failed"}
    finally:
        worker.shutdown()
