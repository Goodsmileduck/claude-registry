---
name: aws-cost-investigation
description: Diagnoses AWS cost spikes and audits accounts for ongoing waste. Cost Explorer + Cost & Usage Report query patterns, anomaly detection, the cost-trap inventory (forever log groups, NAT egress, unattached EBS/EIPs, idle ELBs, incomplete S3 multipart uploads, gp2/gp3 migration), commitment decision rules (Compute SP vs EC2 Instance SP vs RI), and the cost-allocation-tag activation trap. Use when working with AWS billing, "bill is up", `aws ce`, Cost Explorer, Cost and Usage Report, Savings Plans, Reserved Instances, NAT vs VPC endpoint trade-offs, or AWS cost optimization.
---

# AWS Cost Investigation

Operational skill for diagnosing AWS cost spikes and auditing for ongoing waste. The focus is on **diagnostic flow** (data-first, not guess-first) plus a **concrete trap inventory** with detection CLI for each.

## When to invoke

**Symptoms:**

- "The bill is up $X with no deploys" / "AWS bill spiked last month."
- An anomaly notification from AWS Cost Anomaly Detection.
- Cost Explorer dashboards show large `(no tag)` slices despite tagging policies.
- NAT Gateway charges growing month over month.
- Looking at a Savings Plan / Reserved Instance commitment decision.
- A general account audit ("find the waste").
- Designing a cost-allocation tagging strategy.

## Cross-cutting rules

