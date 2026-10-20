"""Pure ACL guard checks: no system Keychain access or credentials."""
from copy import deepcopy
import pytest
from scripts.jev_keychain_access_probe import security_partition_plan


def acl():
    return [{'authorizations':['ACLAuthorizationDecrypt'],'all_applications':False,'applications':['/usr/bin/security']},
            {'authorizations':['ACLAuthorizationPartitionID'],'all_applications':True,'applications':[],'partitions':['apple:']}]


def test_preserves_existing_partition_and_adds_only_system_tool_partition():
    original=acl();snapshot=deepcopy(original)
    assert security_partition_plan(original)==['apple:','apple-tool:']
    assert original==snapshot


def test_idempotent_item_partition_repair():
    values=acl();values[1]['partitions'].append('apple-tool:')
    assert security_partition_plan(values)==['apple:','apple-tool:']


@pytest.mark.parametrize('mutation',[
    lambda a:a[0].update(all_applications=True),
    lambda a:a[0].update(applications=[]),
    lambda a:a[0].update(applications=['/usr/bin/security','/usr/bin/python3']),
    lambda a:a[1].update(partitions=['teamid:unrelated']),
    lambda a:a.pop(1),
    lambda a:a.append(deepcopy(a[0])),
])
def test_unexpected_or_broad_decrypt_acl_is_never_modified(mutation):
    values=acl();mutation(values)
    with pytest.raises(ValueError):security_partition_plan(values)
