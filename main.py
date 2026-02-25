import os
import hcloud


def get_pool(hclient: hcloud.Client, label="ready") -> []:
    ready = []
    for server in hclient.servers.get_all():
        for l in server.labels:
            if l == label:
                ready.append(server)
    return ready


def main():
    hetzner_api_token = os.environ.get("HETZNER_API_TOKEN")
    hclient = hcloud.Client(token=hetzner_api_token)

    ready = get_pool(hclient, label="ready")
    print("available servers:")
    [print(s.name) for s in ready]


if __name__ == "__main__":
    main()
