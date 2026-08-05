"""Single-instance guard: prevent two app services from running at once."""
import ctypes

_mutex_handle = None
_MUTEX_NAME = "Local\\ItsOurCryWorkbenchSingleInstance"
_ERROR_ALREADY_EXISTS = 183


def acquire_single_instance_mutex():
    global _mutex_handle
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True
    except Exception:
        return True
