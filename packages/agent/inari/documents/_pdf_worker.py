from __future__ import annotations

import ctypes
import ctypes.util
import errno
import json
import sys
from dataclasses import asdict

from ._pdf_policy import MAX_INPUT_BYTES, PdfPolicyError, inspect_pdf


def _confine_linux() -> None:
    import resource

    library_name = ctypes.util.find_library("seccomp")
    if library_name is None:
        raise OSError("libseccomp is required for PDF preflight.")
    library = ctypes.CDLL(library_name, use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    library.seccomp_rule_add.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_load.restype = ctypes.c_int
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))

    context = library.seccomp_init(0x00050000 | errno.EPERM)
    if not context:
        raise OSError("The PDF syscall policy could not be allocated.")
    try:
        # No open, socket, clone, exec, or namespace syscall can cross this policy.
        for name in (
            "read",
            "write",
            "close",
            "fstat",
            "newfstatat",
            "lseek",
            "brk",
            "mmap",
            "mmap2",
            "mprotect",
            "munmap",
            "mremap",
            "madvise",
            "futex",
            "futex_time64",
            "rt_sigaction",
            "rt_sigprocmask",
            "rt_sigreturn",
            "sigaltstack",
            "clock_gettime",
            "clock_gettime64",
            "gettimeofday",
            "getpid",
            "gettid",
            "getrusage",
            "getrandom",
            "sched_yield",
            "exit",
            "exit_group",
        ):
            number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if (
                number >= 0
                and library.seccomp_rule_add(context, 0x7FFF0000, number, 0) != 0
            ):
                raise OSError("The PDF syscall policy could not be installed.")
        if library.seccomp_load(context) != 0:
            raise OSError("The PDF syscall policy could not be activated.")
    finally:
        library.seccomp_release(context)


def main() -> int:
    try:
        if sys.platform != "linux":
            return 3
        _confine_linux()
    except (OSError, ValueError):
        return 3
    try:
        dpi = int(sys.argv[1]) if len(sys.argv) == 2 else 300
        content = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        result = inspect_pdf(content, dpi=dpi)
        sys.stdout.write(json.dumps(asdict(result), separators=(",", ":")))
        return 0
    except (PdfPolicyError, MemoryError, ValueError, RecursionError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
