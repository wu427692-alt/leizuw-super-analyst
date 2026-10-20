#!/usr/bin/env python3
"""Inspect metadata for the dedicated Jev item; never print item data.

By default this only reads ACL metadata. Optional normal keychain unlock takes
its password from stdin only. --authorize-security uses password authorization
to append apple-tool: to this dedicated item's partition ACL after verifying
that its decrypt ACL allows only /usr/bin/security. Global keychain policy and
all other items are unchanged. A successful repair verifies retrieval into a
private pipe, discards the data and reports only a boolean.
"""
import argparse
import ctypes as c
import json
import plistlib
import subprocess
import sys


def security_partition_plan(acls):
    decrypt = [a for a in acls if 'ACLAuthorizationDecrypt' in a['authorizations']]
    partitions = [a for a in acls if 'ACLAuthorizationPartitionID' in a['authorizations']]
    if len(decrypt) != 1 or decrypt[0]['all_applications'] or decrypt[0]['applications'] != ['/usr/bin/security']:
        raise ValueError('Unexpected dedicated item application ACL')
    if len(partitions) != 1 or set(partitions[0].get('partitions', [])) - {'apple:', 'apple-tool:'}:
        raise ValueError('Unexpected dedicated item partition ACL')
    old = partitions[0].get('partitions', [])
    if not old:
        raise ValueError('Missing dedicated item partition ACL')
    return list(dict.fromkeys([*old, 'apple-tool:']))


