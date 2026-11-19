import platform

# Fill platform's uname cache with the real machine before any test patches sys.platform.
# On Windows a first uname() call under a fake "darwin" caches system="darwin" and machine="",
# which then leaks into every later test in the run (bug-report arch, update arch checks).
platform.uname()
