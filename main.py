import argparse
from fabric.connection import Connection
import hcloud
import hcloud.images
import hcloud.servers.client
import os
import socket
import sysrsync
import sysrsync.exceptions
import time
import traceback


def allocate_vps(hclient: hcloud.Client, vps_name: str, run_id: str) -> hcloud.servers.client.BoundServer:
    """Allocate a VPS from a Hetzner pool

    :param hclient: the Hetzner client object
    :param vps_name: part of the name attribute of the VPS the user wants to use
    :param run_id: the UID of this run
    :return: the VPS object
    """
    ready = get_pool(hclient, name=vps_name)
    print("+++ available servers:")
    [print(s.name) for s in ready]
    try:
        vps = ready[0]
        for ready_vps in ready:
            if vps_name in ready_vps.name:
                vps = ready_vps
    except IndexError:
        while len(ready) < 1:
            print("no servers available. Waiting 15 seconds...")
            time.sleep(15)
            ready = get_pool(hclient)
        vps = ready[0]
        for ready_vps in ready:
            if vps_name in ready_vps.name:
                vps = ready_vps
    vps = vps.update(labels={"state": "deploying", "run": run_id})
    return vps


def get_pool(hclient: hcloud.Client, label="ready", name=None) -> [hcloud.servers.client.BoundServer]:
    """Get a VPS by label from a Hetzner project, or a very specific VPS by name (even if it isn't ready).

    :param hclient: the Hetzner client object
    :param label: the label to filter for
    :param name: the exact name attribute of a Hetzner VPS
    :return: the VPS object
    """
    servers = []
    for vps in hclient.servers.get_all():
        if name == vps.name:
            return [vps]
        if vps.labels.get("state") == label:
            servers.append(vps)
    return servers


def install_dependencies(ssh):
    command = "apt update"
    print("\n+++ " + command)
    ssh.run(command)
    command = "apt install -y git python3.11-venv python3-dev gcc"
    print("\n+++ " + command)
    ssh.run(command)


def deploy(vps: hcloud.servers.client.BoundServer, ipv4: str, ssh_args: dict):
    """Deploys a chatmail relay via SSH and runs tests.

    :param vps: the Hetzner VPS object
    :param ipv4: the IP address of the VPS
    :param ssh_args: a dictionary with kwargs for paramiko.client.SSHClient.connect
    """
    ssh = Connection(
        host=ipv4,
        user="root",
        connect_kwargs=ssh_args,
    )
    install_dependencies(ssh)

    command = "cd relay && scripts/initenv.sh"
    print("\n+++ " + command)
    ssh.run(command)

    command = f"cd relay && scripts/cmdeploy init {vps.name} || true"
    print("\n+++ " + command)
    ssh.run(command)

    command = "sed -i 's/^# mtail_address/mtail_address/' relay/chatmail.ini"
    print("\n+++ " + command)
    ssh.run(command)

    command = f"cd relay && scripts/cmdeploy run --ssh-host @local"
    print("\n+++ " + command)
    ssh.run(command)

    ssh.close()  # SSH session needs to be re-opened so test_timezone_env doesn't fail


def clean_zone(zone: str) -> str:
    """From a zonefile, remove the line with the CAA record."""
    result = []
    for line in zone.splitlines():
        if not "CAA" in line:
            result.append(line)
    return '\n'.join(result)


def set_dns(ipv4: str, mail_domain: str, dns_server: str, ssh_args: dict):
    """Generate DNS zonefile and upload it to the authoritative DNS server

    :param ipv4: the IPv4 address of the chatmail relay
    :param mail_domain: the mail_domain of the chatmail relay
    :param dns_server: the authoritative DNS server which hosts the relay's records
    :param ssh_args: a dictionary with kwargs for paramiko.client.SSHClient.connect
    """
    ssh = Connection(
        host=ipv4,
        user="root",
        connect_kwargs=ssh_args,
    )

    command = f"cd relay && scripts/cmdeploy dns --zonefile zone --ssh-host @local"
    print("\n+++ " + command)
    ssh.run(command)
    
    command = f"cat relay/zone"
    print("\n+++ " + command)
    result = ssh.run(command)

    complete_zone = f"""
$ORIGIN {mail_domain}.
$TTL 300
@ IN SOA ns.testrun.org. root.nine.testrun.org {int(time.time())} 7200 3600 1209600 3600
@ IN NS ns.testrun.org.
@ IN A {ipv4}
www IN CNAME {mail_domain}.
mta-sts IN CNAME {mail_domain}.
{result.stdout}
"""
    cleaned_zone = clean_zone(complete_zone)

    ssh_dns = Connection(
        host=dns_server,
        user="root",
    )
    print(f"\n+++ Setting the zonefile for {mail_domain} at {dns_server}")
    command = ("echo '" + cleaned_zone + f"' > /etc/nsd/{mail_domain}.zone")
    print(command)
    ssh_dns.run(command)

    command = f"nsd-checkzone {mail_domain} /etc/nsd/{mail_domain}.zone"
    print("\n+++ " + command)
    ssh_dns.run(command)
    
    command = "systemctl reload nsd"
    print("\n+++ " + command)
    ssh_dns.run(command)
    ssh_dns.close()
    ssh.close()


