---
name: aws-codepipeline-codebuild
description: Authors and debugs AWS CodePipeline + CodeBuild workflows — pipeline v1 vs v2 (triggers, variables), source providers via CodeStar Connections, artifact handoff, buildspec.yml authoring, IAM service roles, ECR pull permissions, VPC build environments, S3/local caching strategies, Lambda invoke action callback pattern, and manual approval setup. Use when working with AWS CodePipeline, AWS CodeBuild, buildspec.yml, CodeStar Connections, pipeline service roles, build VPC config, or "CodeBuild can't pull image" / "Lambda action hangs" debugging.
---

# AWS CodePipeline + CodeBuild

These two services are tightly coupled in practice: CodePipeline orchestrates stages, CodeBuild does the build action. Most friction lives at the boundary (artifact handoff, IAM roles, source authentication).

## When to invoke

**Symptoms:**

- CodeBuild fails immediately with `CannotPullContainerError: pull access denied` for an ECR image.
- A Lambda invoke action in CodePipeline hangs for 30+ minutes even though the function returns quickly.
- `npm ci` / `pip install` / Maven dependency resolution dominates build time and the configured cache isn't helping.
- The source action shows OAuth-style GitHub auth and we want to migrate to the modern GitHub App.
- Cross-account deploy stage fails with assume-role errors.
- Build succeeds but the next stage receives empty / missing artifacts.
- A `buildspec.yml` referenced in CodeBuild silently runs without env vars from Parameter Store / Secrets Manager.
- Pipeline service role attached an AWS-managed policy and we want to scope it down.

## Cross-cutting rules

1. **Always set `imagePullCredentialsType` explicitly when using ECR base images.** Default (`CODEBUILD`) requires an ECR repository policy trusting `codebuild.amazonaws.com`. Setting `SERVICE_ROLE` pulls under the project's IAM role — usually what you want. See [ECR base images](#ecr-base-images).
2. **Lambda actions are async from CodePipeline's perspective.** The Lambda MUST call `PutJobSuccessResult` / `PutJobFailureResult` before returning. Returning is not signaling. See [Lambda invoke action](#lambda-invoke-action).
3. **Never edit `buildspec.yml` and the source CodeBuild project's `buildspec` field at the same time** — one shadows the other. The inline `buildspec` field on the project (when set) overrides the file in source. Pick one; document which.
4. **Pipeline service role and CodeBuild service role are different roles.** Pipeline's role does NOT confer permissions on the build. Each build action has its own service role, set on the CodeBuild project (NOT the pipeline action).
5. **Artifact bucket is encrypted by default.** Cross-account artifact reads require the destination account's role to have decrypt access on the source account's KMS key — and the key policy must grant cross-account use. Symptom of missing this: `AccessDenied` on artifact download even when bucket policy looks correct.

## CodePipeline v1 vs v2

Two pipeline types coexist. `pipelineType: V1` is the original; `V2` adds capabilities that often warrant upgrading.

| Feature | V1 | V2 |
|---|---|---|
| Pipeline triggers with filters (branch, tag, file path) | No — runs on every source change | Yes — `triggers` block with `gitConfiguration` filters |
| Pipeline-level variables (resolved at start, passed to actions) | No | Yes — `variables` block |
| Parallel actions in same stage | Yes (in both) | Yes |
| Queued execution mode | No (replaces in-flight) | Yes (`executionMode: QUEUED`) |
| Action types | All | All |
| Pricing | Per active pipeline-month | Per action-execution-minute (often cheaper for low-traffic, more for high) |

**Upgrade path:** there is no in-place V1 → V2 conversion. Create a new pipeline with `pipelineType: V2`, point at the same source, retire the V1 once parallel-run validates. Existing V1 pipelines continue to work.

