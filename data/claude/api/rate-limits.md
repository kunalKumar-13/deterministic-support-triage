# Claude API rate limits

Rate limits are applied per organization and per model. They are expressed
in three dimensions: requests per minute (RPM), input tokens per minute
(ITPM), and output tokens per minute (OTPM).

When you hit a rate limit, the API returns HTTP 429 with a
`retry-after` header indicating how long to wait before retrying.

## How to avoid 429s

- Implement exponential backoff with jitter on 429 responses.
- Cache results when a request is idempotent.
- For batch workloads, use the Files / Batches APIs which run against a
  separate, higher rate limit.

If your workload genuinely needs higher throughput than the default tier
allows, request a rate-limit increase from the Console. We typically
respond within 1-2 business days.
