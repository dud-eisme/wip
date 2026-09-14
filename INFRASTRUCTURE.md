# Infrastructure Sizing & Deployment Guide

## 1. Minimum Hardware Requirements (Single Deployment)

| Component | CPU | RAM | Storage | Notes |
|-----------|-----|-----|---------|-------|
| Model 1 Backend | 2 cores | 4 GB | 50 GB | PostgreSQL + PostGIS queries |
| Model 1 Frontend | - | - | 100 MB | Static assets only |
| Model 2 Backend | 4 cores | 8 GB | 500 GB | YOLO inference, frame decoding |
| Model 2 Frontend | - | - | 50 MB | Static assets |
| Model 3 Backend | 1 core | 2 GB | 10 GB | Lightweight federation |
| Model 3 Frontend | - | - | 50 MB | Static assets |
| **Total** | **7-8 cores** | **14-16 GB** | **~560 GB** | Single node or distributed |

---

## 2. Per-Camera Resource Cost

These estimates assume:
- YOLO inference at 8 fps (configurable via `MODEL2_FRAME_RATE_TARGET`)
- H.264 compression (typical RTSP)
- Snapshot retention: 30 days
- ~2-5 snapshots per hour per camera (detected plates)

| Cameras | CPU Impact | RAM Impact | Storage/Day (720p 8fps) | Outbound Bandwidth |
|---------|-----------|-----------|------------------------|-----------------|
| 10 | ~0.2 cores | +200 MB | ~50 GB | ~20 Mbps |
| 50 | ~1.0 cores | +1 GB | +250 GB | ~100 Mbps |
| 100 | ~2.0 cores | +2 GB | +500 GB | ~200 Mbps |

**Formula:**
```
CPU (cores) ≈ num_cameras * 0.02 + 0.5 (base)
RAM (GB) ≈ num_cameras * 0.02 + 2 (base)
Storage/day ≈ num_cameras * (resolution_mp * 30) MB
  where: 720p ≈ 0.9 MP, 1080p ≈ 2 MP, 4K ≈ 8 MP
Bandwidth ≈ num_cameras * 2 Mbps (at 8fps, H.264)
```

---

## 3. PostgreSQL Tuning

Add to `/etc/postgresql/13/main/postgresql.conf`:

```ini
# Connection pooling
max_connections = 200
shared_buffers = 4GB              # 25% of system RAM
effective_cache_size = 12GB       # 75% of system RAM
work_mem = 20MB

# PostGIS spatial indexing
maintenance_work_mem = 1GB
random_page_cost = 1.1            # For SSD (1.1) vs HDD (4.0)
effective_io_concurrency = 200    # For SSD

# Query performance
wal_buffers = 16MB
checkpoint_completion_target = 0.9
wal_level = replica
max_wal_senders = 3
```

### Create Performance Indexes

```sql
-- Speed up camera lookups by department
CREATE INDEX ix_camera_department ON cameras(department);

-- Speed up health status queries
CREATE INDEX ix_camera_health_status ON cameras(health_status);

-- GIS spatial index for location queries
CREATE INDEX ix_camera_location ON cameras USING GIST(location);

-- ANPR event queries
CREATE INDEX ix_anpr_events_camera_timestamp 
  ON anpr_events(camera_id, timestamp_ms DESC);

CREATE INDEX ix_anpr_events_plate 
  ON anpr_events(plate_text);

-- Cluster tables for query performance
CLUSTER anpr_events USING ix_anpr_events_camera_timestamp;
```

---

## 4. Network Bandwidth Requirements

### Per-Stream Egress

- **MJPEG live stream** (8 fps, 720p H.264): ~1-2 Mbps per stream
- **ANPR snapshots** (2-5 per hour per camera): ~1-5 KB each = negligible
- **Model 3 federation queries**: ~1-5 MB/hr per query

### Multi-Camera Deployment

```
50 concurrent streams × 1.5 Mbps = 75 Mbps egress
100 concurrent streams × 1.5 Mbps = 150 Mbps egress
```

**Recommendation:** 1 Gbps minimum for 50+ concurrent sources; 10 Gbps for 100+.

---

## 5. Snapshot Retention & Cleanup

### Directory Structure

```
media/anpr_snapshots/
├── camera-uuid-1/
│   ├── 2026-09-14/
│   │   ├── event-uuid-1.jpg
│   │   ├── event-uuid-2.jpg
│   │   └── ...
│   └── 2026-09-15/
│       └── ...
└── camera-uuid-2/
    └── ...
```

This structure allows:
- **Direct file browsing** without database lookup
- **Easy cleanup by date** (delete entire date directory)
- **Parallel cleanup** (one process per camera)

### Cleanup Script

**File: `model2/backend/cleanup_snapshots.py`**