def run_tests(ipv4: str, ssh_args: dict, domain2=""):
    """Run tests on the chatmail relay

    :param ipv4: the IP address of the VPS
    :param ssh_args: a dictionary with kwargs for paramiko.client.SSHClient.connect
    :param domain2: the mail_domain of a second chatmail relay to test against
    """
    ssh = Connection(
        host=ipv4,
        user="root",
        connect_kwargs=ssh_args,
    )
    if domain2:
        domain2 = "CHATMAIL_DOMAIN2=" + domain2
    command = f"cd relay && {domain2} scripts/cmdeploy test --ssh-host @local --slow"
    print("\n+++ " + command)
    ssh.run(command)
    ssh.close()


def pull_cached_state(ipv4: str, vps: hcloud.servers.client.BoundServer, ssh_private_key: str, cache_server: str):
    """Store /etc/dkimkeys and /var/lib/acme, either locally or on the --dns-server.

    :param ipv4: the IPv4 address of the VPS
    :param vps: the VPS object
    :param ssh_private_key: the SSH private key which can login to the VPS
    :param cache_server: a server where we can store the directories between rebuilds
    """
    for path in ["/etc/dkimkeys", "/var/lib/acme"]:
        directory = "/" + path.split("/")[-1]
        print(f"\n+++ downloading {path} to /tmp/pool-state/{vps.name}{directory}")
        sysrsync.run(
            source=path,
            destination="/tmp/pool-state/" + vps.name + directory,
            source_ssh="root@" + ipv4,
            options=["-rlp", "--mkpath"],
            sync_source_contents=True,
            strict_host_key_checking=False,
            private_key=ssh_private_key,
        )
        if cache_server:
            upload_path = "/var/lib/pool-state/" + vps.name + directory
            print(f"+++++ uploading {path} to {cache_server}:{upload_path}")
            sysrsync.run(
                source="/tmp/pool-state/" + vps.name + directory,
                destination=upload_path,
                destination_ssh="root@" + cache_server,
                options=["-rlp", "--mkpath"],
                sync_source_contents=True,
                strict_host_key_checking=False,
                private_key=ssh_private_key,
            )


def push_cached_state(ipv4: str, vps: hcloud.servers.client.BoundServer, ssh_private_key: str, cache_server: str):
    """Store /etc/dkimkeys and /var/lib/acme, either locally or on the --dns-server.

    :param ipv4: the IPv4 address of the VPS
    :param vps: the VPS object
    :param ssh_private_key: the SSH private key which can login to the VPS
    :param cache_server: a server where we can store the directories between rebuilds
    """
    for path in ["/etc/dkimkeys", "/var/lib/acme"]:
        print(f"\n+++ uploading cached {path}")
        directory = "/" + path.split("/")[-1]
        if cache_server:
            cache_path = "/var/lib/pool-state/" + vps.name + directory
            print(f"+++++ downloading {cache_path} from {cache_server} to /tmp/pool-state/{vps.name}{directory}")
            sysrsync.run(
                source=cache_path,
                destination="/tmp/pool-state/" + vps.name + directory,
                source_ssh="root@" + cache_server,
                options=["-rlp", "--mkpath"],
                sync_source_contents=True,
                strict_host_key_checking=False,
                private_key=ssh_private_key,
            )
        print(f"+++++ uploading to {path}")
        sysrsync.run(
            source="/tmp/pool-state/" + vps.name + directory,
            destination=path,
            destination_ssh="root@" + ipv4,
            options=["-rlp", "--mkpath"],
            sync_source_contents=True,
            strict_host_key_checking=False,
            private_key=ssh_private_key,
        )


