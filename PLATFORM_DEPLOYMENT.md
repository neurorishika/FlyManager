# FlyManager Platform Deployment Runbooks

This document explains how to deploy FlyManager on:

- Synology NAS
- Ubuntu server
- AWS
- GCP

The application is currently packaged as a Docker Compose deployment with a bundled MongoDB container. The simplest and most reliable production strategy is to run it on a single host with persistent local storage, then expose it publicly with the included Caddy reverse proxy.

If you want the shortest generic setup path first, read [DEPLOYMENT.md](DEPLOYMENT.md) before using the platform-specific instructions below.
For the detailed backup and recovery runbook shared by all platforms, read [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md).

## Choose The Right Platform Shape

For this repository as it exists today, the recommended targets are:

- Synology: Container Manager project on a NAS with a real domain
- Ubuntu: a single VM or physical server running Docker Engine and Docker Compose v2
- AWS: a single Ubuntu EC2 instance with an Elastic IP
- GCP: a single Ubuntu Compute Engine VM with a static external IP

These instructions intentionally avoid more complex orchestrators such as ECS, EKS, GKE, or Cloud Run because the current deployment model assumes:

- one app container running the scheduler
- one attached MongoDB service
- one persistent host filesystem for data and backups

## Common Production Checklist

Use this checklist on every platform:

1. Clone the repository onto the target machine.
2. Copy `.env.example` to `.env`.
3. Set a strong `SECRET_KEY`.
4. Set `FLYMANAGER_DOMAIN` to the public hostname.
5. Leave `APP_PUBLISHED_HOST=127.0.0.1`.
6. Leave `MONGO_PUBLISHED_HOST=127.0.0.1`.
7. Configure optional `FLYMANAGER_ADMIN_*` values if you want bootstrap account creation.
8. Point DNS to the machine's public IP.
9. Open inbound ports `80` and `443`.
10. Start the production stack with `./scripts/install-production.sh your-domain`.
11. Verify `https://your-domain` and `/health/ready`.
12. Run both backup scripts after the first successful deployment.

## Synology NAS

This is the best fit when the lab already has a Synology device that is always on and reachable from the network.

### Recommended Synology prerequisites

- DSM 7.x or newer
- Container Manager installed
- SSH enabled for easier Git and Compose management
- a shared folder with enough space for Docker volumes and backups
- a static LAN IP for the NAS
- router access so ports `80` and `443` can be forwarded if the NAS is exposed directly

### Synology deployment model

Recommended host path:

```text
/volume1/docker/flymanager
```

Suggested layout on the NAS:

```text
/volume1/docker/flymanager/repo
/volume1/docker/flymanager/backups
/volume1/docker/flymanager/backups/offsite
```

### Step 1: Prepare the NAS

In DSM:

1. Install Container Manager.
2. Enable SSH in Control Panel.
3. Create a shared folder for the project if one does not already exist.
4. Make sure the NAS has enough free space for MongoDB growth, uploaded files, and backups.

### Step 2: SSH into the NAS

From your workstation:

```bash
ssh your-user@your-synology-ip
```

Create the working directories:

```bash
mkdir -p /volume1/docker/flymanager
cd /volume1/docker/flymanager
```

### Ubuntu Step 3: Clone the repository

If `git` is available on the NAS:

```bash
git clone https://github.com/neurorishika/FlyManager.git repo
cd repo
```

If `git` is not available, copy the repository from another machine into the NAS shared folder and then `cd` into it.

### Step 4: Configure the environment

```bash
cp .env.example .env
```

Edit `.env` and set:

```env
SECRET_KEY=replace-with-a-long-random-secret
FLYMANAGER_DOMAIN=flymanager.yourdomain.org
APP_PUBLISHED_HOST=127.0.0.1
MONGO_PUBLISHED_HOST=127.0.0.1
```

Optional first admin bootstrap:

```env
FLYMANAGER_ADMIN_USERNAME=your-admin
FLYMANAGER_ADMIN_PASSWORD=replace-with-a-strong-password
FLYMANAGER_ADMIN_INITIALS=ABC
```

### Step 5: Start the stack

