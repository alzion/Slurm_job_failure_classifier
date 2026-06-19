# GPU Fleet Escalation Runbook

---

## GPU_HARDWARE
**Trigger:** `DCGM_FI_DEV_ECC_DBE_VOL_TOTAL > 0` or `DCGM_FI_DEV_XID_ERRORS > 0` or sacct `NODE_FAIL`  
**Severity:** P1  
**Page:** On-call infra engineer + hardware team  

1. Identify node: `scontrol show node <node> | grep Reason`
2. Check XID code in slurmd.log: XID 48=ECC, 74=NVLink, 79=bus
3. Pull ECC counts: `nvidia-smi -q -d ECC | grep -A4 "Volatile ECC"`
4. Check DCGM dashboard for ECC_SBE lead-up (should show 90-min ramp)
5. Open vendor ticket if DBE count > 0 or XID 79

```bash
scontrol update NodeName=<node> State=drain Reason="GPU_HARDWARE detected"
```
**Job owner:** "Job <id> failed due to a GPU hardware error on <node>. The node has been drained. Re-queue when notified."  
**Escalation:** Hardware team → vendor (NVIDIA support) if not resolved in 2h

---

## NCCL_NETWORK_HARDWARE
**Trigger:** Log `NVLink error`, `nvlink crc flit error`, `NVSwitch error`, or `nvidia-fabricmanager` crash  
**Severity:** P2  
**Page:** On-call infra engineer  

Physical interconnect failure. The node must be drained — requeuing on the same nodes will reproduce the failure.

1. Confirm NVLink errors: `nvidia-smi nvlink --status` on affected node
2. Check fabric manager: `systemctl status nvidia-fabricmanager`
3. Review NVSwitch logs: `nvidia-smi nvswitch --status` (DGX/HGX systems)
4. Drain the node before requeuing:

```bash
scontrol update NodeName=<node> State=drain Reason="NVLink/NVSwitch hardware failure"
```
**Job owner:** "Job <id> failed due to a GPU interconnect hardware failure on `<node>`. Re-queued on a different node set. Hardware team notified."  
**Escalation:** Hardware team immediately; open NVIDIA support case if fabric manager cannot restart

---

## NCCL_COMM_FAILURE
**Trigger:** Log `ncclSystemError`, `Socket: Connection timed out`, NCCL bootstrap failure, or rank timeout  
**Severity:** P2  
**Page:** On-call infra engineer  

Software or network configuration failure. Check config before draining — the node hardware is likely fine.

1. Check slurmd.log for the specific error: `socket.cc`, `bootstrap`, or `ncclSystemError`
2. Verify `NCCL_SOCKET_IFNAME` matches the correct network interface on all nodes
3. Check firewall rules: NCCL needs open ports between compute nodes
4. Test inter-node bandwidth: run NCCL allreduce test across affected nodes
5. If repeated on the same node pair, escalate to network team (possible switch/routing issue)

```bash
# Only drain if the same node fails repeatedly with different job partners
scontrol update NodeName=<node> State=drain Reason="Repeated NCCL_COMM_FAILURE — investigation"
```
**Job owner:** "Job <id> failed due to an inter-GPU communication error. Re-queuing — this is often transient."  
**Escalation:** Network team if allreduce test fails across multiple node pairs

---

## CUDA_OOM
**Trigger:** sacct `OUT_OF_MEMORY` or log `CUDA out of memory`  
**Severity:** P3  
**Page:** None (notify job owner only)  

1. Check job's requested memory: `sacct -j <id> --format=ReqMem,MaxRSS`
2. Check model batch size and sequence length in job script
3. Review whether job recently changed checkpoint size
4. Check if other jobs on same node are over-allocating
5. Advise gradient checkpointing or reduced batch size

```bash
# No cordon needed — software issue, not hardware
```
**Job owner:** "Job <id> ran out of GPU memory. Reduce batch size or enable gradient checkpointing, then re-queue."  
**Escalation:** None — user-actionable

---

## THERMAL_THROTTLE
**Trigger:** `DCGM_FI_DEV_GPU_TEMP > 82°C` sustained or `DCGM_FI_DEV_SM_CLOCK` drops >15%  
**Severity:** P2  
**Page:** On-call infra engineer + data-center ops  

1. Check current temp: `nvidia-smi -q -d TEMPERATURE`
2. Check room/rack inlet temperature with DC ops
3. Inspect fan status: `nvidia-smi -q -d FAN`
4. Check for blocked airflow or recent physical changes to rack
5. Review temperature trend on Fleet Health dashboard (last 4h)

```bash
scontrol update NodeName=<node> State=drain Reason="THERMAL_THROTTLE detected"
```
**Job owner:** "Job <id> was terminated due to GPU overheating. Node is under inspection; re-queue will be possible once cleared."  
**Escalation:** DC ops for cooling fault; hardware team if GPU fan has failed

---

## INFRA_STORAGE
**Trigger:** Log `Stale file handle`, `lustre`, or `NFS`  
**Severity:** P2  
**Page:** On-call storage engineer  

1. Identify mount: `grep "Stale file handle" /var/log/slurmd.log | tail -5`
2. Check Lustre/NFS health: `lfs check all` or `showmount -e <nfs-server>`
3. Test from affected node: `ls /lustre/scratch` (should not hang)
4. Check storage system logs for I/O errors or OST failures
5. Re-mount if stale: `umount -l /lustre/scratch && mount /lustre/scratch`

```bash
# No cordon needed unless all jobs on node are affected
```
**Job owner:** "Job <id> failed due to a storage system error. The issue is being investigated. Re-queue once storage is confirmed healthy."  
**Escalation:** Storage vendor if OST/OSD offline

---

## PREEMPTION
**Trigger:** sacct `PREEMPTED`  
**Severity:** P4  
**Page:** None  

1. Confirm preemption: `sacct -j <id> --format=State,Priority,Partition`
2. Check which higher-priority job triggered it: `squeue --start -j <id>`
3. Verify preemption policy is configured as intended
4. No hardware action needed
5. Advise user to re-queue with higher priority or reservation

```bash
# No cordon needed
```
**Job owner:** "Job <id> was preempted by a higher-priority job. Re-queue or request a reservation."  
**Escalation:** None

---

## TIMEOUT
**Trigger:** sacct `TIMEOUT`  
**Severity:** P4  
**Page:** None  

1. Check wall-time limit: `sacct -j <id> --format=Timelimit,Elapsed`
2. Compare against historical runtime for the same job type
3. Check for abnormal slowdown (I/O stall, CPU contention) in job logs
4. No hardware action needed
5. Advise user to increase time limit or optimize job

```bash
# No cordon needed
```
**Job owner:** "Job <id> exceeded its time limit. Increase `--time` or optimize the workload, then re-queue."  
**Escalation:** None

---

## USER_ERROR
**Trigger:** sacct `FAILED` + log `execve failed` / `No such file or directory`  
**Severity:** P4  
**Page:** None  

1. Check exit code: `sacct -j <id> --format=ExitCode`
2. Read slurmd.log for the exact error: `grep "847340" /var/log/slurmd.log`
3. Verify binary/script path exists on compute nodes
4. Check module environment: `module list` matches what job script loads
5. Test interactively: `srun --pty bash` and reproduce the execve call

```bash
# No cordon needed
```
**Job owner:** "Job <id> failed due to a configuration error (missing binary or bad environment). Check your job script and re-queue."  
**Escalation:** None — user-actionable
