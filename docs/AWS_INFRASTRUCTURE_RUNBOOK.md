# AWS Infrastructure — CPU-Credit Incident, the m7i-flex Resize, and the ECS Runbook

**Written 2026-09-17.** Covers three things done in one session, plus the runbook you will
actually come back for. If you are here to **restart the webhook services, jump to
[Part 6](#part-6--runbook-stopping-and-restarting-the-ecs-services)** — everything before it is
the reasoning, not the procedure.

Two AWS accounts' worth of things live in `us-east-1` here, and they are unrelated:

| Stack | What it is | Where it runs |
|---|---|---|
| **StockAI** | this repository — 15 containers, Postgres, Redis, Nginx | one EC2 instance, `i-0fb547ee9c6e15739` |
| **webhook-integration** | a Klaviyo webhook receiver, nothing to do with StockAI | ECS Fargate + ALB + RDS MySQL |

Confusing these two is the single most expensive mistake available in this document. The ECS
cluster is **not** StockAI, and StockAI does **not** run on Fargate.

---

## Part 1 — Why the instance kept dying: t3 credit exhaustion, not OOM

**Symptom.** The instance became unreachable and rebooted itself repeatedly — five times in
four days:

```
2026-09-14 19:47 UTC
2026-09-15 22:47 UTC
2026-09-16 05:41 UTC
2026-09-16 22:40 UTC
2026-09-17 20:52 UTC
```

**What it was not.** `journalctl -b -1` showed **zero** OOM kills and **zero** kernel panics
across every boot. The last line written before each freeze was
`rtkit-daemon: The canary thread is apparently starving` — which is the *tell*, not the cause: a
realtime-priority watchdog thread failing to get scheduled is exactly what CPU starvation looks
like from inside the box.

**What it was.** `t3.medium` in `unlimited` mode, out of credits and pinned at the surplus cap:

| Metric | Value | Meaning |
|---|---|---|
| `CPUCreditBalance` | `0.0` for 5+ days | nothing banked, nothing left to spend |
| `CPUSurplusCreditBalance` | `576`, flat since 2026-09-14 04:55 PDT | **at the cap** |

576 is not an arbitrary number — it is the t3.medium maximum surplus, `24 h x 24 credits/hr`.
This is the part that surprises people: **reaching the surplus cap throttles you to baseline
anyway, `unlimited` mode notwithstanding.** `unlimited` buys you the right to *pay* for surplus
credits; it does not buy unbounded surplus. At the cap, the instance is clamped to baseline,
which for a t3.medium is **20% of 2 vCPU = 0.4 vCPU**.

Measured demand over the same window was **1.08 vCPU mean, 1.92 vCPU peak** — roughly **2.7x**
the 0.4 vCPU the instance was being allowed. A machine asked to do 2.7x what it is permitted to
do does not run slowly; it stops responding, and the health checks that are supposed to notice
are themselves starved.

### A measurement error worth not repeating

The first pass at "how much CPU does this thing actually want" used CloudWatch `Average` over
6-hour buckets, and produced a comfortably low number. That statistic averages the underlying
5-minute datapoints, so a period of hard throttling reads as *low demand* rather than as
*suppressed demand* — the metric measures what the instance was allowed to consume, not what it
wanted. Recomputing with `Sum` over 1-hour buckets gave the 1.08 / 1.92 figures above, which are
the ones the resize decision was actually made on.

**Generalisation:** on a throttled resource, utilisation metrics are an output of the throttle,
not evidence about the load. Measure the thing being denied (credit burn, queue depth, run-queue
length), not the thing being rationed.

---

## Part 2 — The cost picture, and why m7i-flex.large

Actuals over a 14 days, extrapolated to a month (**~$173/mo total across both stacks**):

| Line item | ~$/mo | Stack |
|---|---|---|
| Fargate vCPU + memory | 56.39 + 12.38 | webhook |
| `BoxUsage:t3.medium` | 28.74 | StockAI |
| `PublicIPv4` | 20.92 | both |
| `LoadBalancer` (ALB) | 15.67 | webhook |
| **`CPUCredits:t3`** | **13.85** | StockAI |
| RDS `db.t3.micro` + storage | 11.84 + 5.54 | webhook |
| EBS `gp3` | 7.64 | StockAI |

The `CPUCredits:t3` line is the one that reframes the decision. The honest price of the
t3.medium was not $28.74 — it was **$42.59/mo**, because the instance had been continuously
buying surplus credits it then could not even use once it hit the cap. Paying a credit surcharge
*and* being throttled is the worst quadrant of the burstable model.

On-demand alternatives (us-east-1, verified at the time):

| Type | vCPU | RAM | ~$/mo | Note |
|---|---|---|---|---|
| t3.medium (current) | 2 burst | 4 GiB | 28.74 **+ 13.85 credits** | the failing configuration |
| t3.large | 2 burst | 8 GiB | 61 | same failure mode, higher baseline |
| t3.xlarge | 4 burst | 16 GiB | 121 | over-provisioned |
| **m7i-flex.large** | **2 dedicated** | **8 GiB** | **70** | **chosen** |
| m6i.large | 2 dedicated | 8 GiB | 70 | same price, older silicon |
| m7i-flex.xlarge | 4 | 16 GiB | 140 | headroom not yet justified |

**Chosen: `m7i-flex.large`.** Real delta is **~$27/mo** over what the t3 was truly costing, not
the ~$40 the sticker prices suggest. It doubles RAM (4 -> 8 GiB, and the old box was sitting at
120 MB free), moves from Skylake-era burstable to Sapphire Rapids (`Xeon Platinum 8488C`), and —
the actual point — removes the credit-cap cliff that was causing the reboots. `m6i.large` costs
the same but on older silicon, so there was no reason to pick it.

---

## Part 3 — The resize, as performed

An instance-type change requires a stop/start. Three things were verified **before** stopping,
because a stopped instance that cannot start again is a much worse problem than a slow one:

```bash
I=i-0fb547ee9c6e15739
AZ=$(aws ec2 describe-instances --instance-ids $I --region us-east-1 \
      --query 'Reservations[0].Instances[0].Placement.AvailabilityZone' --output text)

# 1. Is the target type even offered in THIS AZ?  (m7i-flex is not in every AZ)
aws ec2 describe-instance-type-offerings --location-type availability-zone \
  --filters "Name=instance-type,Values=m7i-flex.large" "Name=location,Values=$AZ" \
  --region us-east-1 --output table

# 2. Will the containers come back on their own?
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  'systemctl is-enabled docker nginx; \
   docker ps -a --format "{{.Names}}" | while read n; do \
     echo "$n $(docker inspect -f "{{.HostConfig.RestartPolicy.Name}}" $n)"; done'
```

Answers were: offered in `us-east-1d`; `docker` and `nginx` both `enabled`; all 15 StockAI
containers on `unless-stopped`. Driver compatibility needed no action — `t3` and `m7i-flex` are
both ENA + NVMe-required, so the existing AMI already carries what the new type needs.

```bash
aws ec2 stop-instances  --instance-ids $I --region us-east-1
aws ec2 wait instance-stopped --instance-ids $I --region us-east-1
aws ec2 modify-instance-attribute --instance-id $I --region us-east-1 \
  --instance-type '{"Value": "m7i-flex.large"}'
aws ec2 start-instances --instance-ids $I --region us-east-1
aws ec2 wait instance-status-ok --instance-ids $I --region us-east-1
```

**The public IP did not change.** `18.205.121.71` is an Elastic IP
(`eipalloc-0861a4581e551e1e8`), so it survives stop/start and re-associates automatically. Had it
been a dynamic public IP it would have been lost on stop, taking DNS and the TLS cert's reachability
with it. No DNS change, no cert reissue, no `CLAUDE.md` edit was needed.

**Verified after:**

| Check | Result |
|---|---|
| Instance type | `m7i-flex.large`, `us-east-1d` |
| CPU / RAM | 2 vCPU `Xeon Platinum 8488C`, 7783 MB (was 3839 MB) |
| Status checks | 2/2 OK |
| Containers | **15/15 `Up … (healthy)`** |
| `https://lausing.com` | `200` in 0.31 s |
| `https://lausing.com/api/health` | `200` |
| Scheduler catch-up | `_avg_volume_startup_check` and `_opthist_startup_check` both fired and completed |

That last row matters and is easy to skip. A restart is precisely the event that silently skips
an APScheduler cron slot for the day — see
[`incidents/scheduler-misfire-data-gaps.md`](incidents/scheduler-misfire-data-gaps.md). The
boot-time catch-up jobs exist for this, and a resize is not verified until they are observed to
have run.

---

## Part 4 — The caveat: m7i-flex is not unlimited either

**Do not read this resize as "the CPU problem is permanently solved."** `m7i-flex` is itself a
credit-backed family: it delivers a **40% baseline** with the ability to burst to 100% about 95%
of the time over a rolling 24-hour window. It is much more generous than t3 and it fails much
more gracefully, but it is not a dedicated-performance instance.

Do the arithmetic honestly. Measured demand was **1.08 vCPU sustained**. On 2 vCPU that is
**~54% sustained**, which is *above* the 40% baseline. Sapphire Rapids is meaningfully faster per
vCPU than the t3's silicon, so the same work should land somewhere nearer **~45%** — still above
baseline.

What genuinely changed is the severity of the floor. Throttled, this instance falls back to
**0.8 vCPU of modern cores**, versus the t3's **0.4 vCPU** of older ones — roughly a 2.4x better
worst case, and above the measured 1.08 vCPU mean far more of the time. That is what should stop
the reboots. It is not headroom to grow into.

**Monitoring checkpoint — re-check about a week out (~2026-09-24):** pull `CPUUtilization` with
`Sum` over 1-hour buckets (not `Average` over long ones — see Part 1) and confirm sustained
utilisation is under 40%. If it is not, the next step is `m7i-flex.xlarge` (4 vCPU, ~$140/mo) or
`m7i.large` (non-flex, fully dedicated), **not** another burstable tier.

---

## Part 5 — The ECS webhook stack, scaled to zero

Both services in cluster `webhook-integration-cluster` were scaled to `desired-count 0`
on 2026-09-17.

| Service | Task def | Was it serving traffic? |
|---|---|---|
| `webhook-integration-task-service-4m0oxgst` | `webhook-klaviyo-task:14` | **No.** Not registered in any target group — an orphan of a previous deploy. |
| `webhook-service` | `webhook-klaviyo-task:15` | **Yes.** Registered healthy in `webhook-tg`, ~10,000+ requests/week. |

**The second one was stopped as a deliberate, informed decision, and it has a cost.** While it is
at zero, inbound Klaviyo webhooks are dropped. Klaviyo deliveries are generally **not replayable**,
so events arriving during the outage are lost permanently rather than queued. Restoring the
service restores *future* deliveries, not the missed ones. If that is not acceptable, restore it
now via Part 6 — the gap only grows.

**Scale-to-zero, not delete.** `--desired-count 0` keeps the service, its task definition, its
target-group registration and its network config intact, so the restore is a single command.
`aws ecs delete-service` would have been irreversible and would have required rebuilding all of
that by hand. Prefer scale-to-zero for anything you might want back.

---

## Part 6 — RUNBOOK: stopping and restarting the ECS services

Cluster: `webhook-integration-cluster` · Region: `us-east-1`

### Check current state

```bash
aws ecs describe-services --region us-east-1 \
  --cluster webhook-integration-cluster \
  --services webhook-service webhook-integration-task-service-4m0oxgst \
  --query 'services[].{Name:serviceName,Desired:desiredCount,Running:runningCount,Status:status}' \
  --output table
```

### Stop (reversible)

```bash
aws ecs update-service --region us-east-1 \
  --cluster webhook-integration-cluster \
  --service webhook-service \
  --desired-count 0
```

Draining takes a minute or two. Wait for it rather than assuming:

```bash
aws ecs wait services-stable --region us-east-1 \
  --cluster webhook-integration-cluster --services webhook-service
```

### Restart

```bash
aws ecs update-service --region us-east-1 \
  --cluster webhook-integration-cluster \
  --service webhook-service \
  --desired-count 1

aws ecs wait services-stable --region us-east-1 \
  --cluster webhook-integration-cluster --services webhook-service
```

For the orphan, substitute `--service webhook-integration-task-service-4m0oxgst`. It has no load
balancer, so the health verification below does not apply to it — and unless you have a specific
reason, **leave it at 0**: it was serving nothing before it was stopped.

### Verify the restart actually worked

`runningCount: 1` means a container started. It does not mean traffic reaches it. The service is
only genuinely back when the ALB says so:

```bash
# 1. A task is running
aws ecs list-tasks --region us-east-1 \
  --cluster webhook-integration-cluster --service-name webhook-service --output table

# 2. The ALB considers it healthy  (this is the one that matters)
aws elbv2 describe-target-health --region us-east-1 \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-1:705033936133:targetgroup/webhook-tg/d784cbd677eba4ad \
  --query 'TargetHealthDescriptions[].{Id:Target.Id,State:TargetHealth.State,Reason:TargetHealth.Reason}' \
  --output table

# 3. End to end
curl -s -o /dev/null -w '%{http_code}\n' http://webhook-alb-121806171.us-east-1.elb.amazonaws.com/
```

Expect `State: healthy` in step 2. `initial` means still registering — wait. `unhealthy` means
the task is up but failing the `/` health check on port 80, which is a container problem, not a
scaling problem; read `Reason` and then the task's CloudWatch logs.

### Reference config (for rebuilding if a service is ever lost)

| | `webhook-service` | `webhook-integration-task-service-4m0oxgst` |
|---|---|---|
| Task definition | `webhook-klaviyo-task:15` | `webhook-klaviyo-task:14` |
| Launch type | FARGATE | FARGATE |
| Container / port | `frontend` : 80 | — |
| Target group | `webhook-tg` | none |
| Security group | `sg-08d44defcc337e576` | `sg-01e8727d0d911d570` |
| Public IP | ENABLED | ENABLED |
| Subnets | `subnet-03cd73e4b75b03ea6`, `subnet-071b341903a6dcb8b`, `subnet-01a0ff30c6163b792` | those three plus `subnet-022753bf8592f75ea`, `subnet-08e5cdd9e25dcf1d9`, `subnet-0513d1c0120061d35` |

Load balancer: `webhook-alb` → `webhook-alb-121806171.us-east-1.elb.amazonaws.com`, HTTP:80 →
`webhook-tg` (health check path `/`).

---

## Part 7 — What still bills while both services sit at zero

Scaling Fargate tasks to zero stops the Fargate vCPU/memory charges (~$68.77/mo). It stops
nothing else. The supporting infrastructure is billed on existence, not on use:

| Still billing | ~$/mo | Currently serving |
|---|---|---|
| `webhook-alb` (ALB) | 15.67 | **nothing** — `webhook-tg` has zero registered targets |
| RDS `webhookintegration-db` (`db.t3.micro`, MySQL, 50 GB, public) | 17.38 | unknown — not verified |
| 2 Elastic IPs on the ALB's ENIs | ~7 | the idle ALB |

That is roughly **$40/mo for a stack that currently answers no requests.** Deleting the ALB and
RDS instance would recover it, but both are **destructive and were not done**: the ALB's DNS name
is presumably configured in Klaviyo, and the RDS instance holds whatever the integration
persisted. If the webhook stack is being retired for good, the order is: snapshot RDS → confirm
the snapshot → delete the RDS instance → delete the ALB → release the two EIPs. That is a
separate decision, not a cleanup.

### One correction to an earlier claim

An earlier note in this session reported "3 unattached Elastic IPs, ~$11/mo, safe to release."
**That was wrong.** All four Elastic IPs in the account are associated:

| IP | Attached to |
|---|---|
| `18.205.121.71` | the StockAI EC2 instance — **never release this one** |
| `54.172.59.190`, `98.87.203.160` | the two `webhook-alb` ENIs |
| `3.232.116.39` | the `webhookintegration-db` RDS network interface |

The error came from filtering on `AssociationId == null`, which was the right *idea* but read
against the wrong assumption: an EIP attached to a bare network interface (an ALB's, an RDS
instance's) has an `AssociationId` and no `InstanceId`, so a query that only looks for instances
reports it as free. **Releasing them on that basis would have broken the ALB and the RDS
endpoint.** Resolve an EIP through its `NetworkInterfaceId` before ever calling it unattached.

---

## The transferable lessons

1. **A throttled resource's utilisation metric measures the throttle, not the demand.** Measure
   what is being denied — credit burn, run-queue depth — and pick the CloudWatch statistic
   deliberately; `Average` over wide buckets will quietly agree with you.
2. **`unlimited` mode is not unlimited.** It caps surplus, and at the cap it throttles to baseline
   exactly like `standard` mode would — while still charging for the surplus. Being billed for
   credits *and* throttled is a reachable state, and it was the state this instance was in.
3. **Verify the target instance type is offered in your AZ before you stop.** It is one API call,
   and the alternative is an instance you cannot start.
4. **Scale to zero, don't delete.** One command down, one command back, with every piece of
   config preserved in between.
5. **`runningCount: 1` is not "it's back."** Check the target group.
6. **Resolve an EIP through its network interface, not its instance,** before believing it is
   unattached.