If Docker Compose is available through Synology's CLI:

```bash
./scripts/install-production.sh flymanager.yourdomain.org
```

If you prefer the raw command:

```bash
docker compose -f compose.yaml -f compose.production.yaml up -d --build
```

### Step 6: Configure router and DNS

You need both:

- public DNS pointing `flymanager.yourdomain.org` to your public IP
- router forwarding of TCP `80` and `443` to the Synology NAS LAN IP

If your ISP blocks inbound `80` or `443`, certificate issuance may fail.

### Step 7: Verify the deployment

```bash
docker compose -f compose.yaml -f compose.production.yaml ps
docker compose -f compose.yaml -f compose.production.yaml logs -f proxy
```

Then open:

```text
https://flymanager.yourdomain.org
```

### Synology-specific notes

- Put the repository in a shared folder that survives DSM restarts and updates.
- Synology filesystem performance can be slower than a VM or server, so keep regular MongoDB backups.
- If you use DSM's firewall, explicitly allow inbound `80` and `443`.
- If you do not want direct internet exposure on the NAS, place it behind a VPN or a reverse proxy appliance instead of port-forwarding from the public internet.

### Synology backup automation

Use DSM Task Scheduler to run the shared scripts from the repository checkout.

Example Mongo backup task:

```bash
cd /volume1/docker/flymanager/repo && \
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" \
BACKUP_OFFSITE_DIR=/volume1/docker/flymanager/backups/offsite \
./scripts/mongo-backup.sh
```

Example state backup task:

```bash
cd /volume1/docker/flymanager/repo && \
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" \
BACKUP_OFFSITE_DIR=/volume1/docker/flymanager/backups/offsite \
./scripts/state-backup.sh
```

Recommended starting cadence:

- Mongo backup every hour
- state backup once per day
- an additional Mongo backup immediately before upgrades

When restoring on Synology, run the same shared restore scripts from the repo checkout:

```bash
cd /volume1/docker/flymanager/repo && \
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" \
./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz

cd /volume1/docker/flymanager/repo && \
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" \
./scripts/mongo-restore.sh backups/mongodb/flymanager_mongodb_YYYYMMDDTHHMMSSZ.archive.gz
```

## Ubuntu Server

This is the simplest general-purpose production deployment. It works for a small dedicated server, a lab VM, or a cloud VM.

### Recommended Ubuntu target

- Ubuntu 22.04 LTS or Ubuntu 24.04 LTS
- at least 2 vCPU
- at least 4 GB RAM
- at least 30 GB SSD storage for app data, MongoDB, and backups

### Step 1: Install system packages