def probe(path, service, account, password=None, authorize_security=False):
    p = c.c_void_p
    sec = c.CDLL('/System/Library/Frameworks/Security.framework/Security')
    cf = c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    signatures = {
        'SecKeychainOpen': [c.c_char_p, c.POINTER(p)],
        'SecKeychainGetStatus': [p, c.POINTER(c.c_uint32)],
        'SecKeychainUnlock': [p, c.c_uint32, c.c_char_p, c.c_ubyte],
        'SecKeychainFindGenericPassword': [p, c.c_uint32, c.c_char_p, c.c_uint32, c.c_char_p, c.POINTER(c.c_uint32), c.POINTER(p), c.POINTER(p)],
        'SecKeychainItemCopyAccess': [p, c.POINTER(p)],
        'SecAccessCopyACLList': [p, c.POINTER(p)],
        'SecACLCopyContents': [p, c.POINTER(p), c.POINTER(p), c.POINTER(c.c_uint32)],
        'SecTrustedApplicationCopyData': [p, c.POINTER(p)],
        'SecKeychainSetUserInteractionAllowed': [c.c_ubyte],
        'SecACLSetContents': [p, p, p, c.c_uint32],
        'SecKeychainItemSetAccessWithPassword': [p, p, c.c_uint32, c.c_char_p],
    }
    for name, args in signatures.items():
        getattr(sec, name).argtypes = args
    sec.SecACLCopyAuthorizations.argtypes = [p]; sec.SecACLCopyAuthorizations.restype = p
    cf.CFArrayGetCount.argtypes = [p]; cf.CFArrayGetCount.restype = c.c_long
    cf.CFArrayGetValueAtIndex.argtypes = [p, c.c_long]; cf.CFArrayGetValueAtIndex.restype = p
    cf.CFStringGetCString.argtypes = [p, c.c_char_p, c.c_long, c.c_uint32]
    cf.CFStringCreateWithCString.argtypes = [p, c.c_char_p, c.c_uint32]
    cf.CFStringCreateWithCString.restype = p
    cf.CFDataGetLength.argtypes = [p]; cf.CFDataGetLength.restype = c.c_long
    cf.CFDataGetBytePtr.argtypes = [p]; cf.CFDataGetBytePtr.restype = p
    cf.CFRelease.argtypes = [p]
    refs = []
    def own(value):
        if value: refs.append(value)
        return value
    def string(value):
        buf=c.create_string_buffer(16384)
        return buf.value.decode('utf-8','replace') if value and cf.CFStringGetCString(value,buf,len(buf),0x08000100) else ''
    result = {}
    # Suppress a diagnostic GUI prompt; this does not grant access or alter policy.
    sec.SecKeychainSetUserInteractionAllowed(False)
    try:
        keychain=p();result['open_status']=sec.SecKeychainOpen(path.encode(),c.byref(keychain))
        if result['open_status']:return result
        own(keychain.value)
        if password is not None:
            result['unlock_status']=sec.SecKeychainUnlock(keychain,len(password),password,True)
        status=c.c_uint32();result['get_status']=sec.SecKeychainGetStatus(keychain,c.byref(status))
        result['unlocked']=bool(status.value&1)
        item=p();sb,ab=service.encode(),account.encode()
        result['find_metadata_status']=sec.SecKeychainFindGenericPassword(keychain,len(sb),sb,len(ab),ab,None,None,c.byref(item))
        if result['find_metadata_status']:return result
        own(item.value)
        access=p();result['copy_access_status']=sec.SecKeychainItemCopyAccess(item,c.byref(access))
        if result['copy_access_status']:return result
        own(access.value)
        acls=p();result['copy_acl_status']=sec.SecAccessCopyACLList(access,c.byref(acls))
        if result['copy_acl_status']:return result
        own(acls.value);result['acls']=[];partition_entry=None
        for i in range(cf.CFArrayGetCount(acls)):
            acl=cf.CFArrayGetValueAtIndex(acls,i); apps=p();desc=p();selector=c.c_uint32()
            status=sec.SecACLCopyContents(acl,c.byref(apps),c.byref(desc),c.byref(selector))
            own(apps.value);own(desc.value)
            tags=own(sec.SecACLCopyAuthorizations(acl))
            entry={'status':status,'prompt_selector':selector.value,
                   'authorizations':[string(cf.CFArrayGetValueAtIndex(tags,j)) for j in range(cf.CFArrayGetCount(tags))] if tags else [],
                   'all_applications':not bool(apps.value),'applications':[]}
            if apps.value:
                for j in range(cf.CFArrayGetCount(apps)):
                    payload=p();s=sec.SecTrustedApplicationCopyData(cf.CFArrayGetValueAtIndex(apps,j),c.byref(payload));own(payload.value)
                    if not s:
                        raw=c.string_at(cf.CFDataGetBytePtr(payload),cf.CFDataGetLength(payload)).rstrip(b'\0')
                        entry['applications'].append(raw.decode('utf-8') if raw.startswith(b'/') and b'\0' not in raw else '[opaque application identity]')
            description=string(desc)
            try:
                parsed=plistlib.loads(bytes.fromhex(description))
                entry['partitions']=parsed.get('Partitions',[])
            except Exception:
                entry['description_matches_service']=description==service
            result['acls'].append(entry)
            if 'ACLAuthorizationPartitionID' in entry['authorizations']:
                partition_entry=(acl,apps.value,selector.value)
        if authorize_security:
            if service != 'codex.jev-use.worldseek' or account != 'wuyinle' or not password:
                raise ValueError('This repair is restricted to the dedicated authorized relay item')
            planned=security_partition_plan(result['acls'])
            acl,apps,selector=partition_entry
            encoded=plistlib.dumps({'Partitions':planned},fmt=plistlib.FMT_XML).hex().encode()
            description=own(cf.CFStringCreateWithCString(None,encoded,0x08000100))
            result['set_partition_contents_status']=sec.SecACLSetContents(acl,apps,description,selector)
            if not result['set_partition_contents_status']:
                result['authorized_save_status']=sec.SecKeychainItemSetAccessWithPassword(item,access,len(password),password)
                if not result['authorized_save_status']:
                    read=subprocess.run(['/usr/bin/security','find-generic-password','-s',service,'-a',account,'-w',path],
                                        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10)
                    result['dedicated_read_status']=read.returncode
                    result['dedicated_read_success']=read.returncode==0 and bool(read.stdout.strip())
                    read.stdout=b''
            result['requested_partitions']=planned
        return result
    finally:
        password=None
        for ref in reversed(refs):cf.CFRelease(ref)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keychain',required=True);parser.add_argument('--service',required=True)
    parser.add_argument('--account',required=True);parser.add_argument('--unlock',action='store_true')
    parser.add_argument('--authorize-security',action='store_true',help='Password-authorized, item-only apple-tool partition repair')
    args=parser.parse_args()
    password=None
    if args.unlock:
        print('READY_FOR_KEYCHAIN_PASSWORD_STDIN',flush=True)
        password=sys.stdin.buffer.readline(4097).rstrip(b'\r\n')
        if not password:raise SystemExit('No password input received')
    try:print(json.dumps(probe(args.keychain,args.service,args.account,password,args.authorize_security)))
    except Exception:raise SystemExit('Dedicated Keychain metadata inspection failed')
    finally:password=None
