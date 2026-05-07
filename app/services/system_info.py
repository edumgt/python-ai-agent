"""시스템 상태 대시보드용 정보 수집."""
import asyncio
import subprocess
import shutil
import httpx
import redis.asyncio as aioredis
from datetime import datetime, timezone
from app.config import settings


async def _ping_http(url: str, timeout: float = 2.0) -> tuple[bool, float]:
    try:
        t0 = asyncio.get_event_loop().time()
        async with httpx.AsyncClient(timeout=timeout) as cli:
            await cli.get(url)
        return True, round((asyncio.get_event_loop().time() - t0) * 1000, 1)
    except Exception:
        return False, -1


async def _ping_redis(url: str, timeout: float = 2.0) -> tuple[bool, float]:
    try:
        t0 = asyncio.get_event_loop().time()
        r = aioredis.from_url(url, socket_connect_timeout=timeout, decode_responses=True)
        await asyncio.wait_for(r.ping(), timeout=timeout)
        await r.aclose()
        return True, round((asyncio.get_event_loop().time() - t0) * 1000, 1)
    except Exception:
        return False, -1


def _disk_usage(path: str = "/") -> dict:
    total, used, free = shutil.disk_usage(path)
    return {
        "total_gb": round(total / 1e9, 1),
        "used_gb":  round(used  / 1e9, 1),
        "free_gb":  round(free  / 1e9, 1),
        "pct":      round(used / total * 100, 1),
    }


def _cpu_mem() -> dict:
    try:
        import psutil
        return {
            "cpu_pct": psutil.cpu_percent(interval=0.2),
            "mem_total_gb": round(psutil.virtual_memory().total / 1e9, 1),
            "mem_used_gb":  round(psutil.virtual_memory().used  / 1e9, 1),
            "mem_pct":      psutil.virtual_memory().percent,
        }
    except ImportError:
        # psutil 미설치 시 /proc 직접 읽기
        try:
            with open("/proc/meminfo") as f:
                lines = {l.split(":")[0]: int(l.split()[1]) for l in f if ":" in l}
            total = lines.get("MemTotal", 0)
            avail = lines.get("MemAvailable", 0)
            used  = total - avail
            pct   = round(used / total * 100, 1) if total else 0
            return {
                "cpu_pct": None,
                "mem_total_gb": round(total / 1e6, 1),
                "mem_used_gb":  round(used  / 1e6, 1),
                "mem_pct":      pct,
            }
        except Exception:
            return {"cpu_pct": None, "mem_total_gb": None, "mem_used_gb": None, "mem_pct": None}


def _docker_containers() -> list[dict]:
    """docker ps 실행 결과 파싱. Docker 미설치 시 빈 목록 반환."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format",
             "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"],
            capture_output=True, text=True, timeout=5
        )
        containers = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                containers.append({
                    "id":     parts[0][:12],
                    "name":   parts[1],
                    "image":  parts[2],
                    "status": parts[3],
                    "ports":  parts[4] if len(parts) > 4 else "",
                    "up":     "Up" in parts[3],
                })
        return containers
    except Exception:
        return []


async def get_system_status() -> dict:
    # 서비스 핑을 병렬로
    (ollama_ok, ollama_ms), (qdrant_ok, qdrant_ms), (redis_ok, redis_ms) = await asyncio.gather(
        _ping_http(f"{settings.OLLAMA_BASE_URL}/api/tags"),
        _ping_http(f"{settings.QDRANT_URL}/collections"),
        _ping_redis(settings.REDIS_URL),
    )

    services = [
        {"name": "Ollama (LLM)",  "url": settings.OLLAMA_BASE_URL, "ok": ollama_ok, "ms": ollama_ms},
        {"name": "Qdrant",        "url": settings.QDRANT_URL,       "ok": qdrant_ok, "ms": qdrant_ms},
        {"name": "Redis",         "url": settings.REDIS_URL,        "ok": redis_ok,  "ms": redis_ms},
    ]

    return {
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "host":        _cpu_mem(),
        "disk":        _disk_usage("/"),
        "services":    services,
        "containers":  _docker_containers(),
    }
