"""Keep QR login and daily visits from opening the same Chromium profile."""
from pathlib import Path


class ProfileBusyError(Exception):
    pass


def acquire_profile_lock(account_dir):
    import fcntl
    handle = (Path(account_dir) / 'profile.lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise ProfileBusyError from None
    return handle
