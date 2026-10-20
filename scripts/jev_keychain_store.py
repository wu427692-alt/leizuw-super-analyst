#!/usr/bin/env python3
"""Add a dedicated macOS Keychain item from stdin without secret argv/files.

Existing items are never overwritten. Only /usr/bin/security is preauthorized
to retrieve the item for the host relay; no allow-all-applications ACL is used.
"""
import argparse
import ctypes as c
import sys


def unlock_keychain(path, password):
    sec = c.CDLL("/System/Library/Frameworks/Security.framework/Security")
    cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    keychain = c.c_void_p()
    sec.SecKeychainOpen.argtypes = [c.c_char_p, c.POINTER(c.c_void_p)]
    sec.SecKeychainUnlock.argtypes = [c.c_void_p, c.c_uint32, c.c_char_p, c.c_ubyte]
    cf.CFRelease.argtypes = [c.c_void_p]
    cf.CFRelease.restype = None
    status = sec.SecKeychainOpen(path.encode(), c.byref(keychain))
    if status:
        return status
    try:
        return sec.SecKeychainUnlock(keychain, len(password), password, True)
    finally:
        cf.CFRelease(keychain)


def add_item(service, account, secret):
    cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    sec = c.CDLL("/System/Library/Frameworks/Security.framework/Security")
    pointer = c.c_void_p
    cf.CFStringCreateWithCString.argtypes = [pointer, c.c_char_p, c.c_uint32]
    cf.CFStringCreateWithCString.restype = pointer
    cf.CFDataCreate.argtypes = [pointer, c.c_char_p, c.c_long]
    cf.CFDataCreate.restype = pointer
    cf.CFArrayCreate.argtypes = [pointer, c.POINTER(pointer), c.c_long, pointer]
    cf.CFArrayCreate.restype = pointer
    cf.CFDictionaryCreate.argtypes = [pointer, c.POINTER(pointer), c.POINTER(pointer), c.c_long, pointer, pointer]
    cf.CFDictionaryCreate.restype = pointer
    cf.CFRelease.argtypes = [pointer]
    cf.CFRelease.restype = None
    sec.SecTrustedApplicationCreateFromPath.argtypes = [c.c_char_p, c.POINTER(pointer)]
    sec.SecAccessCreate.argtypes = [pointer, pointer, c.POINTER(pointer)]
    sec.SecItemAdd.argtypes = [pointer, c.POINTER(pointer)]
    objects = []

    def retain(value):
        if not value:
            raise RuntimeError("Keychain object creation failed")
        objects.append(value)
        return value

    def string(value):
        return retain(cf.CFStringCreateWithCString(None, value.encode(), 0x08000100))

    def constant(name):
        return pointer.in_dll(sec, name).value

    try:
        trusted = pointer()
        status = sec.SecTrustedApplicationCreateFromPath(b"/usr/bin/security", c.byref(trusted))
        if status:
            return status
        retain(trusted.value)
        allowed = retain(cf.CFArrayCreate(None, (pointer * 1)(trusted.value), 1, None))
        access = pointer()
        status = sec.SecAccessCreate(string(service), allowed, c.byref(access))
        if status:
            return status
        retain(access.value)
        pairs = [(constant("kSecClass"), constant("kSecClassGenericPassword")),
                 (constant("kSecAttrService"), string(service)),
                 (constant("kSecAttrAccount"), string(account)),
                 (constant("kSecValueData"), retain(cf.CFDataCreate(None, secret, len(secret)))),
                 (constant("kSecAttrAccess"), access.value)]
        keys = (pointer * len(pairs))(*(pair[0] for pair in pairs))
        values = (pointer * len(pairs))(*(pair[1] for pair in pairs))
        attributes = retain(cf.CFDictionaryCreate(None, keys, values, len(pairs), None, None))
        return sec.SecItemAdd(attributes, None)
    finally:
        for item in reversed(objects):
            cf.CFRelease(item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--unlock-keychain", help="Optional login keychain path; its password is the first stdin frame")
    args = parser.parse_args()
    if args.unlock_keychain:
        password = sys.stdin.buffer.readline(4097).rstrip(b"\r\n")
        status = unlock_keychain(args.unlock_keychain, password)
        del password
        if status:
            raise SystemExit(f"Keychain was not unlocked (OSStatus {status})")
    secret = sys.stdin.buffer.readline(4097).rstrip(b"\r\n")
    if sys.platform != "darwin" or not 8 <= len(secret) <= 4096 or any(char < 33 or char > 126 for char in secret):
        raise SystemExit("Invalid platform or credential frame")
    status = add_item(args.service, args.account, secret)
    del secret
    if status:
        raise SystemExit(f"Keychain item was not added (OSStatus {status})")
    print("Keychain item added; credential not printed")


if __name__ == "__main__":
    main()