```python
import os
import shutil
import time
from pathlib import Path
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RETENTION_DAYS = int(os.getenv("ANPR_SNAPSHOT_RETENTION_DAYS", "30"))
SNAPSHOT_DIR = Path(os.getenv("ANPR_SNAPSHOT_DIR", "media/anpr_snapshots"))

def cleanup_snapshots():
    """Delete snapshots older than RETENTION_DAYS."""
    cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
    deleted_count = 0
    freed_bytes = 0
    
    logger.info(f"Starting cleanup: retaining only last {RETENTION_DAYS} days")
    
    for camera_dir in SNAPSHOT_DIR.iterdir():
        if not camera_dir.is_dir():
            continue
        
        for date_dir in camera_dir.iterdir():
            if not date_dir.is_dir():
                continue
            
            try:
                date_str = date_dir.name
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                
                if date_obj < cutoff_date:
                    logger.info(f"Deleting {date_dir}")
                    
                    # Get size before deletion
                    dir_size = sum(f.stat().st_size for f in date_dir.rglob('*') if f.is_file())
                    
                    shutil.rmtree(date_dir)
                    deleted_count += len(list(date_dir.glob("*.jpg")))  # Rough count
                    freed_bytes += dir_size
            except (ValueError, OSError) as e:
                logger.error(f"Error processing {date_dir}: {e}")
    
    logger.info(
        f"Cleanup complete: deleted {deleted_count} snapshots, "
        f"freed {freed_bytes / 1e9:.2f} GB"
    )

if __name__ == "__main__":
    cleanup_snapshots()
```

### Schedule with Cron

```bash
# Run cleanup daily at 2 AM
0 2 * * * /usr/bin/python3 /path/to/cleanup_snapshots.py >> /var/log/cleanup.log 2>&1
```

---

## 6. Monitoring & Health Checks

### Prometheus Metrics (All Backends)

Each backend exposes metrics at `/metrics`:

```python
# model2/backend/main.py
from prometheus_client import Counter, Histogram, Gauge, generate_latest

# Metrics
camera_frames_processed = Counter(
    'camera_frames_processed',
    'Total frames decoded',
    ['camera_id']
)
anpr_detections = Counter(
    'anpr_detections',
    'Total plates detected',
    ['camera_id']
)
worker_connections = Gauge(
    'camera_worker_connections',
    'Active camera workers'
)
decode_errors = Counter(
    'decode_errors',
    'Decoder errors',
    ['camera_id', 'error_type']
)

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type="text/plain")
```

### Grafana Dashboard

Key dashboards:
1. **System Health**: CPU, RAM, disk usage, network
2. **Model 2 Status**: Active workers, frame rate, decode errors
3. **ANPR Pipeline**: Detections/hour, confidence histogram, snapshot storage usage
4. **Model 3 Federation**: Adapter sync success rate, correlation latency
5. **Security**: Failed logins, rate limit events, audit log volume

---

## 7. Distributed Deployment (Multi-Node)

### Architecture

```
┌─────────────────────┐
│  Load Balancer      │ (nginx/HAProxy)
│  (HTTPS, TLS)       │
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    │             │
┌───▼───┐   ┌────▼───┐
│App 1   │   │App 2   │  (Multiple instances)
│M1+M2+M3│   │M1+M2+M3│  (or separate nodes)
└───┬───┘   └────┬───┘
    │            │
    └──────┬─────┘
           │
    ┌──────▼──────┐
    │ PostgreSQL  │ (Shared database)
    │ Cluster     │
    └─────────────┘
```

### Shared Cache (Redis)

For multi-instance Model 3 token caching:

```python
# model3/backend/cache.py
import redis
import os

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
cache = redis.from_url(redis_url)

def get_cached_token():
    token = cache.get("model3_service_token")
    if token:
        return token.decode()
    return None

def cache_token(token: str, ttl: int = 86400):
    cache.setex("model3_service_token", ttl, token)
```

---

## 8. Disaster Recovery

### Database Backup

```bash
#!/bin/bash
# backup_postgres.sh

BACKUP_DIR=/backups/postgres
DB_NAME=cctv_db
DATE=$(date +%Y-%m-%d_%H-%M-%S)

mkdir -p $BACKUP_DIR

pg_dump -h localhost -U cctv_app $DB_NAME | \
  gzip > $BACKUP_DIR/cctv_db_$DATE.sql.gz

# Keep only last 30 days
find $BACKUP_DIR -name "*.sql.gz" -mtime +30 -delete

echo "Backup completed: $BACKUP_DIR/cctv_db_$DATE.sql.gz"
```

**Cron schedule:**

```bash
# Daily backup at 3 AM
0 3 * * * /path/to/backup_postgres.sh >> /var/log/backup.log 2>&1
```

### Restore Procedure

```bash
# Restore from backup
pg_restore -h localhost -U cctv_app -d cctv_db \
  /backups/postgres/cctv_db_2026-09-14_03-00-00.sql.gz
```

---

## 9. Deployment Checklist

- [ ] **PostgreSQL** tuned and indexed
- [ ] **TLS certificates** generated (self-signed for dev, CA-signed for prod)
- [ ] **Secrets** in `.env` (not committed)
- [ ] **Rate limiting** configured
- [ ] **Audit logging** enabled, logs stored outside code directory
- [ ] **Snapshot cleanup** scheduled via cron
- [ ] **Database backups** scheduled daily
- [ ] **Prometheus/Grafana** monitoring set up
- [ ] **Firewall rules** restrict access to app ports (8000, 8001, 8002)
- [ ] **Load balancer** (nginx) configured for HTTPS, TLS 1.2+
- [ ] **Health check endpoint** (`/health`) monitored
- [ ] **Log rotation** configured (logrotate or equivalent)