**When V2 is worth it:**
- You're triggering one pipeline from many branches and want per-branch filtering — V2 triggers eliminate the "one pipeline per branch" anti-pattern.
- You want to pass a commit SHA or PR number through multiple stages as a variable.
- You want PR-specific pipelines (V2's pull request triggers).

**When V1 is fine:** simple "build on main push, deploy to prod" pipelines with no per-branch logic.

## Source providers

| Provider | Use | Notes |
|---|---|---|
| **CodeStar Connections** (GitHub, Bitbucket, GitLab) | Default for third-party Git | GitHub App, modern. Action type: `CodeStarSourceConnection`. |
| GitHub via OAuth token | Legacy | Deprecated. New pipelines should not use it. |
| S3 | Drop-zip-into-bucket triggers | `pollForSourceChanges: false` + S3 event notifications via CloudWatch. |
| CodeCommit | Internal AWS Git | **Deprecated for new accounts as of 2024-07.** Existing customers keep access; do not adopt for greenfield. |
| ECR (image push triggers pipeline) | Container-as-source | `pipelineType: V2` recommended for image tag filters. |

### CodeStar Connections setup

Connection is a separate account-level resource (Console → Developer Tools → Settings → Connections, or `aws codestar-connections create-connection`). The first-time flow requires browser-based authorization by a GitHub org admin — there's no purely API path.

Once created, the connection ARN is reused across pipelines. In the source action:

```yaml
# (CloudFormation snippet)
- Name: Source
  ActionTypeId:
    Category: Source
    Owner: AWS
    Provider: CodeStarSourceConnection
    Version: '1'
  Configuration:
    ConnectionArn: arn:aws:codestar-connections:us-east-1:1234:connection/abc-def
    FullRepositoryId: my-org/my-repo
    BranchName: main
    DetectChanges: 'true'
  OutputArtifacts:
    - Name: SourceArtifact
```

Pipeline service role needs `codestar-connections:UseConnection` on the connection ARN.

## Artifact handoff

Every action declares `InputArtifacts` and/or `OutputArtifacts` by name. The pipeline ships artifacts via a versioned S3 bucket (set at the pipeline level: `artifactStore.location` and `artifactStore.encryptionKey`).

**The primary-artifact rule:**

A build action emits ONE primary output artifact. The buildspec's `artifacts` block defines what's in it:

```yaml
# buildspec.yml
artifacts:
  files:
    - '**/*'
  base-directory: dist
  discard-paths: no
```

For multi-artifact builds, use `secondary-artifacts`:

```yaml
artifacts:
  secondary-artifacts:
    app:
      files: ['**/*']
      base-directory: dist
    config:
      files: ['*.yml']
      base-directory: config
```

In the CodePipeline build action, map each secondary artifact name to a named output:

```yaml
OutputArtifacts:
  - Name: AppArtifact     # maps to 'app' from buildspec
  - Name: ConfigArtifact  # maps to 'config'
```

Without `secondary-artifacts`, the entire `artifacts.files` set goes to the primary output artifact. Common trap: `files: ['**/*']` with no `base-directory` includes `node_modules` — huge artifact, slow handoff.

**Cross-account artifact reads:**

When stage N is in account A and stage N+1 is in account B (cross-account deploy pattern), B must have:

1. IAM role in B that trusts A's pipeline role.
2. KMS key permissions on A's encryption key — A's key policy must allow B's role to `kms:Decrypt`.
3. Bucket policy on A's artifact bucket allowing B's role to read.

Forgetting (2) is the most common cause of `AccessDenied` errors that look like bucket-policy issues.

## buildspec.yml essentials

Current version is `0.2`. The shape:

```yaml
version: 0.2

env:
  variables:
    NODE_ENV: production
  parameter-store:
    DB_PASSWORD: /prod/db/password         # Parameter Store name
  secrets-manager:
    API_KEY: prod/api:apikey               # SecretId:JSONKey
  exported-variables:
    - GIT_SHA                              # exposed to downstream pipeline actions

phases:
  install:
    runtime-versions:
      nodejs: 20
      python: 3.11
    commands:
      - npm ci
  pre_build:
    on-failure: ABORT                      # ABORT (default) | CONTINUE
    commands:
      - export GIT_SHA=$(echo $CODEBUILD_RESOLVED_SOURCE_VERSION | head -c 8)
  build:
    commands:
      - npm run build
  post_build:
    commands:
      - npm test

artifacts:
  files: ['dist/**/*']
  base-directory: .
  discard-paths: no

reports:
  test-results:
    files: ['junit.xml']
    file-format: JUNITXML
    base-directory: reports

cache:
  paths:
    - '/root/.npm/**/*'      # cache the npm download cache, NOT node_modules
```

### env block — three sources

| Source | Use | Permission needed |
|---|---|---|
| `variables` | Plain text, non-secret | None |
| `parameter-store` | Systems Manager Parameter Store | `ssm:GetParameters` on the parameter ARN |
| `secrets-manager` | Secrets Manager | `secretsmanager:GetSecretValue` on the secret ARN; KMS decrypt if customer key |
| Project `environmentVariables` (not in buildspec) | Set on the project, override buildspec | Same as the sources they reference |

**Never use `variables` for secrets** — values appear in build logs and the CodeBuild console.

### phases — failure semantics

By default a failed command aborts the phase. `on-failure: CONTINUE` keeps going within the phase but still marks the phase failed at the end. Useful in `post_build` for cleanup that should always run.

For unconditional cleanup, use the `finally` key (per-phase):

```yaml
phases:
  build:
    commands:
      - run_tests.sh
    finally:
      - upload_test_logs.sh    # runs even if commands fail
```

### Exported variables

`exported-variables` in the buildspec env block exposes the variable's value to downstream CodePipeline actions in V2 pipelines:

```yaml
# In V2 pipeline action, reference as:
Configuration:
  DeployVersion: '#{BuildOutput.GIT_SHA}'
```

V1 pipelines can't consume exported variables this way; use S3 artifact files for handoff.

## CodeBuild project IAM and source roles

Each CodeBuild project has its own **service role**. This role is what the build container runs as — for AWS API calls in the buildspec, for ECR pulls (when `SERVICE_ROLE` mode), for KMS decrypt, for VPC ENI provisioning.

**Minimum service role policy (template):**

- `logs:CreateLogGroup`, `CreateLogStream`, `PutLogEvents` on `arn:aws:logs:region:acct:log-group:/aws/codebuild/<project>:*`
- `s3:GetObject`, `PutObject`, `GetObjectVersion` on the pipeline artifact bucket
- `s3:GetBucketLocation` on the artifact bucket
- For VPC: `ec2:CreateNetworkInterface`, `DescribeNetworkInterfaces`, `DeleteNetworkInterface`, `DescribeSubnets`, `DescribeSecurityGroups`, `DescribeVpcs`, `CreateNetworkInterfacePermission` (last one scoped to the project)
- For parameter-store env: `ssm:GetParameters` on the specific parameter ARNs
- For secrets-manager env: `secretsmanager:GetSecretValue` on the specific secret ARNs
- For ECR base image (SERVICE_ROLE mode): `ecr:GetAuthorizationToken` (resource `*`), `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` on the repo

The AWS-managed `AWSCodeBuildServiceRolePolicy` exists but is broader than most projects need. Custom-author for production.

## ECR base images

Two paths via `imagePullCredentialsType`:

| Mode | Pulls under | Setup |
|---|---|---|
| `CODEBUILD` (default) | CodeBuild service principal | ECR repo policy: `Principal: { Service: codebuild.amazonaws.com }` with the three ECR actions |
| `SERVICE_ROLE` | Project's service role | Service role: three ECR actions on the repo; optionally ECR repo policy for the role ARN |

**Recommended for private images:** `SERVICE_ROLE` mode. Reasons:
- Auditable in CloudTrail under the role ARN.
- Same IAM model as everything else the build does.
- Doesn't require a separate ECR repository policy if the role's policy is sufficient.

**Symptom of misconfigured ECR pull:**

```
CLIENT_ERROR: Unable to pull customer's container image.
CannotPullContainerError: pull access denied for <acct>.dkr.ecr.<region>.amazonaws.com/<repo>:tag
```

This is always an IAM / repo policy issue, never a network issue at this layer. The CodeBuild host has internet access to ECR by default; the auth step fails because the principal lacks `ecr:GetAuthorizationToken`.

## VPC build environments

A CodeBuild project in a VPC provisions an ENI per build in the configured subnets. Implications:

- **~30 seconds of ENI provisioning latency** added to every build. For short builds, this dominates wall time.
- **No public IPv4 by default.** If the build needs internet (npm/pip/Docker Hub), either: NAT Gateway (charges per GB), or VPC endpoint for the AWS services in use (S3, ECR, Secrets Manager).
- **VPC endpoint for S3 is free and removes NAT charges** for artifact upload/download. Recommended.
- **Security group rules:** outbound 443 to ECR/S3/Secrets Manager VPC endpoints (or to NAT). No inbound rules needed; the ENI is not addressable.

**When to put builds in a VPC:**
- Build needs to reach a private database/service for integration tests.
- Compliance requires no public internet egress.

**When NOT to:** the build only needs public registries and AWS APIs. The ENI overhead isn't worth it.

## Caching strategies

Cache configuration is split between the **project** and the **buildspec**:

```text
Project (cache.type, cache.location, cache.modes)
  +
buildspec (cache.paths)
  = effective cache
```

| Type | Project config | Buildspec | Notes |
|---|---|---|---|
| **No cache** | `cache.type: NO_CACHE` | — | Default. |
| **S3** | `cache.type: S3`, `cache.location: bucket/prefix` | `cache.paths: [...]` | Multi-build, durable. Slow for many small files. |
| **Local** | `cache.type: LOCAL`, `cache.modes: [...]` | `cache.paths: [...]` for CUSTOM | Per-host, best-effort. Near-instant when warm. |

**Local cache modes** (combinable):

- `LOCAL_SOURCE_CACHE` — git history kept; cuts checkout time on repeat builds.
- `LOCAL_DOCKER_LAYER_CACHE` — Docker layer cache; requires `privilegedMode: true`. Big win for `docker build`-based pipelines.
- `LOCAL_CUSTOM_CACHE` — Paths from buildspec's `cache.paths` kept on host between builds.

**The `node_modules` trap:**

Caching `node_modules/**/*` in **S3** is usually slower than `npm ci` on fresh, because S3 cache serializes path restore. Better: cache `~/.npm` (the npm download cache) so `npm ci` does a clean install but skips the network. Or use local cache with `LOCAL_CUSTOM_CACHE`.

Same pattern applies to pip (`~/.cache/pip`), Maven (`~/.m2`), Gradle (`~/.gradle`).

## Lambda invoke action

The CodePipeline → Lambda action is asynchronous. CodePipeline marks the action `InProgress` until the Lambda function explicitly reports back via the SDK:

```python
import boto3

codepipeline = boto3.client('codepipeline')

def lambda_handler(event, context):
    job_id = event['CodePipeline.job']['id']
    try:
        # do work
        codepipeline.put_job_success_result(jobId=job_id)
    except Exception as e:
        codepipeline.put_job_failure_result(
            jobId=job_id,
            failureDetails={'type': 'JobFailed', 'message': str(e)}
        )
```

**Failure mode:** function returns without posting a result. Pipeline hangs until the action timeout (1 hour default, 7 days max). Logs show the Lambda completed successfully; pipeline shows the action stuck.

**IAM (two roles):**

- Lambda execution role: `codepipeline:PutJobSuccessResult`, `codepipeline:PutJobFailureResult` (resource `*` — job ID is opaque).
- Pipeline service role: `lambda:InvokeFunction` on the function ARN.

**Long-running work (>15min):**

Lambda max execution is 15 minutes. For longer work, return without posting, store the `jobId` (DynamoDB, Step Functions task token, SQS), and have a separate trigger call `PutJobSuccessResult` when the work finishes. This is the **continuation token** pattern — the action stays `InProgress` across multiple Lambda invocations.

## Manual approval action

```yaml
- Name: Approve
  ActionTypeId:
    Category: Approval
    Owner: AWS
    Provider: Manual
    Version: '1'
  Configuration:
    NotificationArn: arn:aws:sns:us-east-1:1234:pipeline-approvals
    ExternalEntityLink: https://app.example.com/staging
    CustomData: 'Verify the staging deploy before approving prod.'
```

**Required permission gotcha:** the pipeline service role needs `sns:Publish` on the notification topic. Without it, the action runs but no one gets notified — and you'll never catch it because the action shows the expected `Pending` state.

Approvers need `codepipeline:PutApprovalResult` on the pipeline ARN. Don't grant `codepipeline:*` to "anyone who might need to approve" — scope to the specific pipeline.

## Anti-patterns checklist

- ❌ Storing secrets in the project's `environmentVariables` as `PLAINTEXT`. Use `PARAMETER_STORE` or `SECRETS_MANAGER`.
- ❌ `artifacts.files: ['**/*']` with no `base-directory`. Ships `node_modules`, `.git`, build temp dirs.
- ❌ Lambda invoke action handler that doesn't call `PutJobSuccessResult` / `PutJobFailureResult`.
- ❌ Using `imagePullCredentialsType: CODEBUILD` for a private ECR image without an ECR repo policy trusting `codebuild.amazonaws.com`.
- ❌ Caching `node_modules` in S3. Cache `~/.npm` (or equivalent download cache) instead.
- ❌ VPC build for a project that only needs public registries — pays ENI latency for no benefit.
- ❌ Pipeline service role with the AWS-managed `AWSCodePipelineServiceRole` for production — over-broad.
- ❌ One V1 pipeline per branch when V2 triggers with branch filter would do.
- ❌ Editing the inline `buildspec` field on the project while the source repo also has a `buildspec.yml`. One wins silently; pick one.
- ❌ Manual approval action without `sns:Publish` on the pipeline service role. Approvers are never notified.

## Cross-skill notes

- For "why we might prefer GitHub Actions over CodePipeline" decision context, see the `github-actions-pipelines` skill — OIDC to AWS is often cleaner from GH Actions than CodePipeline's cross-account dance.
- For Terraform-managed pipelines, the rules in `terraform-workflows` (plan-then-apply, never bump pins in passing) apply — pipeline + project definitions are load-bearing.
- For ECR base images stored in a registry separate from the build account, the cross-account ECR access pattern is the same as the cross-account artifact-bucket pattern documented above.
