import argparse
from fabric.connection import Connection
import hcloud
import os
import sysrsync
import time


def get_pool(hclient: hcloud.Client, label="ready") -> [hcloud.servers.client.BoundServer]:
    servers = []
    for server in hclient.servers.get_all():
        if server.labels.get("state") == label:
            servers.append(server)
    return servers


def install_dependencies(ssh):
    command = "apt update"
    print("\n+++ " + command)
    ssh.run(command)
    command = "apt install -y git python3.11-venv python3-dev gcc"
    print("\n+++ " + command)
    ssh.run(command)


def deploy(vps: hcloud.servers.client.BoundServer, ipv4: str):
    """Deploys a chatmail relay via SSH and runs tests."""
    ssh = Connection(
        host=ipv4,
        user="root",
    )
    #install_dependencies(ssh)

    command = "cd relay && scripts/initenv.sh"
    print("\n+++ " + command)
    ssh.run(command)

    command = f"cd relay && scripts/cmdeploy init {vps.name} || true"
    print("\n+++ " + command)
    ssh.run(command)

    command = f"cd relay && scripts/cmdeploy run --ssh-host @local"
    print("\n+++ " + command)
    ssh.run(command)

    ssh.close()  # SSH session needs to be re-opened so test_timezone_env doesn't fail


def run_tests(vps: hcloud.servers.client.BoundServer, ipv4: str, domain2=""):
    ssh.open()
    if domain2:
        domain2 = "CHATMAIL_DOMAIN2=" + domain2
    command = f"cd relay && {domain2} scripts/cmdeploy test --ssh-host @local --slow"
    print("\n+++ " + command)
    ssh.run(command)
    ssh.close()


def rebuild_vps(ipv4: str, vps: hcloud.servers.client.BoundServer):
    """Rebuilds a VPS after a finished CI run."""
    print("\n+++ rebuilding VPS")
    # XXX download /var/lib/acme and /etc/dkimkeys
    vps.rebuild(image=hcloud.images.Image("debian-12"))
    time.sleep(10)

    print(f"\n+++ resetting SSH Host Key for {ipv4}")
    os.system(f"ssh-keygen -R {ipv4}")
    ssh = Connection(
        host=ipv4,
        user="root",
        connect_timeout = 180  # wait until VPS is rebuilt
    )
    # XXX re-upload /var/lib/acme and /etc/dkimkeys
    install_dependencies(ssh)


def main():
    """Get a ready VPS from a Hetzner project and deploy chatmail/relay to it."""

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "relay_repo",
        help="path to your local chatmail/relay repository",
    )
    parser.add_argument(
        "--hetzner-api-token",
        default=os.environ.get("HETZNER_API_TOKEN"),
        help="path to your local chatmail/relay repository",
    )
    parser.add_argument(
        "--domain2",
        default=os.environ.get("CHATMAIL_DOMAIN2", "ci-chatmail.testrun.org"),
        help="a second chatmail domain to run test against",
    )
    parser.add_argument(
        "--ssh-host",
        default=None,
        help="the SSH host you want to connect to",
    )
    parser.add_argument(
        "-i", "--ssh-private-key",
        default=os.environ.get("SSH_PRIVATE_KEYFILE", "~/.ssh/staging.testrun.org"),
        help="path to the private SSH key you want to login with",
    )
    parser.add_argument(
        "--deploy",
        default=False,
        action="store_true",
        help="Deploy chatmail/relay to the allocated VPS",
    )
    parser.add_argument(
        "--test",
        default=False,
        action="store_true",
        help="Test chatmail/relay on the allocated VPS. Implies --deploy",
    )
    parser.add_argument(
        "--keep",
        default=False,
        action="store_true",
        help="Don't rebuild the VPS after a successful test",
    )
    args = parser.parse_args()

    if args.test:
        args.deploy = True

    hclient = hcloud.Client(token=args.hetzner_api_token)
    ready = get_pool(hclient)
    print("+++ available servers:")
    [print(s.name) for s in ready]
    try:
        vps = ready[0]
    except IndexError:
        while len(ready) < 1:
            print("no servers available. Waiting 15 seconds...")
            time.sleep(15)
            ready = get_pool(hclient)
        vps = ready[0]
    ipv4 = vps.public_net.ipv4.ip if not args.ssh_host else args.ssh_host
    print(f"\n+++ using {vps.name} for deployment\n")

    if args.deploy:
        try:
            print(f"+++ uploading relay repository from {relay_repo}")
            vps = vps.update(labels={"state":"deploying"})
            sysrsync.run(
                source=args.relay_repo,
                destination="/root/relay",
                exclusions=[".tox", "venv"],
                destination_ssh="root@" + ipv4,
                options=["-r"],
                sync_source_contents=True,
                strict_host_key_checking=False,
                private_key=args.ssh_private_key,
            )
            deploy(vps, ipv4)
            if args.test:
                run_tests(vps, ipv4, args.domain2)
                vps = vps.update(labels={"state":"successful"})
        except Exception as e:
            vps = vps.update(labels={"state":"failed"})
            raise e
    if not args.keep:
        rebuild_vps(ipv4, vps)
        vps = vps.update(labels={"state":"ready"})


if __name__ == "__main__":
    main()

