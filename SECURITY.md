# Security Architecture & Hardening Guide

## Overview

This document outlines security measures implemented across Models 1, 2, and 3, along with deployment hardening recommendations.

---

## 1. TLS/HTTPS Configuration

### Enable HTTPS for All Backends

All three backends must run behind HTTPS in production. Configure in `.env`:

```env
ENABLE_HTTPS=true
SSL_CERT_PATH=/etc/ssl/certs/cert.pem
SSL_KEY_PATH=/etc/ssl/private/key.pem
```

### Generate Self-Signed Certificates (Development)

```bash
# Generate 365-day self-signed cert
openssl req -x509 -newkey rsa:4096 -nodes \
  -out cert.pem -keyout key.pem -days 365 \
  -subj "/C=IN/ST=Gujarat/L=Ahmedabad/O=Police/CN=localhost"

mkdir -p ssl/
mv cert.pem key.pem ssl/
```

### FastAPI TLS Setup

Each backend's `main.py` includes TLS support via uvicorn:

```python
import ssl
import os

if os.getenv("ENABLE_HTTPS") == "true":
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(
        certfile=os.getenv("SSL_CERT_PATH"),
        keyfile=os.getenv("SSL_KEY_PATH")
    )
else:
    ssl_context = None

uvicorn.run(
    app,
    host="0.0.0.0",
    port=8000,
    ssl_context=ssl_context
)
```

---

## 2. Authentication & Authorization

### JWT-Based Authentication (Model 1)

- **Endpoint**: `POST /api/v1/auth/login`
- **Method**: Username/password → JWT token
- **Token Format**: HS256 (HMAC-SHA256)
- **Secret Key**: Must be ≥32 characters, set in `.env` as `SECRET_KEY`
- **Expiry**: Configure in auth module (default: 24 hours)

```python
# model1/backend/auth.py
from datetime import timedelta, datetime
import jwt

TOKEN_EXPIRY = timedelta(hours=24)
SECRET_KEY = os.getenv("SECRET_KEY")

def create_access_token(user_id: str) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + TOKEN_EXPIRY,
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload["user_id"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

### Department-Scoped RBAC

**All three models enforce department-level access control:**

```python
# model1/backend/auth.py
def get_current_user(token: str = Depends(HTTPBearer())) -> User:
    user_id = verify_token(token.credentials)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def enforce_department_access(user: User, resource_department: str):
    """Enforce: user can only see cameras in their department (except admins)."""
    if user.department != "Admin" and user.department != resource_department:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: {resource_department} cameras are not in your department"
        )
```

**Usage in endpoints:**

```python
@app.get("/api/v1/cameras/{camera_id}")
async def get_camera(camera_id: str, user: User = Depends(get_current_user)):
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    
    # Enforce department-scoped access
    enforce_department_access(user, camera.department)
    
    return camera
```

### Shared Identity (Models 2 & 3)

- **Model 2** reads Model 1's `users` table directly → same `SECRET_KEY` required
- **Model 3** authenticates to Model 1 as a service account, caches token in memory

---

## 3. Rate Limiting

### Implementation (All Backends)

Rate limiting is enforced via `slowapi` middleware:

```python
# model1/backend/main.py (same for model2, model3)
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

# Rate limit specific endpoints
@app.post("/api/v1/auth/login", dependencies=[Depends(limiter.limit("5/minute"))])
async def login(credentials: LoginRequest):
    # 5 login attempts per minute per IP
    pass

@app.get("/api/v1/cameras", dependencies=[Depends(limiter.limit("100/minute"))])
async def get_cameras():
    # 100 requests per minute per IP
    pass
```

### Configuration

```env
# .env
RATE_LIMIT_REQUESTS=100        # Requests per minute
RATE_LIMIT_PERIOD=60           # Seconds
```

### Response Headers

Every response includes rate limit info:

```
X-RateLimit-Limit: 100
X-RateLimit-Period: 60
```

---

## 4. Audit Logging

### Audit Event Types

| Event | Logged | Notes |
|-------|--------|-------|
| LOGIN_SUCCESS | ✓ | Successful authentication |
| LOGIN_FAILURE | ✓ | Failed login attempt (reason: invalid_credentials, etc.) |
| UPDATE (cameras, events) | ✓ | User modified resource |
| DELETE (cameras, events) | ✓ | User deleted resource |
| RATE_LIMIT_EXCEEDED | ✓ | IP exceeded rate limit |
| DATABASE_ERROR | ✓ | Database connection/query failure |
| SYSTEM_STARTUP | ✓ | Graceful startup |
| SYSTEM_SHUTDOWN | ✓ | Graceful shutdown |
| CONFIG_WARNING | ✓ | Missing/invalid configuration (Model 3) |
| ADAPTER_SYNC_SUCCESS | ✓ | Model 3 federation sync succeeded |
| ADAPTER_SYNC_FAILED | ✓ | Model 3 federation sync failed |

### Log Format

JSON-formatted, one event per line:

```json
{
  "timestamp": "2026-09-14T18:00:00.000Z",
  "action": "LOGIN_SUCCESS",
  "resource": "auth",
  "user_id": "user-123",
  "details": {
    "ip_address": "192.168.1.100",
    "status": "success"
  }
}