def rebuild_vps(ipv4: str, vps: hcloud.servers.client.BoundServer, ssh_private_key: str, cache_server: str):
    """Rebuilds a VPS after a finished CI run, caches some state between rebuilds if possible.

    :param ipv4: the IPv4 address of the VPS
    :param vps: the VPS object
    :param ssh_private_key: the SSH private key which can login to the VPS
    :param cache_server: a server where we can store the directories between rebuilds
    """
    try:
        pull_cached_state(ipv4, vps, ssh_private_key, cache_server)
    except sysrsync.exceptions.RsyncError:
        print("WARNING: could not download /etc/dkimkeys and /var/lib/acme to cache")

    print("\n+++ rebuilding VPS")
    vps.rebuild(image=hcloud.images.Image(name="debian-12"))
    time.sleep(10)

    print(f"\n+++ resetting SSH Host Key for {ipv4}")
    os.system(f"ssh-keygen -R {ipv4}")
    ssh = Connection(
        host=ipv4,
        user="root",
        connect_timeout = 300,
        connect_kwargs={"key_filename": ssh_private_key} if ssh_private_key else {},
    )
    print("\n+++ wait until host is rebuilt")
    ssh.run("uptime")  # wait until VPS is rebuilt
    try:
        push_cached_state(ipv4, vps, ssh_private_key, cache_server)
    except sysrsync.exceptions.RsyncError:
        print("WARNING: could not upload /etc/dkimkeys and /var/lib/acme from cache")
    install_dependencies(ssh)


def main():
    """Get a ready VPS from a Hetzner project and deploy chatmail/relay to it."""

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "relay_repo",
        help="path to your local chatmail/relay repository",
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
        "--dns",
        metavar="ns.testrun.org",
        const="ns.testrun.org",
        nargs="?",
        help="Generate a DNS zonefile and deploy it to an authoritative name server, like ns.testrun.org. Implies --deploy",
    )
    parser.add_argument(
        "--rebuild",
        default=False,
        action="store_true",
        help="Rebuild the VPS after a successful test",
    )

    parser.add_argument(
        "--hetzner-api-token",
        default=os.environ.get("HETZNER_API_TOKEN"),
        help="the API token to a Hetzner console project",
    )
    parser.add_argument(
        "--run-id",
        default=socket.gethostname(),
        help="a unique ID for the CI run, to lock a specific VPS for your usage.",
    )
    parser.add_argument(
        "--vps",
        dest="vps_name",
        default="",
        help="the name of a hetzner VPS to use",
    )

    parser.add_argument(
        "-i", "--ssh-private-key",
        metavar="PATH",
        default=os.environ.get("SSH_PRIVATE_KEYFILE", "~/.ssh/staging.testrun.org"),
        help="path to the private SSH key you want to login with",
    )
    parser.add_argument(
        "--ssh-host",
        default=None,
        help="the SSH host you want to connect to",
    )

    parser.add_argument(
        "--domain2",
        default=os.environ.get("CHATMAIL_DOMAIN2", "ci-chatmail.testrun.org"),
        help="a second chatmail domain to run test against",
    )
    args = parser.parse_args()

    if args.dns:
        args.deploy = True
    if args.test:
        args.deploy = True
    ssh_args = dict(key_filename=args.ssh_private_key) if args.ssh_private_key else {}

    step = "Allocating relay"
    print(f"\n============== {step} ===============")
    hclient = hcloud.Client(token=args.hetzner_api_token)
    vps = allocate_vps(hclient, args.vps_name, args.run_id)
    vps = hclient.servers.get_by_id(vps.id)
    while vps.labels.get("run") != args.run_id:
        # Lost the race — retry with a different server
        vps = allocate_vps(hclient, args.vps_name, args.run_id)
        vps = hclient.servers.get_by_id(vps.id)

    if args.vps_name:
        if args.vps_name != vps.name:
            print(f"WARNING: {args.vps_name} not available.")
    print(f"\n+++ using {vps.name} for deployment\n")
    ipv4 = vps.public_net.ipv4.ip if not args.ssh_host else args.ssh_host

    exc = None
    if args.deploy:
        try:
            step = "Deploying relay"
            print(f"\n============== {step} ===============")
            print(f"+++ uploading relay repository from {args.relay_repo}")
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
            deploy(vps, ipv4, ssh_args)
            if args.dns:
                step = "Setting DNS records"
                print(f"\n============== {step} ===============")
                set_dns(ipv4, vps.name, args.dns, ssh_args)
            if args.test:
                step = "Running tests"
                print(f"\n============== {step} ===============")
                run_tests(ipv4, args.domain2, ssh_args)
                vps = vps.update(labels={"state":"successful"})
        except Exception as e:
            print(f"\n============= {step} failed: {type(e).__name__} ==============")
            traceback.print_exc()
            vps = vps.update(labels={"state":"failed"})
            exc = e
    if args.rebuild:
        print("\n============== Rebuilding VPS ===============")
        rebuild_vps(ipv4, vps, args.ssh_private_key, args.dns)
        vps = vps.update(labels={"state":"ready"})
        print("\n+++ Rebuilding VPS finished.")
    with open("/tmp/pool-target", "w") as f:
        f.write(vps.name)
    if exc:
        print(f"\n============= {step} failed with {type(exc).__name__}, see context above ==============")
        raise exc


if __name__ == "__main__":
    main()
