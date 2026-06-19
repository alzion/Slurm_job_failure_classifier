"""
Unit tests for classifier/log_parser.py

Tests cover:
  - Timestamp parsing for all four supported formats
  - Lines that cannot be parsed (raw kernel uptime, empty, malformed)
  - Every pattern-rule category with at least two representative log lines
  - Evidence field extraction (job_id, node, detail)
  - First-rule-wins: a line matching multiple categories gets the highest-priority one
  - parse_logs() end-to-end with real temp files
"""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from classifier.log_parser import (
    LogEvidence,
    _split_line,
    _reset_file_state,
    parse_logs,
    summarise,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TS = '2026-05-16T12:34:56'
TS_SLURM  = f'[{TS}.000]'       # Slurm bracket format
TS_ISO    = TS                   # ISO plain
TS_SYSLOG = 'May 16 12:34:56'   # syslog (no year — parser injects current year)
TS_DMESG  = '[Fri May 16 12:34:56 2026]'


def _slurm(body: str) -> str:
    return f'{TS_SLURM} {body}'

def _iso(body: str) -> str:
    return f'{TS_ISO} {body}'

def _syslog(body: str) -> str:
    return f'{TS_SYSLOG} myhost slurmd[1234]: {body}'

def _dmesg(body: str) -> str:
    return f'{TS_DMESG} {body}'


def parse_line(line: str) -> list[LogEvidence]:
    """Write a single log line to a temp file and parse it."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'slurmctld.log'
        path.write_text(line + '\n')
        return parse_logs(d)


def first(line: str) -> LogEvidence:
    ev = parse_line(line)
    assert ev, f'Expected at least one evidence record for line:\n  {line}'
    return ev[0]


# ===========================================================================
# Timestamp parsing
# ===========================================================================

class TestTimestampParsing:

    def test_slurm_bracket_format(self):
        ts, body = _split_line('[2026-05-16T12:34:56.000] some body text')
        assert ts is not None
        assert ts.year == 2026 and ts.month == 5 and ts.day == 16
        assert ts.hour == 12 and ts.minute == 34 and ts.second == 56
        assert 'some body text' in body

    def test_iso_plain_format(self):
        ts, body = _split_line('2026-05-16T12:34:56 some body text')
        assert ts is not None
        assert ts.year == 2026
        assert 'some body text' in body

    def test_iso_with_milliseconds(self):
        ts, body = _split_line('2026-05-16T12:34:56.123 body')
        assert ts is not None
        assert ts.second == 56

    def test_syslog_format(self):
        ts, body = _split_line('May 16 12:34:56 myhost slurmd[1234]: body text')
        assert ts is not None
        assert ts.month == 5 and ts.day == 16
        assert 'body text' in body

    def test_dmesg_T_format(self):
        ts, body = _split_line('[Fri May 16 12:34:56 2026] kernel: body')
        assert ts is not None
        assert ts.year == 2026 and ts.month == 5

    def test_raw_kernel_uptime_is_skipped(self):
        ts, body = _split_line('[12345.678] some kernel message')
        assert ts is None

    def test_empty_line_is_skipped(self):
        ts, body = _split_line('')
        assert ts is None

    def test_unparseable_line_is_skipped(self):
        ts, body = _split_line('garbage line with no timestamp at all')
        assert ts is None

    def test_timezone_set_to_utc(self):
        ts, _ = _split_line(f'[{TS}.000] body')
        assert ts.tzinfo == timezone.utc


# ===========================================================================
# GPU_HARDWARE patterns
# ===========================================================================

class TestGpuHardwarePatterns:

    def test_job_requeue_extracts_job_id_and_node(self):
        e = first(_slurm('_job_requeue: requeueing job 847293 due to node failure gpu03'))
        assert e.category_hint == 'GPU_HARDWARE'
        assert e.job_id == '847293'
        assert e.node == 'gpu03'

    def test_node_down_extracts_node(self):
        e = first(_slurm('_node_down: node gpu07 is DOWN'))
        assert e.category_hint == 'GPU_HARDWARE'
        assert e.node == 'gpu07'

    def test_nvrm_xid_extracts_detail(self):
        e = first(_slurm('NVRM: Xid (PCI:0000:81:00): 48, pid=12345'))
        assert e.category_hint == 'GPU_HARDWARE'
        assert e.detail == 'XID=48'

    def test_bare_xid_pattern(self):
        # Pattern requires "Xid.*: <digits>" — the syslog-stripped "NVRM:" prefix variant
        e = first(_slurm('GPU Xid kernel error: 79, device lost'))
        assert e.category_hint == 'GPU_HARDWARE'
        assert 'XID=79' in e.detail

    def test_ecc_double_bit_error(self):
        e = first(_iso('ECC Double Bit Error detected on GPU 0'))
        assert e.category_hint == 'GPU_HARDWARE'
        assert e.detail == 'ECC_DBE'

    def test_uncorrectable_ecc(self):
        e = first(_slurm('NVRM: uncorrectable ECC error in frame buffer'))
        assert e.category_hint == 'GPU_HARDWARE'
        assert e.detail == 'ECC_DBE'

    def test_gpu_board_error(self):
        e = first(_iso('NVRM: GPU Board Error on GPU 2'))
        assert e.category_hint == 'GPU_HARDWARE'
        assert e.detail == 'GPU_INIT_FAIL'

    def test_rm_init_adapter_failed(self):
        e = first(_slurm('RmInitAdapter failed for GPU at PCI:0000:05:00'))
        assert e.category_hint == 'GPU_HARDWARE'

    def test_gpu_disappeared_from_bus(self):
        e = first(_iso('NVRM: GPU-abc123 not found on bus'))
        assert e.category_hint == 'GPU_HARDWARE'
        assert e.detail == 'GPU_MISSING'

    def test_hardware_machine_check_exception(self):
        # Use ISO format — syslog strips everything before the last ': ' in the body,
        # which would hide the "Machine check exception" text.
        e = first(_iso('Machine check exception: CPU 3 bank 4'))
        assert e.category_hint == 'GPU_HARDWARE'
        assert e.detail == 'HW_MCE'


# ===========================================================================
# NCCL_COMM_FAILURE patterns
# ===========================================================================

class TestNcclPatterns:

    def test_nccl_system_error(self):
        e = first(_slurm('ncclSystemError: system call failed'))
        assert e.category_hint == 'NCCL_COMM_FAILURE'

    def test_nccl_warn_timeout(self):
        e = first(_slurm('NCCL WARN Timeout waiting for group call'))
        assert e.category_hint == 'NCCL_COMM_FAILURE'

    def test_socket_connection_timed_out(self):
        e = first(_slurm('Socket: Connection timed out socket.cc:123'))
        assert e.category_hint == 'NCCL_COMM_FAILURE'

    def test_nvlink_crc_error(self):
        e = first(_iso('nvlink crc flit error on GPU 0 link 3'))
        assert e.category_hint == 'NCCL_NETWORK_HARDWARE'
        assert e.detail == 'NVLINK_CRC'

    def test_mpi_allreduce_fatal(self):
        e = first(_slurm('Fatal error in MPI_Allreduce: rank 3 died'))
        assert e.category_hint == 'NCCL_COMM_FAILURE'
        assert 'MPI_Allreduce_FATAL' in e.detail

    def test_rank_lost_contact(self):
        e = first(_slurm('Rank 5 lost contact with peer'))
        assert e.category_hint == 'NCCL_COMM_FAILURE'

    def test_nccl_internal_error(self):
        e = first(_slurm('ncclInternalError: unexpected state'))
        assert e.category_hint == 'NCCL_COMM_FAILURE'


# ===========================================================================
# NCCL_NETWORK_HARDWARE patterns
# (physical interconnect — drain node, file hardware ticket)
# ===========================================================================

class TestNcclNetworkHardwarePatterns:

    def test_nvlink_crc_flit_error(self):
        e = first(_iso('nvlink crc flit error on GPU 0 link 3'))
        assert e.category_hint == 'NCCL_NETWORK_HARDWARE'
        assert e.detail == 'NVLINK_CRC'

    def test_nvlink_generic_error(self):
        e = first(_slurm('NVLink error detected on device 0'))
        assert e.category_hint == 'NCCL_NETWORK_HARDWARE'

    def test_nvswitch_error(self):
        e = first(_iso('NVSwitch error: fabric timeout on port 4'))
        assert e.category_hint == 'NCCL_NETWORK_HARDWARE'
        assert e.detail == 'NVSWITCH'

    def test_fabric_manager_crash(self):
        e = first(_iso('nvidia-fabricmanager died unexpectedly'))
        assert e.category_hint == 'NCCL_NETWORK_HARDWARE'
        assert e.detail == 'FABRIC_MGR'

    def test_fabric_manager_error(self):
        e = first(_iso('nvidia-fabricmanager: error initialising fabric'))
        assert e.category_hint == 'NCCL_NETWORK_HARDWARE'
        assert e.detail == 'FABRIC_MGR'

    def test_nccl_network_hardware_distinct_from_comm_failure(self):
        # ncclSystemError (software) must NOT match NCCL_NETWORK_HARDWARE
        e = first(_slurm('ncclSystemError on rank 3'))
        assert e.category_hint == 'NCCL_COMM_FAILURE'


# ===========================================================================
# CUDA_OOM patterns
# ===========================================================================

class TestCudaOomPatterns:

    def test_pytorch_cuda_oom(self):
        e = first(_slurm('CUDA out of memory. Tried to allocate 20.00 GiB'))
        assert e.category_hint == 'CUDA_OOM'

    def test_cuda_error_out_of_memory(self):
        e = first(_slurm('CUDA error: out of memory on device 0'))
        assert e.category_hint == 'CUDA_OOM'

    def test_tensorflow_oom(self):
        e = first(_slurm('OOM when allocating tensor with shape[1024, 1024, 512]'))
        assert e.category_hint == 'CUDA_OOM'

    def test_cuda_error_constant(self):
        e = first(_slurm('CUDA_ERROR_OUT_OF_MEMORY returned by cudaMalloc'))
        assert e.category_hint == 'CUDA_OOM'

    def test_cuda_malloc_failed(self):
        e = first(_iso('cudaMalloc failed for 21474836480 bytes'))
        assert e.category_hint == 'CUDA_OOM'

    def test_jax_oom(self):
        e = first(_slurm('ResourceExhaustedError: OOM allocating 8GB on device'))
        assert e.category_hint == 'CUDA_OOM'


# ===========================================================================
# THERMAL_THROTTLE patterns
# ===========================================================================

class TestThermalPatterns:

    def test_gpu_thermal_throttle(self):
        e = first(_slurm('GPU thermal throttle engaged on device 2'))
        assert e.category_hint == 'THERMAL_THROTTLE'

    def test_hw_thermal_slowdown(self):
        e = first(_iso('HW Thermal Slowdown active on GPU 1'))
        assert e.category_hint == 'THERMAL_THROTTLE'


# ===========================================================================
# INFRA_STORAGE patterns
# ===========================================================================

class TestInfraStoragePatterns:

    def test_stale_file_handle_with_job_id(self):
        # Regex is job_(\d+)' — job number must be immediately before the closing quote.
        e = first(_slurm("Stale file handle: '/lustre/scratch/job_847293'"))
        assert e.category_hint == 'INFRA_STORAGE'
        assert e.job_id == '847293'

    def test_stale_file_handle_generic(self):
        e = first(_slurm('Stale file handle on /mnt/lustre'))
        assert e.category_hint == 'INFRA_STORAGE'

    def test_no_space_left(self):
        e = first(_slurm('write error: No space left on device'))
        assert e.category_hint == 'INFRA_STORAGE'

    def test_nfs_mount_error(self):
        e = first(_iso('NFS4ERR_IO: i/o error on /nfs/scratch'))
        assert e.category_hint == 'INFRA_STORAGE'

    def test_eio_error(self):
        e = first(_syslog('kernel: EIO reading from /dev/sda'))
        assert e.category_hint == 'INFRA_STORAGE'

    def test_disk_quota_exceeded(self):
        e = first(_slurm('Disk quota exceeded for user researcher'))
        assert e.category_hint == 'INFRA_STORAGE'

    def test_transport_endpoint_not_connected(self):
        e = first(_iso('Transport endpoint is not connected'))
        assert e.category_hint == 'INFRA_STORAGE'


# ===========================================================================
# USER_ERROR patterns
# ===========================================================================

class TestUserErrorPatterns:

    def test_execve_task_launch_with_job_id(self):
        e = first(_slurm('Task launch for StepId=847293.0 failed due to execve failed'))
        assert e.category_hint == 'USER_ERROR'
        assert e.job_id == '847293'

    def test_execve_no_such_file(self):
        e = first(_slurm('execve(): No such file or directory'))
        assert e.category_hint == 'USER_ERROR'

    def test_module_not_found(self):
        e = first(_slurm("ModuleNotFoundError: No module named 'torch'"))
        assert e.category_hint == 'USER_ERROR'

    def test_import_error(self):
        e = first(_slurm("ImportError: No module named 'transformers'"))
        assert e.category_hint == 'USER_ERROR'

    def test_syntax_error(self):
        e = first(_slurm('SyntaxError: invalid syntax at line 42'))
        assert e.category_hint == 'USER_ERROR'

    def test_sbatch_error(self):
        e = first(_slurm('sbatch: error: Invalid partition name specified'))
        assert e.category_hint == 'USER_ERROR'


# ===========================================================================
# First-rule-wins: priority within pattern matching
# ===========================================================================

class TestFirstRuleWins:

    def test_gpu_hardware_beats_nccl_on_same_line(self):
        # A line that triggers both NVRM XID rule (GPU_HARDWARE) and ncclSystemError
        # (NCCL_COMM_FAILURE). The NVRM pattern requires "NVRM: Xid[^:]*?: <digits>".
        # GPU_HARDWARE rule appears earlier in _RULES, so it wins.
        e = first(_slurm('NVRM: Xid (PCI:0000:81:00): 48, pid=12345, ncclSystemError'))
        assert e.category_hint == 'GPU_HARDWARE'

    def test_only_first_match_per_line(self):
        # Each log line produces at most one LogEvidence record
        results = parse_line(_slurm('NVRM: Xid 79: GPU error — ncclSystemError'))
        assert len(results) == 1


# ===========================================================================
# parse_logs end-to-end
# ===========================================================================

class TestParseLogs:

    def test_primary_logs_both_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'slurmctld.log').write_text(
                _slurm('_job_requeue: requeueing job 100 due to node failure gpu01') + '\n'
            )
            (Path(d) / 'slurmd.log').write_text(
                _slurm('CUDA out of memory') + '\n'
            )
            ev = parse_logs(d)
        cats = {e.category_hint for e in ev}
        assert 'GPU_HARDWARE' in cats
        assert 'CUDA_OOM' in cats

    def test_supplementary_log_parsed_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'dmesg.log').write_text(
                _iso('NVRM: Xid (PCI:0000:81:00): 48') + '\n'
            )
            ev = parse_logs(d)
        assert any(e.category_hint == 'GPU_HARDWARE' for e in ev)

    def test_missing_supplementary_log_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'slurmctld.log').write_text(
                _slurm('_node_down: node gpu01 is DOWN') + '\n'
            )
            ev = parse_logs(d)  # dmesg.log, kern.log etc. are absent — should not raise
        assert len(ev) == 1

    def test_unparseable_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'slurmctld.log').write_text(
                'garbage line\n'
                + _slurm('ncclSystemError') + '\n'
                + '[12345.678] raw uptime line\n'
            )
            ev = parse_logs(d)
        assert len(ev) == 1
        assert ev[0].category_hint == 'NCCL_COMM_FAILURE'

    def test_results_sorted_by_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'slurmctld.log').write_text(
                '[2026-05-16T12:35:00.000] ncclSystemError\n'
                '[2026-05-16T12:34:00.000] CUDA out of memory\n'
            )
            ev = parse_logs(d)
        assert ev[0].timestamp < ev[1].timestamp

    def test_empty_log_directory(self):
        with tempfile.TemporaryDirectory() as d:
            ev = parse_logs(d)
        assert ev == []

    def test_summarise_groups_by_category(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'slurmctld.log').write_text(
                _slurm('ncclSystemError\n')
                + _slurm('CUDA out of memory\n')
                + _slurm('ncclSystemError\n')
            )
            ev = parse_logs(d)
        grouped = summarise(ev)
        assert len(grouped['NCCL_COMM_FAILURE']) == 2


# ===========================================================================
# Incremental reads and log rotation
# ===========================================================================

class TestIncrementalReads:
    """
    Each test resets _file_state before running so they don't interfere
    with each other or with the TestParseLogs tests above.
    """

    def setup_method(self):
        _reset_file_state()

    def test_second_call_returns_only_new_lines(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'slurmctld.log'
            log.write_text(_slurm('ncclSystemError') + '\n')

            ev1 = parse_logs(d)
            assert len(ev1) == 1

            # Append a new event
            with log.open('a') as f:
                f.write(_slurm('CUDA out of memory') + '\n')

            ev2 = parse_logs(d)
            assert len(ev2) == 1
            assert ev2[0].category_hint == 'CUDA_OOM'

    def test_no_duplicate_events_across_calls(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'slurmctld.log'
            log.write_text(_slurm('ncclSystemError') + '\n')

            ev1 = parse_logs(d)
            ev2 = parse_logs(d)   # nothing new written

            assert len(ev1) == 1
            assert len(ev2) == 0  # no new bytes → no new evidence

    def test_three_appends_each_returns_only_delta(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'slurmctld.log'
            log.write_text('')

            for line in [
                _slurm('ncclSystemError'),
                _slurm('CUDA out of memory'),
                _slurm('Stale file handle'),
            ]:
                with log.open('a') as f:
                    f.write(line + '\n')
                ev = parse_logs(d)
                assert len(ev) == 1  # exactly the one new line each time

    def test_rotation_detected_new_file_read_from_start(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'slurmctld.log'
            log.write_text(_slurm('ncclSystemError') + '\n')

            ev1 = parse_logs(d)
            assert len(ev1) == 1

            # Simulate rotation the way logrotate does it: rename the active log
            # (it keeps its inode) then create a fresh file at the original path.
            # This guarantees a different inode because the old inode is still held
            # by slurmctld.log.1, so the OS cannot recycle it for the new file.
            # Using unlink()+create instead risks inode reuse on Linux.
            log.rename(Path(d) / 'slurmctld.log.1')
            log.write_text(_slurm('CUDA out of memory') + '\n')

            ev2 = parse_logs(d)
            assert len(ev2) == 1
            assert ev2[0].category_hint == 'CUDA_OOM'

    def test_truncation_guard_resets_to_start(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'slurmctld.log'
            log.write_text(_slurm('ncclSystemError') + '\n')

            ev1 = parse_logs(d)
            assert len(ev1) == 1

            # Simulate truncate-and-rewrite on same inode (unusual but possible).
            # We preserve the inode by truncating in-place rather than replacing.
            import classifier.log_parser as lp
            abs_path = str(log.resolve())
            stored_inode, _ = lp._file_state[abs_path]

            # Write a shorter file — stored offset now exceeds file size.
            log.write_text(_slurm('CUDA out of memory') + '\n')

            # Manually corrupt the offset to simulate it exceeding the new file size.
            lp._file_state[abs_path] = (stored_inode, 99999)

            ev2 = parse_logs(d)
            assert len(ev2) == 1
            assert ev2[0].category_hint == 'CUDA_OOM'

    def test_state_not_updated_for_nonexistent_file(self):
        import classifier.log_parser as lp
        with tempfile.TemporaryDirectory() as d:
            parse_logs(d)   # no files present
        # No state should have been written for missing files
        assert not any('slurmctld.log' in k for k in lp._file_state)

    def test_reset_file_state_clears_all_entries(self):
        import classifier.log_parser as lp
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'slurmctld.log').write_text(
                _slurm('ncclSystemError') + '\n'
            )
            parse_logs(d)
            assert len(lp._file_state) > 0

        _reset_file_state()
        assert len(lp._file_state) == 0