SSH into the machine and run:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git ufw
```

### Step 2: Install Docker Engine and Compose v2

Use Docker's official Ubuntu instructions. The compact command sequence is:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Optional, so you can run Docker without `sudo` after logging in again:

```bash
sudo usermod -aG docker $USER
```

### Step 3: Clone the repository

```bash
cd /opt
sudo git clone https://github.com/neurorishika/FlyManager.git flymanager
sudo chown -R $USER:$USER /opt/flymanager
cd /opt/flymanager
```

### Step 4: Configure the firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### Step 5: Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```env
SECRET_KEY=replace-with-a-long-random-secret
FLYMANAGER_DOMAIN=flymanager.yourdomain.org
```

Optional bootstrap account:

```env
FLYMANAGER_ADMIN_USERNAME=your-admin
FLYMANAGER_ADMIN_PASSWORD=replace-with-a-strong-password
FLYMANAGER_ADMIN_INITIALS=ABC
```

### Step 6: Start FlyManager

```bash
./scripts/install-production.sh flymanager.yourdomain.org
```

### Step 7: Validate

```bash
docker compose -f compose.yaml -f compose.production.yaml ps
docker compose -f compose.yaml -f compose.production.yaml logs -f app
docker compose -f compose.yaml -f compose.production.yaml logs -f proxy
```

Then verify:

```text
https://flymanager.yourdomain.org
```

### Ubuntu-specific notes

- Keep the repo in a stable path such as `/opt/flymanager`.
- Back up `/opt/flymanager/.env`, the project repository, and the MongoDB archives under `backups/mongodb/`.
- If you later add monitoring, start with Docker logs plus a basic uptime monitor against `/health/ready`.

### Ubuntu backup automation

The shared backup scripts can run directly from cron or a systemd timer.

Simple cron example:

```bash
0 * * * * cd /opt/flymanager && COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" BACKUP_OFFSITE_DIR=/srv/flymanager-backups ./scripts/mongo-backup.sh >> /opt/flymanager/backups/logs/mongo-backup.log 2>&1
15 2 * * * cd /opt/flymanager && COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" BACKUP_OFFSITE_DIR=/srv/flymanager-backups ./scripts/state-backup.sh >> /opt/flymanager/backups/logs/state-backup.log 2>&1
```

Version-controlled scheduler assets are also included under:

- `deploy/cron/flymanager-backups.crontab.example`
- `deploy/systemd/flymanager-*.service`
- `deploy/systemd/flymanager-*.timer`
- `deploy/systemd/flymanager-backups.env.example`

## AWS

For the current stack, the cleanest AWS deployment target is a single Ubuntu EC2 instance with Docker Compose.

### Recommended AWS architecture

- EC2 instance running Ubuntu 22.04 or 24.04
- Elastic IP attached to the instance
- Route 53 DNS record pointing to the Elastic IP
- security group allowing inbound `22`, `80`, and `443`
- EBS root volume sized for MongoDB data growth and backups

This is simpler than ECS for the current repository because Compose, local persistent storage, and the bundled MongoDB are already part of the deployment design.

### Step 1: Create the EC2 instance

Recommended baseline:

- instance type: `t3.medium` or larger
- OS: Ubuntu Server LTS
- storage: 30 GB or more gp3 EBS
- authentication: SSH key pair

Attach an Elastic IP so the public IP does not change across restarts.

### Step 2: Configure the security group

Allow inbound:

- TCP `22` from your admin IP range
- TCP `80` from `0.0.0.0/0`
- TCP `443` from `0.0.0.0/0`

Do not expose MongoDB publicly.

### Step 3: Point DNS to the Elastic IP

Create an `A` record such as:

```text
flymanager.yourdomain.org -> your Elastic IP
```

### Step 4: SSH into the instance

```bash
ssh -i /path/to/key.pem ubuntu@your-elastic-ip
```

### AWS Step 5: Install Docker and Git

Use the Ubuntu steps from the Ubuntu section in this document.

### AWS Step 6: Clone and configure the app

```bash
cd /opt
sudo git clone https://github.com/neurorishika/FlyManager.git flymanager
sudo chown -R $USER:$USER /opt/flymanager
cd /opt/flymanager
cp .env.example .env
```

Set at minimum:

```env
SECRET_KEY=replace-with-a-long-random-secret
FLYMANAGER_DOMAIN=flymanager.yourdomain.org
```

### AWS Step 7: Deploy

```bash
./scripts/install-production.sh flymanager.yourdomain.org
```

### AWS Step 8: Verify

```bash
docker compose -f compose.yaml -f compose.production.yaml ps
curl -I https://flymanager.yourdomain.org
```

### AWS-specific notes

- Use an Elastic IP, not just the default public IP, so DNS remains stable.
- Take EBS snapshots in addition to MongoDB archive backups if you want faster machine-level recovery.
- Keep SSH restricted to known admin IP addresses.
- If you use Route 53, lower the DNS TTL during migration so cutovers are easier.

### AWS backup automation

Keep the backup scripts on the EC2 host and use the same cron or systemd timer pattern as Ubuntu.

Recommended off-host pattern:

- local archives stay on the instance filesystem or attached EBS volume
- `BACKUP_OFFSITE_DIR` points to a mounted backup target if present
- use a host-level sync step or snapshot policy to move archives into S3 or another AWS-managed retention layer
- keep the shared restore scripts on the instance so you can recover both state and Mongo directly after rehydrating the VM or attached volume

## GCP

For the current stack, the cleanest GCP deployment target is a single Ubuntu Compute Engine VM with Docker Compose.

### Recommended GCP architecture

- Compute Engine VM running Ubuntu LTS
- static external IP attached to the VM
- Cloud DNS record pointing to the static IP
- VPC firewall rule allowing inbound `80` and `443`
- persistent boot disk sized for MongoDB data and backups

### Step 1: Create the VM

Recommended baseline:

- machine type: `e2-standard-2` or larger
- boot disk: Ubuntu LTS, 30 GB or more SSD persistent disk
- network: default VPC is fine for a single-server deployment
- external IP: reserve and assign a static IP

### Step 2: Configure firewall rules

Allow inbound:

- TCP `22` from your admin IP range
- TCP `80` from the internet
- TCP `443` from the internet

Do not create a public rule for MongoDB.

### Step 3: Point DNS to the static IP

Create an `A` record such as:

```text
flymanager.yourdomain.org -> your static external IP
```

### Step 4: SSH into the VM

You can use the GCP console or standard SSH:

```bash
gcloud compute ssh your-vm-name --zone your-zone
```

### GCP Step 5: Install Docker and Git

Use the Ubuntu steps from the Ubuntu section in this document.

### GCP Step 6: Clone and configure the app

```bash
cd /opt
sudo git clone https://github.com/neurorishika/FlyManager.git flymanager
sudo chown -R $USER:$USER /opt/flymanager
cd /opt/flymanager
cp .env.example .env
```

Set at minimum:

```env
SECRET_KEY=replace-with-a-long-random-secret
FLYMANAGER_DOMAIN=flymanager.yourdomain.org
```

### GCP Step 7: Deploy

```bash
./scripts/install-production.sh flymanager.yourdomain.org
```

### GCP Step 8: Verify

```bash
docker compose -f compose.yaml -f compose.production.yaml ps
curl -I https://flymanager.yourdomain.org
```

### GCP-specific notes

- Reserve a static external IP before pointing DNS.
- Make sure the VPC firewall allows `80` and `443` to the VM, even if the OS firewall is already open.
- For recovery, snapshot the persistent disk and also run MongoDB archive backups.

### GCP backup automation

Use the same Ubuntu-style host scheduling pattern on the Compute Engine VM.

Recommended off-host pattern:

- local archives stay on the persistent disk or attached data disk
- `BACKUP_OFFSITE_DIR` points to a mounted secondary path when available
- add a host-level sync or policy to move archives into Cloud Storage for longer retention and host-loss recovery
- keep the shared restore scripts on the VM so you can recover both state and Mongo directly after rehydrating the VM or attached volume

## Backup Strategy By Platform

For retention policy, restore order, manifest details, and recovery drills, see [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md).

Use the included Mongo backup script on every platform:

```bash
./scripts/mongo-backup.sh
```

Use the deployment-state backup on every platform as well:

```bash
./scripts/state-backup.sh
```

Use the matching restore path during drills and real recovery:

```bash
./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
./scripts/mongo-restore.sh backups/mongodb/flymanager_mongodb_YYYYMMDDTHHMMSSZ.archive.gz
```

For publicly deployed stacks using the production overlay:

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/mongo-backup.sh
```

Recommended minimum policy:

- hourly MongoDB archive backup on active systems
- daily deployment-state backup
- one retained pre-upgrade backup before every deployment
- off-machine copy of backups for disaster recovery

Platform-specific backup recommendations:

- Synology: copy MongoDB archives to a second shared folder, another NAS, or Synology Hyper Backup target
- Ubuntu: copy archives to external storage or another server
- AWS: store backup archives off-instance and take EBS snapshots for faster recovery
- GCP: store backup archives off-instance and take persistent disk snapshots for faster recovery

## When Not To Use These Runbooks

These runbooks are intentionally optimized for the current single-host Compose architecture.

They are not the right starting point if you want:

- multi-instance horizontal scaling
- managed MongoDB instead of the bundled MongoDB container
- Kubernetes-based deployments
- secret management through AWS Secrets Manager or GCP Secret Manager
- zero-downtime blue/green rollout orchestration

Those are possible next steps, but they would be a different deployment design than the one currently committed in this repository.
