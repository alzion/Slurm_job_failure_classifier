export const INCIDENT_DASHBOARDS = {
  "01_cuda_oom":          "/grafana/d/job-failure-v1/job-failure-analysis",
  "02_thermal_throttle":  "/grafana/d/prefailure-signals-v1/prefailure-signals",
  "03_nccl_failure":      "/grafana/d/node-reliability-v1/node-reliability",
  "04_xid_error":         "/grafana/d/prefailure-signals-v1/prefailure-signals",
  "05_cascading_failure": "/grafana/d/fleet-health-v1/fleet-health",
};

export const LEARNING_OBJECTIVE =
  "By the end of this simulation you can identify the root cause of a GPU incident " +
  "from logs, metrics, and dashboards, and make a defensible escalation decision under pressure.";
