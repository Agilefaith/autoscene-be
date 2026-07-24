from celery import Celery
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "autoscene",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.tasks.finalize",
        # AutoScene (scene-based) pipeline
        "app.workers.tasks.scene_breakdown",
        "app.workers.tasks.image_gen",
        "app.workers.tasks.voiceover",
        "app.workers.tasks.scene_render",
        "app.workers.tasks.assembly",
        "app.workers.tasks.thumbnails",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    # prefetch=1 is required for priority to actually take effect — otherwise a
    # worker greedily buffers tasks and ignores newly-arrived higher-priority ones.
    worker_prefetch_multiplier=1,
    # ── Priority (Redis) ──────────────────────────────────────────────────────
    # Redis ignores message priority unless these are set. With Redis, priority is
    # bucketed into sub-queues per `priority_steps`; the worker drains lower step
    # numbers FIRST, so **0 = highest priority** (opposite of RabbitMQ). Callers
    # therefore use small numbers for paid users — see PRIORITY_* in projects.py.
    broker_transport_options={
        "priority_steps": list(range(10)),   # 0..9 buckets
        "sep": ":",
        "queue_order_strategy": "priority",
    },
    task_default_priority=5,                  # mid bucket for anything unspecified
    # Queue definitions. Two queues: `fast` (light orchestration) and `media`
    # (network- + FFmpeg-heavy). The legacy `render` queue (HeyGen) is retired.
    task_queues={
        "fast": {"exchange": "fast", "routing_key": "fast"},
        "media": {"exchange": "media", "routing_key": "media"},
    },
    task_default_queue="fast",
    task_routes={
        "app.workers.tasks.finalize.*": {"queue": "fast"},
        # AutoScene: breakdown is light (fast queue); image/voice/render/assembly
        # are network- + FFmpeg-heavy (media queue).
        "app.workers.tasks.scene_breakdown.*": {"queue": "fast"},
        "app.workers.tasks.image_gen.*": {"queue": "media"},
        "app.workers.tasks.voiceover.*": {"queue": "media"},
        "app.workers.tasks.scene_render.*": {"queue": "media"},
        "app.workers.tasks.assembly.*": {"queue": "media"},
        "app.workers.tasks.thumbnails.*": {"queue": "media"},
    },
    # Retry policy defaults
    task_max_retries=3,
    task_soft_time_limit=900,   # 15 min
    task_time_limit=1200,       # 20 min hard limit
    # Beat schedule. Campaign runner is retired (dormant pending an AutoScene port);
    # the stuck-job watchdog now guards AutoScene projects.
    beat_schedule={
        "stuck-job-watchdog": {
            "task": "app.workers.tasks.finalize.watchdog_stuck_jobs",
            "schedule": 300.0,  # every 5 minutes
        },
        "reset-video-quotas": {
            "task": "app.workers.tasks.finalize.reset_expired_video_quotas",
            "schedule": 3600.0,  # hourly; refills quotas whose period has ended
        },
    },
)
