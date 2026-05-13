# Day 10 Reliability Final Report

# Day 10 Reliability Final Report

## 1. Architecture summary

The gateway first checks cache, then tries providers through per-provider circuit breakers, and finally returns a static fallback if every live route is unavailable. The in-memory cache is used for local runs; `SharedRedisCache` provides the same API with shared state and TTL for multi-instance deployments.

```
User Request
	|
	v
[ReliabilityGateway]
	|
	+--> [Cache lookup]
	|        |
	|        +--> HIT -> return cached answer
	|        |
	|        +--> MISS
	|
	+--> [Circuit Breaker: primary] -> primary provider
	|
	+--> [Circuit Breaker: backup]  -> backup provider
	|
	+--> [Static fallback message]
```

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Trips quickly enough to prevent retry storms, but not on one-off noise. |
| reset_timeout_seconds | 2 | Short probe window so recovery can be observed during a lab run. |
| success_threshold | 1 | One healthy probe is enough to close the breaker in this simplified lab. |
| cache TTL | 300 | Long enough to show reuse across the small sample query set. |
| similarity_threshold | 0.92 | High enough to reduce false hits across similar-but-different prompts. |
| load_test requests | 100 | Enough volume to exercise fallback, cache reuse, and metrics collection. |

## 3. SLO definitions

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 99% | 99.33% | Yes |
| Latency P95 | < 2500 ms | 313.96 ms | Yes |
| Fallback success rate | >= 95% | 95.83% | Yes |
| Cache hit rate | >= 10% | 76.67% | Yes |
| Recovery time | < 5000 ms | 3739.99 ms in no-cache baseline | Yes |

## 4. Metrics

| Metric | Value |
|---|---:|
| availability | 0.9933 |
| error_rate | 0.0067 |
| latency_p50_ms | 272.45 |
| latency_p95_ms | 313.96 |
| latency_p99_ms | 316.9 |
| fallback_success_rate | 0.9583 |
| cache_hit_rate | 0.7667 |
| estimated_cost_saved | 0.255646 |
| circuit_open_count | 6 |
| recovery_time_ms | None |

## 5. Cache comparison

Spot check on the same sample queries with 20 requests per run.

| Metric | Without cache | With cache | Delta |
|---|---:|---:|---:|
| latency_p50_ms | 260.81 | 230.36 | -30.45 |
| latency_p95_ms | 314.16 | 317.70 | +3.54 |
| estimated_cost | 0.009622 | 0.002274 | -0.007348 |
| cache_hit_rate | 0.0 | 0.7 | +0.7 |

## 6. Redis shared cache

In-memory cache is not enough for multi-instance deployments because each process would see its own isolated cache state. `SharedRedisCache` stores query/response pairs in Redis hashes with TTL, so every gateway instance can read the same cached answers and expire them consistently.

### Evidence of shared state

Two cache instances saw the same entry in Redis:

```
cache_b_get ('shared response', 1.0)
keys ['rl:report:d7978f530864']
```

Redis tests passed after starting Redis on port 6379:

```
tests/test_redis_cache.py::test_redis_connection PASSED
tests/test_redis_cache.py::test_set_and_exact_get PASSED
tests/test_redis_cache.py::test_ttl_expiry PASSED
tests/test_redis_cache.py::test_shared_state_across_instances PASSED
tests/test_redis_cache.py::test_privacy_query_not_cached PASSED
tests/test_redis_cache.py::test_false_hit_different_years PASSED
```

### Redis CLI output

```bash
KEYS "rl:report:*"
rl:report:d7978f530864
```

## 7. Chaos scenarios

| Scenario | Expected behavior | Observed behavior | Pass/Fail |
|---|---|---|---|
| primary_timeout_100 | All traffic fallback to backup, circuit opens | Primary breaker tripped, requests routed to backup/static fallback as needed | Pass |
| primary_flaky_50 | Circuit oscillates, mix of primary and fallback | Mix of primary and fallback responses with breaker opens during failures | Pass |
| all_healthy | All requests via primary, no circuit opens | Primary handled most requests, cache still reduced repeat work | Pass |
| cache_vs_no_cache | Cache should reduce cost and increase hit rate | Cache run showed lower cost and 70% hit rate in the spot check | Pass |

## 8. Failure analysis

The main remaining weakness is that recovery behavior is still synthetic and depends on the lab traffic pattern. In the cached aggregate run, `recovery_time_ms` stayed `None` because the breaker did not complete a full open-to-close cycle in a way the metric could observe. Before production, I would add explicit probe traffic, per-provider backoff jitter, and a clearer health model so recovery can be measured independently of cache effects.

## 9. Next steps

1. Add a real CSV export to the report workflow and keep it in sync with `metrics.json`.
2. Add a dedicated recovery probe scenario so breaker close times are always measurable.
3. Add one more guardrail for prompt similarity so false hits can be audited per category, not just by 4-digit number mismatch.