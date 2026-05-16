---
name: docker-workflows
description: Reviews and hardens Dockerfiles and docker-compose files — multi-stage build conversion, base-image choice, layer caching, secret leakage, root-user containers, missing healthchecks. Use when reviewing a Dockerfile, optimizing image size or build time, writing a compose file, or auditing container security.
---

# Docker — Dockerfile and compose review

For Kubernetes manifests and pod debugging, see the `kubernetes-operations` skill. For Helm charts, see `kubernetes-operators` (if shipped as an operator) or chart-specific tooling.

## When to invoke

Open with the static analyzers — they're stdlib Python, fast, deterministic:

```bash
SKILL=plugins/devops-skills/skills/docker-workflows
python3 "$SKILL/scripts/dockerfile_analyzer.py" Dockerfile
python3 "$SKILL/scripts/compose_validator.py" docker-compose.yml
```

Both accept `--output json` for piping. `dockerfile_analyzer.py --security` narrows to security findings only. `compose_validator.py --strict` fails on warnings.

Read the findings before suggesting changes. The analyzer surfaces ~80% of routine issues; reserve LLM judgment for the rest.

## Pre-flight: what's the image FOR?

Image strategy follows the workload, not vice versa.

| Workload | Base image default | Why |
|---|---|---|
| Compiled binary (Go, Rust) | `gcr.io/distroless/static-debian12` or `scratch` | No shell, no libc — tiny attack surface |
| Compiled w/ glibc deps (CGo) | `gcr.io/distroless/base-debian12` | Has glibc + ca-certs, no shell |
| Python | `python:3.X-slim` (Debian) | Alpine's musl breaks many wheels (pandas, lxml) |
| Node.js | `node:X-alpine` | musl is fine for pure JS; switch to slim if native modules struggle |
| Java | `eclipse-temurin:X-jre-alpine` | JRE-only, not JDK, in runtime stage |
| Need a shell for prod debug | `*-slim` variant | distroless = no `sh`, no `kubectl exec` shell |

**Pin the tag.** `:latest` is a moving target — pin to `python:3.12.7-slim` (full version) or digest (`python@sha256:...`) for CI reproducibility.

## Dockerfile anti-patterns (high → low severity)

| Pattern | Why it's wrong | Fix |
|---|---|---|
| Container runs as root | Default UID 0 inside the container; if it escapes namespace, host root | `RUN adduser -D app && USER app` |
| Secret in `ENV` or `ARG` | Baked into a layer; visible in `docker history` forever | BuildKit `--mount=type=secret,id=...`, read inside RUN |
| `COPY . .` before `COPY package.json && RUN install` | Any source change busts the dep-install cache | Copy lockfile, install, *then* copy source |
| `RUN apt-get install` without cleanup in same layer | `/var/lib/apt/lists/*` stays in the layer | `&& rm -rf /var/lib/apt/lists/*` in the same RUN |
| Single-stage build for compiled code | Ships SDK + source + build tools in final image | Multi-stage: builder + minimal runtime |
| No `HEALTHCHECK` | Orchestrators can't tell "starting" from "broken" | Add a cheap HTTP/exec check |
| `:latest` tag on base | Reproducibility gone; today's build ≠ yesterday's | Pin version, ideally digest |
| `EXPOSE` of ports the app doesn't bind | Misleading documentation; no security impact but noise | Match `EXPOSE` to actual `LISTEN` |
| `ADD https://...` for tarballs | `ADD` doesn't validate checksums | `RUN curl -fsSL ... | sha256sum -c -` |
| `chmod 777` anywhere | Almost always wrong | `chown` + specific mode |

## Multi-stage patterns

### Go (compiled, static)

```dockerfile
FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -ldflags='-s -w' -o /out/app ./cmd/app

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/app /app
USER nonroot:nonroot
ENTRYPOINT ["/app"]
```

### Node.js

```dockerfile
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM deps AS build
COPY . .
RUN npm run build && npm prune --omit=dev

FROM node:20-alpine
WORKDIR /app
RUN addgroup -S app && adduser -S -G app app
COPY --from=build --chown=app:app /app/dist ./dist
COPY --from=build --chown=app:app /app/node_modules ./node_modules
COPY --from=build --chown=app:app /app/package.json ./
USER app
EXPOSE 3000
HEALTHCHECK CMD wget -qO- http://localhost:3000/health || exit 1
CMD ["node", "dist/index.js"]
```

### Python

```dockerfile
FROM python:3.12-slim AS build
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
RUN useradd -r -u 1001 app
COPY --from=build /install /usr/local
COPY --chown=app:app . .
USER app
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## BuildKit features worth using

- `--mount=type=cache,target=/root/.cache/pip` — persistent dep cache between builds.
- `--mount=type=secret,id=npmrc,target=/root/.npmrc` — secret available only inside one RUN, never written to a layer.
- `--platform=$BUILDPLATFORM` + `--platform=$TARGETPLATFORM` — cross-compile from a fast builder.

Enable with `DOCKER_BUILDKIT=1` (or in `docker-bake.hcl`/GitHub Actions `buildx`).

## docker-compose review checklist

Run `compose_validator.py` first. Then read the file with this filter:

| Concern | What to look for |
|---|---|
| Healthchecks present | Every service that another service `depends_on` should have a `healthcheck:` + `condition: service_healthy` in the dependent's `depends_on` |
| Networks explicit | Backend network with `internal: true`, frontend network for public services; never rely on the default bridge |
| Volumes named, not bind | Bind mounts (`./data:/data`) leak host paths; use named volumes for state |
| `restart:` set | `unless-stopped` for prod-like, `no` for dev |
| Resource limits | `mem_limit`, `cpus` — without these one service can OOM the host |
| Env from file | `env_file: .env` over inline `environment:` for anything secret; `.env` in `.gitignore` |
| Pinned image tags | Same `:latest` rule applies |
| `docker-compose.override.yml` for dev | Hot-reload mounts, debug ports — keep dev concerns out of the prod compose |

## Proactive triggers

Flag these without being asked:

- **`FROM ...:latest`** → pin to a version tag, ideally a digest.
- **`COPY . .` before any dependency-install RUN** → cache bust. Reorder.
- **No `USER` instruction** → container runs as root. Add a non-root user.
- **Secrets in `ENV` / `ARG`** → BuildKit secret mounts. Never bake.
- **No `.dockerignore`** → at minimum exclude `.git`, `node_modules`, `__pycache__`, `.env`, `dist`, `build`.
- **`apt-get install` without `rm -rf /var/lib/apt/lists/*` in the same RUN** → cache stays in the layer.
- **Single-stage build for a compiled language** → multi-stage with distroless runtime.
- **Compose service `depends_on` without `condition: service_healthy`** → startup race; the dependent starts as soon as the container exists, not when it's ready.

## What this skill does NOT cover

- Container runtime internals (cgroups, namespaces, seccomp profiles beyond defaults). Image scanning beyond Dockerfile static checks — use Trivy/Grype.
- Registry workflows (push/pull/signing). See `do-registry-cleanup` for DigitalOcean registry hygiene.
- Kubernetes-specific manifest concerns. See `kubernetes-operations`.
