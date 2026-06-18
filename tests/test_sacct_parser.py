"""
Unit tests for classifier/sacct_parser.py

Tests cover:
  - expand_nodelist: bracket ranges, single nodes, bare names
  - Helper parsers: _parse_elapsed, _parse_gpu_count
  - Simulator format: valid records, malformed records, missing file, bad JSON
  - Real sacct --json format: Slurm 22+ schema, state-as-list, state-as-string
  - Auto-detection: list → simulator, dict-with-jobs-key → real
  - Format env-var override
  - Sorted output (by end_time)
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from classifier.sacct_parser import (
    SacctJob,
    expand_nodelist,
    parse_sacct,
    _parse_elapsed,
    _parse_gpu_count,
    _parse_state_real,
    _parse_exit_code_real,
    _parse_gpu_count_tres,
    _detect_format,
)


# ---------------------------------------------------------------------------
# expand_nodelist
# ---------------------------------------------------------------------------

class TestExpandNodelist:

    def test_bracket_range(self):
        assert expand_nodelist('gpu[03-06]') == ['gpu03', 'gpu04', 'gpu05', 'gpu06']

    def test_bracket_range_leading_zeros_preserved(self):
        result = expand_nodelist('gpu[03-05]')
        assert all(len(n) == len('gpu03') for n in result)

    def test_single_bracket(self):
        assert expand_nodelist('gpu[01]') == ['gpu01']

    def test_bare_hostname(self):
        assert expand_nodelist('gpu03') == ['gpu03']

    def test_large_range(self):
        result = expand_nodelist('gpu[03-10]')
        assert result == [f'gpu{i:02d}' for i in range(3, 11)]
        assert len(result) == 8

    def test_whitespace_stripped(self):
        assert expand_nodelist('  gpu01  ') == ['gpu01']


# ---------------------------------------------------------------------------
# Helper parsers
# ---------------------------------------------------------------------------

class TestParseElapsed:

    def test_normal_duration(self):
        assert _parse_elapsed('1:30:00') == 5400

    def test_zero(self):
        assert _parse_elapsed('0:00:00') == 0

    def test_long_job(self):
        assert _parse_elapsed('24:00:00') == 86400

    def test_malformed_returns_zero(self):
        assert _parse_elapsed('bad') == 0

    def test_empty_returns_zero(self):
        assert _parse_elapsed('') == 0


class TestParseGpuCount:

    def test_eight_gpus(self):
        assert _parse_gpu_count('gpu:8') == 8

    def test_single_gpu(self):
        assert _parse_gpu_count('gpu:1') == 1

    def test_empty_string(self):
        assert _parse_gpu_count('') == 0

    def test_no_gpu_gres(self):
        assert _parse_gpu_count('cpu:32') == 0

    def test_none_input(self):
        assert _parse_gpu_count(None) == 0


# ---------------------------------------------------------------------------
# Real format helpers
# ---------------------------------------------------------------------------

class TestRealFormatHelpers:

    def test_state_as_list(self):
        assert _parse_state_real({'current': ['FAILED'], 'reason': 'NonZeroExitCode'}) == 'FAILED'

    def test_state_as_string(self):
        assert _parse_state_real({'current': 'TIMEOUT'}) == 'TIMEOUT'

    def test_state_as_bare_string(self):
        assert _parse_state_real('PREEMPTED') == 'PREEMPTED'

    def test_state_empty_list(self):
        assert _parse_state_real({'current': []}) == 'UNKNOWN'

    def test_exit_code_from_dict(self):
        assert _parse_exit_code_real({'status': ['FAILED'], 'return_code': 1}) == '1:0'

    def test_exit_code_zero(self):
        assert _parse_exit_code_real({'status': ['SUCCESS'], 'return_code': 0}) == '0:0'

    def test_exit_code_not_dict(self):
        assert _parse_exit_code_real(None) == '0:0'

    def test_gpu_count_from_tres(self):
        tres = [
            {'type': 'cpu', 'name': None, 'count': 64},
            {'type': 'gres', 'name': 'gpu', 'count': 8},
        ]
        assert _parse_gpu_count_tres(tres) == 8

    def test_gpu_count_no_gpu_in_tres(self):
        tres = [{'type': 'cpu', 'name': None, 'count': 64}]
        assert _parse_gpu_count_tres(tres) == 0

    def test_gpu_count_empty_tres(self):
        assert _parse_gpu_count_tres([]) == 0


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

class TestDetectFormat:

    def test_real_format_detected(self):
        assert _detect_format({'jobs': [], 'meta': {}}) == 'real'

    def test_simulator_format_list(self):
        assert _detect_format([]) == 'simulator'

    def test_simulator_format_list_with_records(self):
        assert _detect_format([{'JobID': 1}]) == 'simulator'

    def test_unknown_structure_falls_back_to_simulator(self):
        assert _detect_format({'not_jobs': []}) == 'simulator'


# ---------------------------------------------------------------------------
# Simulator format parsing
# ---------------------------------------------------------------------------

SIM_RECORD = {
    'JobID':    847293,
    'JobName':  'llama3-70b-finetune',
    'User':     'researcher',
    'Account':  'ml-team',
    'State':    'FAILED',
    'ExitCode': '1:0',
    'NodeList': 'gpu[03-10]',
    'AllocGRES':'gpu:8',
    'Submit':   '2026-05-16T12:00:00',
    'Start':    '2026-05-16T12:01:00',
    'End':      '2026-05-16T13:01:00',
    'Elapsed':  '1:00:00',
    'ReqMem':   '64G',
    'MaxRSS':   '48G',
}


def _write_sim(records: list, directory: str) -> str:
    path = str(Path(directory) / 'sacct_data.json')
    Path(path).write_text(json.dumps(records))
    return path


class TestSimulatorFormat:

    def test_parses_valid_record(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_sim([SIM_RECORD], d)
            jobs = parse_sacct(path, fmt='simulator')
        assert len(jobs) == 1
        j = jobs[0]
        assert j.job_id == '847293'
        assert j.job_name == 'llama3-70b-finetune'
        assert j.state == 'FAILED'
        assert j.gpu_count == 8
        assert j.elapsed_seconds == 3600
        assert j.node_list == [f'gpu{i:02d}' for i in range(3, 11)]

    def test_timestamps_parsed_as_utc(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_sim([SIM_RECORD], d)
            jobs = parse_sacct(path, fmt='simulator')
        assert jobs[0].start_time.tzinfo == timezone.utc
        assert jobs[0].end_time.tzinfo == timezone.utc

    def test_malformed_record_skipped(self):
        bad = {'JobName': 'missing-job-id'}
        with tempfile.TemporaryDirectory() as d:
            path = _write_sim([SIM_RECORD, bad], d)
            jobs = parse_sacct(path, fmt='simulator')
        assert len(jobs) == 1

    def test_empty_array(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_sim([], d)
            jobs = parse_sacct(path, fmt='simulator')
        assert jobs == []

    def test_missing_file_returns_empty(self):
        jobs = parse_sacct('/nonexistent/sacct_data.json', fmt='simulator')
        assert jobs == []

    def test_invalid_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / 'sacct_data.json')
            Path(path).write_text('not json {{{')
            jobs = parse_sacct(path, fmt='simulator')
        assert jobs == []

    def test_sorted_by_end_time(self):
        early = dict(SIM_RECORD, JobID=1, End='2026-05-16T11:00:00')
        late  = dict(SIM_RECORD, JobID=2, End='2026-05-16T13:00:00')
        with tempfile.TemporaryDirectory() as d:
            path = _write_sim([late, early], d)
            jobs = parse_sacct(path, fmt='simulator')
        assert jobs[0].job_id == '1'
        assert jobs[1].job_id == '2'


# ---------------------------------------------------------------------------
# Real sacct --json format parsing
# ---------------------------------------------------------------------------

REAL_RECORD = {
    'job_id':  847293,
    'name':    'llama3-70b-finetune',
    'user':    'researcher',
    'account': 'ml-team',
    'state':   {'current': ['FAILED'], 'reason': 'NonZeroExitCode'},
    'exit_code': {'status': ['FAILED'], 'return_code': 1},
    'nodes':   'gpu[03-10]',
    'tres': {
        'allocated': [
            {'type': 'cpu',  'name': None, 'count': 64},
            {'type': 'gres', 'name': 'gpu', 'count': 8},
        ]
    },
    'time': {
        'submission': 1747396800,
        'start':      1747396860,
        'end':        1747400460,
        'elapsed':    3600,
    },
    'required': {'memory': 65536},
}

REAL_ENVELOPE = {'meta': {}, 'errors': [], 'jobs': [REAL_RECORD]}


def _write_real(envelope: dict, directory: str) -> str:
    path = str(Path(directory) / 'sacct_data.json')
    Path(path).write_text(json.dumps(envelope))
    return path


class TestRealFormat:

    def test_parses_valid_record(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_real(REAL_ENVELOPE, d)
            jobs = parse_sacct(path, fmt='real')
        assert len(jobs) == 1
        j = jobs[0]
        assert j.job_id == '847293'
        assert j.state == 'FAILED'
        assert j.gpu_count == 8
        assert j.elapsed_seconds == 3600
        assert j.node_list == [f'gpu{i:02d}' for i in range(3, 11)]

    def test_state_as_list(self):
        rec = dict(REAL_RECORD, state={'current': ['TIMEOUT'], 'reason': 'TimeLimit'})
        with tempfile.TemporaryDirectory() as d:
            path = _write_real({'jobs': [rec]}, d)
            jobs = parse_sacct(path, fmt='real')
        assert jobs[0].state == 'TIMEOUT'

    def test_state_as_string(self):
        rec = dict(REAL_RECORD, state={'current': 'PREEMPTED'})
        with tempfile.TemporaryDirectory() as d:
            path = _write_real({'jobs': [rec]}, d)
            jobs = parse_sacct(path, fmt='real')
        assert jobs[0].state == 'PREEMPTED'

    def test_timestamps_are_utc(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_real(REAL_ENVELOPE, d)
            jobs = parse_sacct(path, fmt='real')
        assert jobs[0].start_time.tzinfo == timezone.utc
        assert jobs[0].end_time.tzinfo == timezone.utc

    def test_zero_timestamps_become_none(self):
        rec = dict(REAL_RECORD, time={'submission': 0, 'start': 0, 'end': 0, 'elapsed': 0})
        with tempfile.TemporaryDirectory() as d:
            path = _write_real({'jobs': [rec]}, d)
            jobs = parse_sacct(path, fmt='real')
        assert jobs[0].start_time is None
        assert jobs[0].end_time is None

    def test_malformed_record_skipped(self):
        bad = {'name': 'no-job-id'}
        with tempfile.TemporaryDirectory() as d:
            path = _write_real({'jobs': [REAL_RECORD, bad]}, d)
            jobs = parse_sacct(path, fmt='real')
        assert len(jobs) == 1

    def test_empty_jobs_list(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_real({'jobs': []}, d)
            jobs = parse_sacct(path, fmt='real')
        assert jobs == []


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

class TestAutoDetection:

    def test_auto_detects_real_format(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_real(REAL_ENVELOPE, d)
            jobs = parse_sacct(path, fmt='auto')
        assert len(jobs) == 1
        assert jobs[0].job_id == '847293'

    def test_auto_detects_simulator_format(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_sim([SIM_RECORD], d)
            jobs = parse_sacct(path, fmt='auto')
        assert len(jobs) == 1
        assert jobs[0].job_id == '847293'

    def test_default_fmt_is_auto(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_sim([SIM_RECORD], d)
            jobs = parse_sacct(path)
        assert len(jobs) == 1
