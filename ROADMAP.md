# Technical Roadmap

## Phase 1: PoC → Production Hardening (Now – Q1 2026)

### Q1 2026 — Infrastructure & Security

- [x] TLS/HTTPS for all backends
- [x] Rate limiting (slowapi)
- [x] Audit logging framework
- [ ] Prometheus metrics + Grafana dashboards
- [ ] Docker Compose configuration
- [ ] GitHub Actions CI/CD (tests, lint, SAST)
- [ ] Security scanning (Snyk, bandit for Python)

**Effort:** 2–3 weeks

### Q2 2026 — Database & Reliability

- [ ] **Alembic schema migrations** (replace `create_all_tables()`)
  - Enables safe schema updates without data loss
  - Pre-production requirement
  
- [ ] **Connection pooling** (pgBouncer or SQLAlchemy pool)
  - Reduce database connection overhead
  - Support 100+ concurrent users
  
- [ ] **Automated backups** + point-in-time recovery (PITR)
  - Daily backups to S3 or external storage
  - Test recovery procedure
  
- [ ] **ANPR job queue** (Celery or RQ, replace background threads)
  - Retry failed detections
  - Scale to multiple workers
  - Persistent job state
  
- [ ] **Dead-letter queue** for failed events
  - Investigate and replay failed detections

**Effort:** 3–4 weeks

### Q3 2026 — Analytics & Reporting

- [ ] **Re-identification across cameras** (embedding-based)
  - Track same vehicle across multiple cameras
  - Use visual embeddings (e.g., ResNet-50 on vehicle/license plate)
  - Fuzzy matching for similar plate texts
  
- [ ] **Multi-object tracking** (DeepSORT or SORT)
  - Replace simple nearest-neighbor tracking
  - Better handling of occlusions and scene cuts
  
- [ ] **Heatmaps & dwell-time analysis**
  - Identify high-traffic zones
  - Detect vehicles lingering in restricted areas
  
- [ ] **PDF report generation** (Model 3 analytics)
  - Export federated analytics as PDF
  - Scheduled reports (daily/weekly)
  
- [ ] **Historical trend queries** (TimescaleDB or ClickHouse)
  - Time-series storage for metrics
  - Efficient aggregation over weeks/months

**Effort:** 4–5 weeks

### Q4 2026 → 2027 — Scale & Interoperability

- [ ] **Real vendor VMS adapters** (Hikvision, Axis, Dahua APIs)
  - Plug-and-play federation with existing vendor systems
  - Normalize event formats
  
- [ ] **Multi-region federation** (cross-city deployments)
  - Model 3 adapters for remote deployments
  - Latency-aware event correlation
  
- [ ] **Hardware acceleration** (NVIDIA/Intel GPU ANPR inference)
  - Offload YOLO to GPU (CUDA/TensorRT)
  - 5–10x speedup for inference
  
- [ ] **Edge deployment** (Model 2 on-camera NVR or edge devices)
  - Run ANPR at the camera (NVR-local processing)
  - Reduce central server load
  
- [ ] **API versioning & backward compatibility strategy**
  - v1 endpoints remain stable
  - v2 for breaking changes
  - 6-month deprecation period

**Effort:** 6–8 weeks

---

## Known Technical Debt

| Issue | Impact | Priority | Effort | Notes |
|-------|--------|----------|--------|-------|
| No schema migrations (Alembic) | HIGH | HIGH | 2–3 days | Risk: schema changes require downtime |
| ANPR in background thread (not queue) | HIGH | HIGH | 3–5 days | No retry, no scaling, no persistence |
| In-memory auth cache in Model 3 | MEDIUM | MEDIUM | 1 day | Token expiry edge cases; use Redis |
| No GPU support for YOLO inference | MEDIUM | MEDIUM | 2–3 days | CPU-bound, slow (~500ms/frame on CPU) |
| No multi-process deployment docs | LOW | LOW | 1 day | Unclear how to scale to multiple servers |
| `create_all_tables()` instead of migrations | HIGH | HIGH | 2–3 days | Hard to evolve schema in production |
| No task queue for long-running jobs | MEDIUM | MEDIUM | 3–5 days | ANPR jobs block; can't scale |
| No caching layer (Redis) | LOW | LOW | 2 days | Could optimize repeated queries |

---

## Dependencies Under Review

| Dependency | Current | Issue | Recommendation |
|------------|---------|-------|------------------|
| **YOLO** (PyTorch) | yolov8n.pt (COCO) | Doesn't detect plates; general-purpose weights | Swap for plate-specific weights; evaluate TensorRT for speedup |
| **EasyOCR** | Default English | Accuracy varies by license plate region | Consider Tesseract + deep learning hybrid; add regional models |
| **OpenCV** | Latest | No GPU decode support in standard build | Build with CUDA support for H.264 hw decode |
| **PostGIS** | Latest | Performance concerns at 1000+ cameras | Benchmark; consider TimescaleDB for time-series |
| **FastAPI** | Latest | Stable; no concerns | Keep current; monitor for HTTP/2 support |
| **PostgreSQL** | 13+ | Needs tuning for scale | Keep; add read replicas for Model 3 queries |

---

## API Stability Guarantees

### Current (PoC Stage)

- **v1 endpoints may change** without notice
- **Breaking changes okay** if justified
- **Deprecation notice not required**
- No SLA on uptime

### After Hardening (Q2 2026)

- **v1 endpoints frozen** — guaranteed stable
- **v2 introduced** for breaking changes
- **Deprecation period:** 6 months notice before v1 removal
- **SLA:** 99.5% uptime (36 minutes downtime/month)

### Versioning Strategy

```
v1 (current)     → stable, no breaking changes
v2 (when needed) → new major features, breaking changes

/api/v1/cameras      (stable)
/api/v1/auth/login   (stable)
/api/v2/cameras      (optional: future breaking change)
```

---

## Contact & Governance

- **Architecture Lead**: [Name/GitHub handle]
- **Security Review**: [Name/email]
- **Release Cadence**: Monthly minor (bug fixes), quarterly major (Q1/Q2/Q3/Q4)
- **Deployment Window**: Tuesdays, 2–4 AM UTC (low-traffic window)
- **Rollback Procedure**: Keep N-1 version in production for 24 hours

---

## References

- Alembic: https://alembic.sqlalchemy.org/
- Celery: https://docs.celeryproject.org/
- DeepSORT: https://github.com/nwojke/deep_sort
- TensorRT: https://docs.nvidia.com/deeplearning/tensorrt/
- TimescaleDB: https://www.timescale.com/
- Hikvision API: https://www.hikvision.com/en/
