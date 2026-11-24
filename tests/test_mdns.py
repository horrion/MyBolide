from mybolide.mdns import SERVICE_TYPE, build_service_info, local_ipv4_addresses


def test_service_info_advertises_the_sync_api():
    info = build_service_info(8741, ["192.0.2.10"])
    assert info.type == SERVICE_TYPE
    assert info.port == 8741
    assert info.properties[b"path"] == b"/v1/sync"
    assert info.properties[b"version"] == b"1"


def test_local_addresses_are_strings():
    addresses = local_ipv4_addresses()
    assert isinstance(addresses, list)
    assert all(isinstance(item, str) for item in addresses)
