# hetzner-relay

This script can be used to obtain a fresh VPS from a hetzner project,
deploy a local checkout of the https://github.com/chatmail/relay/ repository to it,
and rebuild it if the deployment was successful,
so it can be reused for another deployment.

## Usage

```
git clone https://github.com/chatmail/relay
git clone https://github.com/chatmail/hetzner-relay
cd hetzner-relay
export HETZNER_API_TOKEN=<token>
uv run main.py ../relay
```
