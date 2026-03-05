# hetzner-relay

This script can be used to allocate a fresh VPS from a hetzner project,
deploy a local checkout of the https://github.com/chatmail/relay/ repository to it,
test it with the appropriate DNS settings,
and rebuild it
so it can be reused for another deployment.

## Getting Started

```
git clone https://github.com/chatmail/relay
git clone https://github.com/chatmail/hetzner-relay
cd hetzner-relay
export HETZNER_API_TOKEN=<token>
uv run main.py ../relay --test --rebuild
```

### Usage

The script has 5 sections. Four of them, you have enable by CLI flags:

* First, it will allocate a VPS from a Hetzner project.
* Then, if `--deploy` is passed, it will deploy chatmail/relay to the VPS (implied by `--dns` and `--test`)
* Then, if `--dns` is passed, it will deploy the DNS records to a name server
* Then, if `--test` is passed, it will run the chatmail/relay tests
* Then, if `--rebuild` is passed, it will rebuild the VPS so others can use it.

### Pre-Necessities

#### Hetzner Console Project with ready VPS

To run this script,
you need access to a Hetzner Console Project
with at least one pre-ordered Virtual Private Server (VPS).

A usable VPS in the project MUST:
- have a public IPv4 address,
- have an A record with a domain name pointed to it,
- have a CNAME record for `www` pointed to it,
- have a CNAME record for `mta-sts` pointed to it,
- be named after its domain name.

Either pass the server's name to the script
with the `--vps` flag,
or give it a `state:ready` label,
so the script can use it.

#### Hetzner API Token

To obtain a Hetzner API Token,
go to the Hetzner Console Project's "Security > API tokens" settings
(e.g. <https://console.hetzner.com/projects/2718696/security/tokens>),
and generate a new one.

You can pass it via
`HETZNER_API_TOKEN` environment variable
or the `--hetzner-api-token` flag.

#### SSH Access

For `--deploy`, `--test`, and `--dns`,
your computer needs root SSH access to the VPS.
You can tell the script the path to the key with `-i PATH`.

If you have access to delta's pass repository,
you can find an SSH key at
`pass delta/staging.testrun.org/github-actions/STAGING_SSH_KEY`.

Hetzner automatically rebuilds a VPS
with the SSH keys that were selected during the purchase.

#### DNS Server

For deploying DNS records dynamically during the test with `--dns`,
you can pass a DNS server.
It needs to provide root access with the same SSH key as the VPS itself.

If you only pass `--dns` the script will try to use `ns.testrun.org`.

### Caching ACME & DKIM state

We try to cache ACME & DKIM state between runs:
1. to avoid running into [Let's Encrypt Rate Limits](https://letsencrypt.org/docs/rate-limits/),
2. and to avoid flaky tests in case DKIM DNS records don't propagate quickly enough.

If `--dns` is supplied,
the script will try to use it as a cache.
Otherwise it will cache the state locally.
