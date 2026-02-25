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


def test_and_deploy(vps: hcloud.servers.client.BoundServer, ipv4: str, domain2=""):
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
    ssh.open()
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
    relay_repo = os.environ.get("RELAY_REPO", "../relay")
    domain2 = "CHATMAIL_DOMAIN2=" + os.environ.get("CHATMAIL_DOMAIN2", "ci-chatmail.testrun.org")
    ssh_private_keyfile = os.environ.get("SSH_PRIVATE_KEYFILE", "~/.ssh/staging.testrun.org")
    hetzner_api_token = os.environ.get("HETZNER_API_TOKEN")
    hclient = hcloud.Client(token=hetzner_api_token)

    ready = get_pool(hclient)
    print("+++ available servers:")
    [print(s.name) for s in ready]
    vps = ready[0]
    ipv4 = vps.public_net.ipv4.ip
    print(f"\n+++ using {vps.name} for deployment\n")

    try:
        print(f"+++ uploading relay repository from {relay_repo}")
        vps = vps.update(labels={"state":"deploying"})
        sysrsync.run(
            source=relay_repo,
            destination="/root/relay",
            exclusions=[".tox", "venv"],
            destination_ssh="root@" + ipv4,
            options=["-r"],
            sync_source_contents=True,
            strict_host_key_checking=False,
            private_key=ssh_private_keyfile,
        )

        test_and_deploy(vps, ipv4, domain2)
        vps = vps.update(labels={"state":"successful"})
        rebuild_vps(ipv4, vps)
        vps = vps.update(labels={"state":"ready"})
    except Exception as e:
        vps = vps.update(labels={"state":"failed"})
        raise e


if __name__ == "__main__":
    main()

