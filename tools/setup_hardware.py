"""Dependency-free installation planning. Never infer CUDA support from GPU names."""
from __future__ import annotations

import csv
import ctypes
import io
import platform
import shutil
import subprocess
from pathlib import Path


def parse_gpus(output):
    result = []
    for row in csv.reader(io.StringIO(output)):
        if len(row) != 5:
            raise ValueError('GPU 정보를 읽지 못했습니다. NVIDIA 드라이버를 업데이트하세요.')
        index, name, memory, capability, driver = (s.strip() for s in row)
        major, minor = map(int, capability.split('.'))
        result.append(dict(index=int(index), name=name, memory_mb=int(float(memory)),
                           capability=[major, minor], driver=driver))
    return result


def make_plan(gpus, free_gb, ram_gb, gpu_index=None):
    if not gpus:
        raise ValueError('NVIDIA CUDA GPU가 필요합니다. 현재 CPU/AMD/Apple 설치는 지원하지 않습니다.')
    candidates = [g for g in gpus if g['capability'] >= [6, 0]]
    if gpu_index is not None:
        candidates = [g for g in candidates if g['index'] == gpu_index]
    if not candidates:
        raise ValueError('선택할 수 있는 CUDA GPU가 없습니다. compute capability 6.0 이상이 필요합니다.')
    gpu = max(candidates, key=lambda g: (g['memory_mb'], g['capability']))
    sm = gpu['capability'][0] * 10 + gpu['capability'][1]
    if sm > 120:
        raise ValueError('이 GPU는 고정 CUDA 12.9 빌드의 검증 범위를 벗어납니다. 설치 사양 업데이트가 필요합니다.')
    modern = sm >= 100
    warnings = []
    if gpu['memory_mb'] < 12000:
        warnings.append('VRAM 12GB 미만: Fish S2-Pro 등은 설치 후에도 메모리 부족으로 합성에 실패할 수 있습니다.')
    if sm < 80:
        warnings.append('이 GPU는 BF16 하드웨어 지원이 없습니다. VoxCPM2/Fish의 실제 합성 성공을 별도로 확인합니다.')
    if ram_gb < 30:
        warnings.append('RAM 32GB 미만: 대형 모델 로딩이 느리거나 실패할 수 있습니다.')
    return dict(gpu=gpu, architecture=str(sm), torch='2.8.0' if modern else '2.6.0',
                cuda='cu128' if modern else 'cu126', zonos_quant='q8_0' if gpu['memory_mb'] >= 12000 else 'q4_k',
                free_gb=round(free_gb, 1), ram_gb=round(ram_gb, 1), warnings=warnings)


def detect(root: Path, gpu_index=None):
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise ValueError('이 설치기는 Windows 10/11 64비트 전용입니다.')
    command = ['nvidia-smi', '--query-gpu=index,name,memory.total,compute_cap,driver_version',
               '--format=csv,noheader,nounits']
    try:
        output = subprocess.check_output(command, text=True, encoding='utf-8', timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('nvidia-smi를 실행할 수 없습니다. NVIDIA 드라이버 설치 후 다시 실행하세요.') from error

    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ('total', 'available', 'page', 'available_page',
                                                  'virtual', 'available_virtual', 'extended')]
    memory = MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise OSError('RAM 용량 확인 실패')
    return make_plan(parse_gpus(output), shutil.disk_usage(root).free / 2**30,
                     memory.total / 2**30, gpu_index)
