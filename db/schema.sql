CREATE TABLE job_events (
  id SERIAL PRIMARY KEY,
  job_id VARCHAR(20) UNIQUE NOT NULL,
  job_name VARCHAR(100),
  account VARCHAR(50),
  state VARCHAR(20),
  exit_code VARCHAR(10),
  node_list TEXT[],
  gpu_count INTEGER,
  start_time TIMESTAMPTZ,
  end_time TIMESTAMPTZ,
  elapsed_seconds INTEGER,
  failure_category VARCHAR(30),
  classification_confidence VARCHAR(10),
  log_patterns_matched JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE correlation_results (
  id SERIAL PRIMARY KEY,
  job_id VARCHAR(20) REFERENCES job_events(job_id),
  node_hostname VARCHAR(20),
  metric_name VARCHAR(80),
  signal_detected BOOLEAN,
  signal_onset_time TIMESTAMPTZ,
  lead_time_seconds INTEGER,
  baseline_value FLOAT,
  peak_anomaly_value FLOAT,
  anomaly_ratio FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(job_id, node_hostname, metric_name)
);

CREATE TABLE node_health_weekly (
  id SERIAL PRIMARY KEY,
  node_hostname VARCHAR(20),
  week_start DATE,
  total_jobs INTEGER DEFAULT 0,
  failed_jobs INTEGER DEFAULT 0,
  failure_rate FLOAT,
  hardware_failures INTEGER DEFAULT 0,
  nccl_failures INTEGER DEFAULT 0,
  ecc_sbe_accumulated INTEGER DEFAULT 0,
  avg_temperature FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(node_hostname, week_start)
);

-- Classifier run history. Persists each run_once() outcome independently
-- of Prometheus retention, enabling a Grafana panel for run history and
-- gap detection without relying on metrics availability.
CREATE TABLE classifier_runs (
  id           SERIAL PRIMARY KEY,
  run_at       TIMESTAMPTZ DEFAULT NOW(),
  jobs_written INTEGER     NOT NULL DEFAULT 0,
  jobs_skipped INTEGER     NOT NULL DEFAULT 0,
  errors       INTEGER     NOT NULL DEFAULT 0,
  duration_ms  INTEGER
);
