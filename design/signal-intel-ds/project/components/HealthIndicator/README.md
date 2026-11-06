# HealthIndicator

Shows the health of one pipeline, connector, job queue or webhook: Healthy, Degraded, Incident or Silent.

- **Provide:** `name`, `status`, `detail` ("0 records in expected 6 h window").
- Each state has its own shape (dot, diamond, square, hollow ring) as well as colour.
- Silent is an incident, not a quiet day. It shows a hollow `danger` ring so it never passes for "nothing to see".