{
  "timestamp": "2026-09-14T18:01:30.500Z",
  "action": "UPDATE",
  "resource": "camera",
  "user_id": "admin-1",
  "details": {
    "camera_id": "CAM-0001",
    "field_changed": "health_status",
    "old_value": "Operational",
    "new_value": "Maintenance Required"
  }
}
```

### Log Location

```
logs/audit.log     # All models write here
```

### Retention Policy

```bash
# Archive logs after 30 days (cron job)
0 2 * * * find logs/ -name "audit.log.*" -mtime +30 -delete
```

---

## 5. CORS Hardening

### Default (Development)

```env
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:5174,http://localhost:5175
```

### Production

```env
# Only allow your deployment domain
ALLOWED_ORIGINS=https://cctv.police.gov.in
```

### Implementation

```python
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],  # No PUT, no *
    allow_headers=["Authorization", "Content-Type"],    # No *, whitelist only
    max_age=3600,  # 1 hour preflight cache
)
```

---

## 6. Secrets Management

### Never Commit Secrets

**`.env.example`** contains ONLY placeholders:

```env
SECRET_KEY=your-secret-key-here-change-in-production-min-32-chars
DATABASE_URL=postgresql://user:password@localhost/cctv_db
MODEL1_SERVICE_PASSWORD=change-me-in-production
```

**`.env`** (actual secrets) is in `.gitignore` and never committed.

### Environment Variable Validation

```python
# model1/backend/config.py
import os

def validate_secrets():
    required_secrets = [
        "SECRET_KEY",
        "DATABASE_URL",
    ]
    
    missing = [s for s in required_secrets if not os.getenv(s)]
    if missing:
        raise ValueError(f"Missing required secrets: {missing}")
    
    secret_key = os.getenv("SECRET_KEY")
    if len(secret_key) < 32:
        raise ValueError("SECRET_KEY must be ≥32 characters")
    
    logger.info("All required secrets validated")

# Call at startup
validate_secrets()
```

---

## 7. Deployment Hardening Checklist

- [ ] **TLS/HTTPS enabled** on all backends
- [ ] **Self-signed cert for dev**, CA-signed for production
- [ ] **`SECRET_KEY` is ≥32 characters, unique per deployment**
- [ ] **`.env` file is in `.gitignore` and never committed**
- [ ] **Database user has limited permissions** (no DDL for app user)
- [ ] **Rate limiting configured** (at least 5/min for login, 100/min for API)
- [ ] **Audit logging enabled**, logs stored outside code directory
- [ ] **CORS origins restricted** to known frontend domains
- [ ] **Database connection uses SSL** (`sslmode=require` in PostgreSQL URL)
- [ ] **JWT token expiry set** (default 24 hours)
- [ ] **Firewall rules enforced**: only 8000, 8001, 8002 (backends) accessible within VPC
- [ ] **No API keys in logs** (audit log scrubbing in place)
- [ ] **Password hashing via bcrypt** (min 12 rounds)

---

## 8. Production Deployment Recommendations

### Use a Reverse Proxy (nginx)

```nginx
# /etc/nginx/sites-available/cctv-model1
upstream model1_backend {
    server localhost:8000;
}

server {
    listen 443 ssl http2;
    server_name cctv.police.gov.in;

    ssl_certificate /etc/letsencrypt/live/cctv.police.gov.in/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cctv.police.gov.in/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Rate limit at nginx level (backup)
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    limit_req zone=api burst=20 nodelay;

    location / {
        proxy_pass http://model1_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name cctv.police.gov.in;
    return 301 https://$server_name$request_uri;  # Redirect HTTP → HTTPS
}
```

### Database Security

```sql
-- PostgreSQL: limit app user to SELECT, INSERT, UPDATE, DELETE only
CREATE USER cctv_app WITH PASSWORD 'strong-password-here';
GRANT CONNECT ON DATABASE cctv_db TO cctv_app;
GRANT USAGE ON SCHEMA public TO cctv_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO cctv_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cctv_app;
```

### Monitoring & Alerting

Set up alerts for:
- Failed login attempts (>5 in 5 min from one IP)
- Rate limit exceeded (>10 times in 1 hour)
- Database errors (>5 in 5 min)
- System shutdown without graceful restart
- Missing/invalid secrets at startup

---

## References

- OWASP Top 10: https://owasp.org/Top10/
- JWT Best Practices: https://tools.ietf.org/html/rfc8725
- TLS 1.3: https://tools.ietf.org/html/rfc8446
- PostgreSQL Security: https://www.postgresql.org/docs/current/sql-createrole.html
