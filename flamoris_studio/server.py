"""Container entry point with an explicit trusted proxy boundary."""

import ipaddress
import os

import uvicorn


def trusted_proxy_ips(value: str) -> str:
    addresses = [entry.strip() for entry in value.split(",") if entry.strip()]
    normalized = []
    for entry in addresses:
        try:
            network = ipaddress.ip_network(entry, strict=False)
            normalized.append(str(network) if "/" in entry else str(ipaddress.ip_address(entry)))
        except ValueError:
            raise ValueError("STUDIO_TRUSTED_PROXY_IPS must contain explicit IPs/CIDRs") from None
    return ",".join(normalized)


def main():
    workers = int(os.getenv("STUDIO_WORKERS", "1"))
    if not 1 <= workers <= 16:
        raise ValueError("STUDIO_WORKERS must be between 1 and 16")
    uvicorn.run("flamoris_studio.app:production_app", factory=True, host="0.0.0.0", port=5087,
                workers=workers, proxy_headers=True,
                forwarded_allow_ips=trusted_proxy_ips(os.getenv(
                    "STUDIO_TRUSTED_PROXY_IPS", "127.0.0.1,::1")))


if __name__ == "__main__":
    main()