1. **Data first, guesses never.** When asked to diagnose a spike, the first action is to query Cost Explorer. Do NOT guess "probably S3" or "probably NAT" without numbers. Naming a likely culprit without data is anti-pattern #1.
2. **Compare windows of equal length.** A 7-day spike compares to the prior 7 days, not month-to-date. A monthly spike compares to the same days of the prior month, not the full prior month.
3. **Never quote a specific dollar amount as a pricing fact.** AWS prices change. State relative magnitudes (Gateway endpoints are free; Interface endpoints are cheaper than NAT for high egress) and link to the [AWS pricing page](https://aws.amazon.com/pricing/) for current numbers when a precise answer is needed.
4. **Activation is the silent gate for tag-based analysis.** A tag on a resource is invisible to Cost Explorer / CUR until it's *activated* as a Cost Allocation Tag. See [Cost allocation tagging](#cost-allocation-tagging).
5. **Stop-the-bleeding before re-architecting.** When a runaway cost is identified, the first action is to cap it (set retention, delete idle, add a budget alarm) — not redesign the workload.

## Diagnostic flow for spikes

Run the steps in order. Don't skip ahead.

### Step 1 — Service-level diff

```bash
# Compare two equal-length windows (here: last 7d vs prior 7d)
END=$(date -u +%Y-%m-%d)
START=$(date -u -d '-7 days' +%Y-%m-%d)
PRIOR_END=$START
PRIOR_START=$(date -u -d '-14 days' +%Y-%m-%d)

for window in "$PRIOR_START $PRIOR_END" "$START $END"; do
  read s e <<<"$window"
  aws ce get-cost-and-usage \
    --time-period Start=$s,End=$e \
    --granularity DAILY \
    --metrics UnblendedCost \
    --group-by Type=DIMENSION,Key=SERVICE \
    --query 'ResultsByTime[].Groups[]' \
    --output json > /tmp/ce-$s.json
done
# Diff per-service totals between the two files to find the top growers.
```

Identify the top-growing service. Do NOT proceed to step 2 without this.

### Step 2 — Drill into the top service by usage type

```bash
aws ce get-cost-and-usage \
  --time-period Start=$START,End=$END \
  --granularity DAILY \
  --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Elastic Compute Cloud - Compute"]}}' \
  --group-by Type=DIMENSION,Key=USAGE_TYPE
```

Usage-type taxonomy worth recognizing:

| Usage type prefix | What it is |
|---|---|
| `BoxUsage:<instance>` | EC2 instance hours by instance type |
| `EBS:VolumeUsage*` / `EBS:SnapshotUsage` | Block storage / snapshots |
| `DataTransfer-Out-Bytes` | Egress to internet from this region |
| `DataTransfer-Regional-Bytes` | Cross-AZ within region |
| `NatGateway-Bytes` / `NatGateway-Hours` | NAT processing / hourly |
| `LoadBalancerUsage` / `LCUUsage` | ELB hourly / load balancer capacity units |
| `Requests-Tier1` / `Requests-Tier2` (S3) | S3 PUT/COPY vs GET/SELECT |
| `TimedStorage-*` (S3) | Storage by class |
| `CW:Requests` / `CW:GMD-Metrics` | CloudWatch API + custom metric storage |
| `<service>:Lambda-GB-Second` | Lambda compute-time |

### Step 3 — Resource-level attribution (opt-in)

`aws ce get-cost-and-usage-with-resources` returns per-resource costs but requires:

- Opt-in via Cost Explorer Settings page (one-time).
- Daily granularity for everything except EC2 (which supports hourly).
- 14-day lookback window only.

```bash
aws ce get-cost-and-usage-with-resources \
  --time-period Start=$START,End=$END \
  --granularity DAILY \
  --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Elastic Compute Cloud - Compute"]}}' \
  --group-by Type=DIMENSION,Key=RESOURCE_ID
```

For costs older than 14 days, the **CUR** is the right tool — see [CUR + Athena](#cost--usage-report-cur).

### Step 4 — Tag axis (if cost-allocation tags are activated)

```bash
aws ce get-cost-and-usage \
  --time-period Start=$START,End=$END \
  --granularity DAILY \
  --metrics UnblendedCost \
  --group-by Type=TAG,Key=team Type=DIMENSION,Key=SERVICE
```

If the response shows large `(no tag)` slices, the tag isn't activated — see [Cost allocation tagging](#cost-allocation-tagging).

### Step 5 — Sanity-check against AWS's anomaly detector

```bash
aws ce get-anomalies \
  --date-interval StartDate=$PRIOR_START,EndDate=$END
```

AWS Cost Anomaly Detection runs its own ML and may have already flagged the cause with `RootCauses` populated. Free service; underused.

## The cost-trap inventory

These are recurring sources of silent cost growth. Audit each in any account you don't know well.

### CloudWatch log groups with no retention

Lambda, ECS, CodeBuild, API Gateway all auto-create log groups with retention `null` = never expire.

```bash
aws logs describe-log-groups \
  --query 'logGroups[?retentionInDays==`null`].[logGroupName,storedBytes]' \
  --output table

# Fix: set retention org-wide (example: 30 days)
aws logs describe-log-groups --query 'logGroups[].logGroupName' --output text \
  | xargs -n1 -I{} aws logs put-retention-policy --log-group-name {} --retention-in-days 30
```

Going forward, Terraform pattern:

```hcl
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.x.function_name}"
  retention_in_days = 30
  # Create this resource BEFORE the Lambda — otherwise Lambda creates the group with null retention.
}
```

### Unattached EBS volumes

```bash
aws ec2 describe-volumes \
  --filters Name=status,Values=available \
  --query 'Volumes[].[VolumeId,Size,VolumeType,CreateTime]' \
  --output table
```

Volumes in `available` state bill at full storage cost. Common after EC2 termination with "delete on termination" not set. Snapshot then delete, or just delete if not needed.

### Old EBS snapshots (no lifecycle)

```bash
aws ec2 describe-snapshots --owner-ids self \
  --query 'Snapshots[?StartTime<=`2024-01-01`].[SnapshotId,StartTime,VolumeSize,Description]' \
  --output table
```

Snapshots accumulate. Use **Data Lifecycle Manager (DLM)** to set automated retention going forward; clean up old ones one-time.

### Unattached Elastic IPs

```bash
aws ec2 describe-addresses \
  --query 'Addresses[?AssociationId==`null`].[PublicIp,AllocationId,Domain]' \
  --output table
```

EIPs not attached to a running instance bill hourly. After AWS's 2024 pricing change, attached EIPs also bill — but unattached is always wasted.

### Idle Load Balancers

```bash
# ALBs and NLBs with low request count over the past 7 days
aws elbv2 describe-load-balancers \
  --query 'LoadBalancers[].[LoadBalancerArn,LoadBalancerName,Type]' \
  --output text \
  | while read arn name type; do
    requests=$(aws cloudwatch get-metric-statistics \
      --namespace AWS/ApplicationELB \
      --metric-name RequestCount \
      --dimensions Name=LoadBalancer,Value=$(echo $arn | cut -d/ -f2-) \
      --start-time $(date -u -d '-7 days' +%Y-%m-%dT%H:%M:%SZ) \
      --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
      --period 604800 --statistics Sum \
      --query 'Datapoints[0].Sum' --output text)
    echo "$name $type $requests"
  done
```

LBs with near-zero traffic are usually leftover from migrated/deleted services.

### NAT Gateway data egress

The single biggest silent killer for workloads in private subnets. Diagnostic:

```bash
# How many bytes did NAT GW process this month?
aws cloudwatch get-metric-statistics \
  --namespace AWS/NATGateway \
  --metric-name BytesOutToDestination \
  --start-time $(date -u -d '-30 days' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 86400 --statistics Sum \
  --dimensions Name=NatGatewayId,Value=<natgw-id>
```

**Fix:** route the traffic through VPC endpoints. See [NAT vs VPC endpoint](#nat-vs-vpc-endpoint).

### S3 incomplete multipart uploads

Failed/abandoned uploads bill at full storage cost indefinitely. No CLI lists "incomplete uploads across all buckets" — must check per-bucket:

```bash
aws s3api list-multipart-uploads --bucket <bucket> \
  --query 'Uploads[?Initiated<=`2024-01-01`].[Key,UploadId,Initiated]'
```

**Fix going forward:** lifecycle rule per bucket:

```json
{
  "Rules": [{
    "ID": "abort-incomplete-mpu",
    "Status": "Enabled",
    "Filter": {},
    "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 7 }
  }]
}
```

### gp2 → gp3 migration

gp3 is cheaper than gp2 at the same baseline performance (3000 IOPS / 125 MB/s included). Accounts pre-2021 may still default to gp2. Detection:

```bash
aws ec2 describe-volumes \
  --filters Name=volume-type,Values=gp2 \
  --query 'Volumes[].[VolumeId,Size,State,AvailabilityZone]' \
  --output table

# In-place migration (no downtime, online)
aws ec2 modify-volume --volume-id <vol-id> --volume-type gp3
```

### Idle RDS / Aurora

```bash
# Instances with zero active connections for a week
aws rds describe-db-instances --query 'DBInstances[].[DBInstanceIdentifier,DBInstanceClass,Engine]' --output text \
  | while read id class engine; do
    conns=$(aws cloudwatch get-metric-statistics \
      --namespace AWS/RDS \
      --metric-name DatabaseConnections \
      --dimensions Name=DBInstanceIdentifier,Value=$id \
      --start-time $(date -u -d '-7 days' +%Y-%m-%dT%H:%M:%SZ) \
      --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
      --period 604800 --statistics Maximum \
      --query 'Datapoints[0].Maximum' --output text)
    echo "$id $class $engine $conns"
  done
```

Look for `0` or `0.0`. Common in dev/staging accounts.

### Provisioned-mode DynamoDB with low utilization

DDB Provisioned with auto-scaling can still over-provision. If WCU/RCU utilization is consistently <50%, switch to On-Demand. Diagnostic via CloudWatch `ConsumedWriteCapacityUnits` / `ProvisionedWriteCapacityUnits` ratio.

### Cross-region data transfer (accidental)

Symptom: a `DataTransfer-Regional-Bytes` or `DataTransfer-OutBytes` line item in Cost Explorer with no obvious source. Common causes:

- S3 replication misconfigured (bucket replicating to itself or to a region it shouldn't).
- CloudWatch cross-account logs.
- VPC peering between regions used heavily by an unintended service.

Drill: filter Cost Explorer by USAGE_TYPE matching `*DataTransfer*` and group by REGION + LINKED_ACCOUNT.

## NAT vs VPC endpoint

The decision rule that consolidates many cost issues into one trade-off.

| Path | Cost shape |
|---|---|
| **NAT Gateway** | Hourly per AZ + per-GB processing (the per-GB charge dominates) |
| **Gateway VPC Endpoint** (S3, DynamoDB) | Free — no hourly, no per-GB |
| **Interface VPC Endpoint** (ECR, Secrets Manager, SSM, CloudWatch Logs, etc.) | Hourly per endpoint per AZ + per-GB (lower per-GB than NAT) |

**Decision rule:**

- **S3 or DynamoDB egress from a VPC: always use the Gateway endpoint.** Free, no downside.
- **ECR pulls, Secrets Manager, SSM, CloudWatch Logs: use Interface endpoints if egress volume is non-trivial.** Crossover is usually tens of GB/month — below that, NAT is cheaper because Interface endpoints have a fixed hourly charge.
- **Random outbound HTTPS to third-party APIs: stays on NAT.** No endpoint exists.

**ECR specifically requires three endpoints** for image pulls to fully bypass NAT:

1. `com.amazonaws.<region>.ecr.api` — Interface, for authentication
2. `com.amazonaws.<region>.ecr.dkr` — Interface, for image-pull operations
3. `com.amazonaws.<region>.s3` — Gateway, because ECR stores layers in S3 and the pull hits S3 directly

Without all three, the pull falls back to NAT for the missing leg.

**Gateway endpoint setup:**

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxx \
  --service-name com.amazonaws.us-east-1.s3 \
  --route-table-ids rtb-aaa rtb-bbb
# Associate with all route tables for subnets that should use the endpoint.
```

**Interface endpoint setup:**

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxx \
  --vpc-endpoint-type Interface \
  --service-name com.amazonaws.us-east-1.ecr.api \
  --subnet-ids subnet-aaa subnet-bbb \
  --security-group-ids sg-xxx \
  --private-dns-enabled
# Private DNS Names must be enabled so the standard AWS DNS names resolve to the endpoint.
```

Security group on the endpoint must allow 443 from the source security groups.

## Commitment decision: Savings Plans vs Reserved Instances

Modern AWS commitment landscape (in order of flexibility, lowest discount first):

| Commitment | Scope | Resources covered | Notes |
|---|---|---|---|
| **Compute Savings Plan** | $/hour commit, any region, any instance family | EC2, Fargate, Lambda | Most flexible; recommended default. |
| **EC2 Instance Savings Plan** | $/hour, specific instance family in a region | EC2 only | Mid discount; locks family. |
| **Standard RI** (EC2/RDS/ElastiCache/etc.) | Specific instance type in a region/AZ | One service | Highest discount; least flexible. |
| **Convertible RI** | Specific instance type, can be exchanged | EC2 | Mostly obsolete vs Compute SP — don't buy new ones. |

**Decision rule:**

- **Baseline EC2/Fargate/Lambda compute that runs 24/7:** Compute Savings Plan, 1-year, No Upfront. Highest flexibility, ~25–30% discount range (verify current).
- **RDS, ElastiCache, Redshift, OpenSearch:** Standard RIs. Savings Plans don't cover these.
- **Predictable instance family but flexible region/size:** EC2 Instance SP.
- **Don't commit at >70% of steady-state.** A SP/RI you don't use is pure waste — you pay the committed rate whether you use the capacity or not. Run `aws ce get-savings-plans-utilization` on existing commitments before buying more.

**Term length:** 1-year is the default for new commitments. 3-year only when the workload is provably stable and won't migrate (no impending platform change, no architecture rewrites scheduled).

```bash
# Check existing SP utilization
aws ce get-savings-plans-utilization \
  --time-period Start=$(date -u -d '-30 days' +%Y-%m-%d),End=$(date -u +%Y-%m-%d)

# Get AWS's recommendation
aws ce get-savings-plans-purchase-recommendation \
  --savings-plans-type COMPUTE_SP \
  --term-in-years ONE_YEAR \
  --payment-option NO_UPFRONT \
  --lookback-period-in-days SIXTY_DAYS
```

## Cost allocation tagging

The activation trap is the single biggest blocker to per-team / per-env cost attribution.

**The flow:**

1. Tag resources with `team`, `env`, `cost-center`, etc. — usually via Terraform `default_tags` on the provider.
2. **Activate each tag** in Billing → Cost Allocation Tags → User-Defined Cost Allocation Tags. Without this step, the tag is invisible to Cost Explorer and CUR.
3. Wait 24–48 hours for the tag to start appearing in cost data.

```bash
# Activate a tag via CLI (uses the Cost Explorer API)
aws ce update-cost-allocation-tags-status \
  --cost-allocation-tags-status TagKey=team,Status=Active TagKey=env,Status=Active
```

**Critical retroactivity rule:** activation applies **forward only**. Costs incurred BEFORE the activation date are not retroactively tagged. The "before" period will continue to show `(no tag)` for that dimension permanently.

**Residual `(no tag)` after activation** — some costs never carry tags regardless:

- AWS Support charges
- Marketplace charges
- Data transfer between regions (untagged)
- Some AWS-managed resources (default VPC NAT gateway, AWS-managed KMS keys)
- Untagged historical data (per the retroactivity rule)

A residual of ~5–15% `(no tag)` is normal. >50% means either tags aren't activated or many resources are untagged.

**Forward-going enforcement:**

- AWS Config rule `required-tags` — flags non-compliant resources.
- SCP at the Organizations level denying `aws:RequestTag/team = null` on resource creation.
- Terraform `default_tags { tags = { team = "..." } }` at the provider — automatic for everything that supports it (which is most resources, but not all).

## Cost & Usage Report (CUR)

Use CUR over Cost Explorer when:

- Lookback >14 months (Cost Explorer max).
- Custom grouping not in Cost Explorer (resource_id × usage_type × tag, simultaneously).
- Building dashboards or pipelines on top of cost data.

Setup (one-time):

1. Billing → Data Exports → Create Export → Standard data export → CUR 2.0 (the modern format; the legacy CUR is being deprecated).
2. Lands as Parquet in an S3 bucket, partitioned by `billing_period`.
3. Optionally enable Athena integration — AWS generates a CloudFormation template that creates the table + Glue crawler.

Common Athena queries:

```sql
-- Top 20 resource IDs by cost, last month
SELECT line_item_resource_id, SUM(line_item_unblended_cost) AS cost
FROM cur_database.cur_table
WHERE billing_period = DATE '2026-04-01'
  AND line_item_resource_id <> ''
GROUP BY line_item_resource_id
ORDER BY cost DESC
LIMIT 20;

-- Untagged cost by service
SELECT line_item_product_code, SUM(line_item_unblended_cost) AS cost
FROM cur_database.cur_table
WHERE billing_period = DATE '2026-04-01'
  AND (resource_tags_user_team IS NULL OR resource_tags_user_team = '')
GROUP BY line_item_product_code
ORDER BY cost DESC;
```

Tag columns are flattened as `resource_tags_user_<tag_key>` in CUR 2.0. Tag activation in Billing is still required.

## Anti-patterns checklist

- ❌ Diagnosing a spike by guessing services before running Cost Explorer.
- ❌ Comparing a 7-day spike against month-to-date totals. Use equal windows.
- ❌ Caching `node_modules` (or similar) in S3 instead of fixing the underlying download path — wasted effort vs the real fix (VPC endpoint, cache the download cache, etc).
- ❌ Buying a Savings Plan or RI at 100% of current usage. Target 70% of steady-state.
- ❌ Buying Convertible RIs. Compute Savings Plan strictly dominates for any new commitment.
- ❌ Tagging resources without activating the tag in Billing. Tags are invisible to Cost Explorer / CUR until activated.
- ❌ Expecting tag activation to retroactively label pre-activation costs. It doesn't.
- ❌ Using NAT for routine S3 / DynamoDB egress. Gateway endpoints are free.
- ❌ CloudWatch log groups with no retention. Set retention at the Terraform level, before the producer creates the group.
- ❌ Quoting specific dollar amounts as pricing facts in skill content. State relative magnitudes; link to AWS pricing for precise current numbers.

## Cross-skill notes

- For VPC endpoint setup details beyond the cost trade-off (subnet placement, security group rules, DNS resolution), see `aws-vpc-networking` (when built) — this skill states the rule; that skill goes deep on setup.
- For CodeBuild + VPC endpoint patterns (ECR pulls, S3 artifact buckets), see `aws-codepipeline-codebuild`.
- For Terraform-managed log groups with retention set at the IaC level, see `terraform-workflows`.
- For Lambda log retention specifically (the auto-creation trap), see `aws-lambda-operations` (when built).
