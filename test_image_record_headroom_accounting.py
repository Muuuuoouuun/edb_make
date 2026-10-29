"""Memory telemetry fixtures: no image allocation, benchmark, or raised budget."""
from pathlib import Path
from unittest.mock import patch

import pytest

import build_problem_board_edb as board

GIB = 1024**3
MIB = 1024**2


def meminfo(available=15*GIB, dirty=0, writeback=0):
    return f"MemTotal: {16*GIB//1024} kB\nMemAvailable: {available//1024} kB\nDirty: {dirty//1024} kB\nWriteback: {writeback//1024} kB\n"


def stat_v2(inactive=0, dirty=0, writeback=0, mapped=0, shmem=0):
    return f"inactive_file {inactive}\nfile_dirty {dirty}\nfile_writeback {writeback}\nfile_mapped {mapped}\nshmem {shmem}\n"


def probe(values):
    def read(path, *args, **kwargs):
        try:
            return values[str(path)]
        except KeyError:
            raise FileNotFoundError(str(path)) from None
    with patch.object(Path, "read_text", read), patch.object(board.os, "sysconf", side_effect=lambda key: {"SC_AVPHYS_PAGES": GIB//4096, "SC_PAGE_SIZE":4096}[key]):
        return board._available_image_record_memory_bytes()


@pytest.mark.skipif(board.os.name == "nt", reason="Linux memory telemetry")
def test_clean_inactive_cache_restores_headroom_without_counting_dirty_mapped_or_shmem():
    values={"/proc/meminfo":meminfo(),"/sys/fs/cgroup/memory.max":str(16*GIB),
        "/sys/fs/cgroup/memory.current":str(14*GIB),
        "/sys/fs/cgroup/memory.stat":stat_v2(10*GIB,GIB,GIB//2,GIB//2,GIB//2)}
    # 2GiB free +7.5GiB clean inactive, bounded by host MemAvailable.
    assert probe(values)==9*GIB+GIB//2
    with patch.object(board,"_available_image_record_memory_bytes",return_value=probe(values)), patch.dict(board.os.environ,{"EDB_IMAGE_RECORD_WORKERS":""}):
        assert board._resolve_image_record_worker_count(16,source_pixels=8_000_000)==2
    assert board.IMAGE_RECORD_WORKER_MEMORY_BUDGET_BYTES==1536*MIB
    assert board.IMAGE_RECORD_WORKING_BYTES_PER_PIXEL==96


@pytest.mark.skipif(board.os.name == "nt", reason="Linux memory telemetry")
def test_host_available_memory_still_caps_container_credit_and_reserves_pending_writes():
    values={"/proc/meminfo":meminfo(2*GIB,GIB,GIB//2),
        "/sys/fs/cgroup/memory.max":str(16*GIB),"/sys/fs/cgroup/memory.current":str(14*GIB),
        "/sys/fs/cgroup/memory.stat":stat_v2(10*GIB)}
    assert probe(values)==GIB//2


@pytest.mark.parametrize("stats",["", "inactive_file 100", "inactive_file 100\nfile_dirty -1\nfile_writeback 0",
    "inactive_file 100\nfile_dirty 0.5\nfile_writeback 0", "inactive_file 100\nfile_dirty 0\nfile_writeback 0\ninactive_file 100",
    "inactive_file 9999999999999\nfile_dirty 0\nfile_writeback 0",
    stat_v2(100).replace("file_mapped 0\n", ""),stat_v2(100).replace("shmem 0\n", "")])
def test_bad_or_missing_cache_counters_never_increase_headroom(stats):
    assert board._clean_inactive_cgroup_file_bytes(stats,14*GIB,version=2)==0


@pytest.mark.skipif(board.os.name == "nt", reason="Linux memory telemetry")
def test_missing_memory_stat_falls_back_to_actual_limit_remaining():
    assert probe({"/proc/meminfo":meminfo(),"/sys/fs/cgroup/memory.max":str(16*GIB),
                  "/sys/fs/cgroup/memory.current":str(14*GIB)})==2*GIB


@pytest.mark.skipif(board.os.name == "nt", reason="Linux memory telemetry")
def test_zero_cgroup_limit_is_not_mistaken_for_unlimited_host_ram():
    assert probe({"/proc/meminfo":meminfo(),"/sys/fs/cgroup/memory.max":"0",
                  "/sys/fs/cgroup/memory.current":"0"})==0


@pytest.mark.skipif(board.os.name == "nt", reason="Linux memory telemetry")
def test_v2_subgroup_honors_stricter_ancestor_with_mount_root_translation():
    values={"/proc/meminfo":meminfo(),"/proc/self/cgroup":"0::/tenant/job\n",
        "/proc/self/mountinfo":"31 20 0:5 /tenant /cg rw - cgroup2 cgroup rw\n",
        "/cg/job/memory.max":str(8*GIB),"/cg/job/memory.current":str(2*GIB),"/cg/job/memory.stat":stat_v2(),
        "/cg/memory.max":str(4*GIB),"/cg/memory.current":str(3*GIB+GIB//2),"/cg/memory.stat":stat_v2(2*GIB,GIB//2)}
    assert probe(values)==2*GIB


@pytest.mark.skipif(board.os.name == "nt", reason="Linux memory telemetry")
def test_namespace_root_and_unlimited_child_do_not_hide_ancestor_limits():
    values={"/proc/meminfo":meminfo(),"/proc/self/cgroup":"0::/\n",
        "/proc/self/mountinfo":"31 20 0:5 /.. /cg rw - cgroup2 cgroup rw\n",
        "/cg/memory.max":str(2*GIB),"/cg/memory.current":str(GIB),"/cg/memory.stat":stat_v2()}
    assert probe(values)==GIB
    values["/proc/self/cgroup"]="0::/parent/child\n"
    values["/proc/self/mountinfo"]="31 20 0:5 / /cg rw - cgroup2 cgroup rw\n"
    values["/cg/memory.max"]="max"
    values.update({"/cg/parent/child/memory.max":"max","/cg/parent/memory.max":str(3*GIB),
        "/cg/parent/memory.current":str(2*GIB),"/cg/parent/memory.stat":stat_v2()})
    assert probe(values)==GIB


@pytest.mark.skipif(board.os.name == "nt", reason="Linux memory telemetry")
def test_v1_hierarchical_cache_uses_total_dirty_counters_and_parent_limits():
    values={"/proc/meminfo":meminfo(),"/proc/self/cgroup":"5:cpu,cpuacct:/\n6:memory:/team/job\n",
        "/proc/self/mountinfo":"40 20 0:8 / /cg\\040mem rw - cgroup cgroup rw,memory\n",
        "/cg mem/team/job/memory.limit_in_bytes":str(8*GIB),"/cg mem/team/job/memory.usage_in_bytes":str(GIB),
        "/cg mem/team/memory.limit_in_bytes":str(4*GIB),"/cg mem/team/memory.usage_in_bytes":str(3*GIB+GIB//2),
        "/cg mem/team/memory.stat":f"inactive_file {3*GIB}\ndirty 0\nwriteback 0\ntotal_inactive_file {3*GIB}\ntotal_dirty {2*GIB+GIB//2}\ntotal_writeback 0\ntotal_mapped_file 0\ntotal_shmem 0\n"}
    assert probe(values)==GIB
    # Incomplete hierarchical stats must not mix in the local dirty=0 value.
    values["/cg mem/team/memory.stat"]=values["/cg mem/team/memory.stat"].replace(f"total_dirty {2*GIB+GIB//2}\n", "")
    assert probe(values)==GIB//2


@pytest.mark.skipif(board.os.name == "nt", reason="Linux memory telemetry")
@pytest.mark.parametrize("host",["", "MemAvailable: invalid kB", "MemAvailable: 99999999999999 kB\nMemTotal: 1 kB\nDirty: 0 kB\nWriteback: 0 kB", "MemAvailable: 1 MB\nDirty: 0 kB\nWriteback: 0 kB"])
def test_malformed_host_estimates_use_free_pages_fallback(host):
    assert probe({"/proc/meminfo":host})==GIB


def test_cache_credit_does_not_include_active_file_or_kernel_reclaimable_bytes():
    text=stat_v2(100,dirty=40,writeback=30,mapped=10,shmem=5)+"active_file 999999\nslab_reclaimable 999999\n"
    assert board._clean_inactive_cgroup_file_bytes(text,1000,version=2)==15


def test_resolved_worker_observability_reports_the_unchanged_safety_budget(capsys):
    with patch.object(board,"_available_image_record_memory_bytes",return_value=8*GIB), patch.dict(board.os.environ,{"EDB_IMAGE_RECORD_WORKERS":""}), patch.object(board.os,"cpu_count",return_value=16):
        assert board._resolve_image_record_worker_count(16,source_pixels=8_000_000)==2
    output=capsys.readouterr().out
    assert "workers=2 records=16 source_pixels_max=8000000" in output
    assert "budget_bytes=1610612736 per_worker_bytes=768000000" in output
