# AWS Free Plan deployment (plan B)

Region: **`us-east-2` (Ohio).** AWS assigned the author's Free Plan project to Ohio and states that Oregon
(`us-west-2`) is unavailable without upgrading the plan, which is not being done. The first draft targeted Oregon.
Availability in Ohio was checked offline (see "Ohio checks" below) and then confirmed by a real apply.

Status: **deployed and verified (2026-10-12): 9 resources applied in Ohio, public app at https://3-13-197-253.sslip.io.** Oracle Always Free had no capacity (see `docs/deployment.md`), so the
assessment's plan B is used: an AWS Free Plan account provisioned with Terraform. The Oracle files in
`infra/` are untouched; this is a separate root module with its own state.

## What it creates (9 Terraform resources)

| Resource | Purpose | Cost |
|---|---|---|
| `aws_instance` (default `t3.small`, Ubuntu 24.04, IMDSv2 only, no IAM role) | Runs the app and Caddy | compute |
| `aws_eip` + `aws_eip_association` | One stable public IPv4, so the HTTPS hostname is stable | $0.005/hr |
| root gp3 volume, 20 GB, encrypted (part of the instance) | OS, code, SQLite database | $0.08/GB-month |
| `aws_security_group` + 4 rules | SSH from one IP; HTTP/HTTPS from anywhere; all egress | free |
| `aws_key_pair` | Public SSH key only | free |

It reuses the account's **default VPC and subnet**, so there is no VPC, NAT gateway, load balancer, database
service, IAM role or secrets service to create or pay for.

## How the VM runs the app

Cloud-init (`user_data.sh.tftpl`, no secrets) installs Caddy and Python, clones the public repository at a
**pinned commit** (`app_git_ref`, the exact reviewed code that is live today), installs the pinned
dependencies in a venv, and starts two systemd services:

- `newsroom.service`: uvicorn on `127.0.0.1:8000` as a non-root `newsroom` user, `Restart=always`, enabled at
  boot. The app's own scheduler runs a refresh at startup and every 180 minutes, so a reboot needs no cron.
- `caddy.service`: HTTPS on 443 for `<ip-with-dashes>.sslip.io` with an automatically issued and renewed
  certificate; `/docs`, `/redoc` and `/openapi.json` return 404; everything else is proxied to the app.

The site is dark for about 70 seconds after every (re)start while the app's first refresh runs (a known
limitation); Caddy answers 502 during that time.

## Architecture decision

Only the **public newsroom application** runs on AWS (`t3.small`, `us-east-2` (Ohio)). Hermes, OpenClaw, the
orchestrator and all agent credentials stay on the engineer's Mac: the agent environment is separate from the
lightweight public app, so nothing on the VM can reach the agents' tokens, sandboxes or GitHub permissions.
The trade-off, described in `DELIVERY.md`, is that the agents' environment is not reproduced on the VM.

## Authentication: what was verified and what is only proposed

Goal: Terraform runs with **short-lived credentials** and **no access keys**, and the account stays a Free Plan
account (nothing here enables AWS Organizations, IAM Identity Center or an upgrade).

### Verified on the author's new AWS Free Plan account

- The account exists and is on the Free Plan. Its project is restricted to `us-east-2` (Ohio).
- An IAM user `newsroom-terraform` was created with the EC2 policy and `SignInLocalDevelopmentAccess` attached.
  The policy attached at that time was the first, **Oregon** version; it has to be replaced by the Ohio version in
  this directory before use. No access keys were created.
- In AWS's new Settings > Team experience, **AWS did not offer console-password setup for that IAM user.**
- **`aws login --region us-west-2 --profile newsroom` failed** (browser: "Something went wrong"), and so did
  `aws login --remote`. Both attempts used **Oregon**, which AWS blocks for this project. Neither was retried.
- **AWS CloudShell reported:** "Your account verification is in progress. This may take up to two days for new
  accounts." The message was not re-checked, but **it did not block `terraform apply`** in Ohio.
- **`aws login --region us-east-2 --profile newsroom` succeeded** on the first attempt in Ohio, with no extra
  browser step, and returned temporary credentials for an `AccountFullAccessRole` session (not the
  least-privilege `newsroom-terraform` user). The Oregon failures are therefore best explained by the region
  mismatch, though AWS did not state the cause. No access keys were created.
- **Read-only checks in Ohio passed:** caller identity, no existing instances, Elastic IPs or key pairs, a default
  VPC with subnets in `us-east-2a`/`b`/`c`, `t3.small` offered in all three, and Canonical's Ubuntu 24.04 AMI
  (`...-20261004`) visible.
- **`terraform plan` succeeded** (see "Plan result" below). `apply` has **not** been run and no VM exists.

### Proposed, not verified

The steps below were not what worked: the successful login used a full-access role, not this IAM user. They remain a
least-privilege option and are unverified.

1. **Create the policy** (IAM > Policies > Create policy > JSON): paste `iam/newsroom-terraform-ec2-policy.json`
   (EC2 actions in `us-east-2` only). Name it `NewsroomTerraformEc2UsEast2`.
2. **An IAM user for `aws login` needs console access.** `aws login` signs in through the AWS Console, so an IAM
   user created *without* console access (no password) **cannot** be used with it; creating such a user is not
   sufficient. The user needs a console password, which the new Team experience did not offer, so this path
   may be unavailable and the user may have to be created differently once the account allows it. If it
   works: attach `NewsroomTerraformEc2UsEast2` and the AWS-managed `SignInLocalDevelopmentAccess`, add MFA, and
   still **do not create access keys**.
3. **Sign in from the terminal:** `aws login --region us-east-2 --profile newsroom` and, in the browser, sign in as
   the IAM user. The CLI should then refresh temporary credentials for up to 12 hours.
4. **Check the identity** without printing the account number:
   `aws sts get-caller-identity --profile newsroom --query Arn --output text | sed 's/[0-9]\{12\}/<account>/'`
5. **SSH key** (a passphrase is recommended): `ssh-keygen -t ed25519 -f ~/.ssh/confer_aws_ed25519`.
6. **Then**: `cd infra/aws && terraform init && terraform plan` and review before any apply.

**Alternatives and why they are not used.** IAM Identity Center is unsuitable: its portal access needs an
*organization instance*, which needs AWS Organizations, and joining Organizations automatically upgrades a Free
Plan account to the paid plan. The root user is not used for Terraform. If `aws login` keeps failing after
verification, the remaining options are all unverified and need the author's decision: a second profile using
`credential_process = aws configure export-credentials --profile newsroom --format process` (only helps once
a login session exists), or, as a last resort, a temporary access key with the same narrow policy, which would
be a deliberate departure from the no-access-keys goal and must be approved first.

## Secrets: nothing touches Terraform, GitHub or state

- No credentials appear in any `.tf` file, variable, `user_data`, plan or state. `user_data` is readable from
  the instance metadata and the console, so it deliberately holds only public information.
- The model key is installed **over SSH, after provisioning**, straight from your local env file to a
  root-owned `0640` file on the VM, never printed:

  ```bash
  awk -F= '/^OLLAMA_API_KEY=/' ~/.config/newsroom/env | ssh -i ~/.ssh/confer_aws_ed25519 ubuntu@<ip> \
    'sudo install -m 0640 -o root -g newsroom /dev/stdin /etc/newsroom/env && sudo systemctl restart newsroom'
  ```

  Until then the site runs and shows the clearly marked title fallback instead of model summaries.
- The VM has no AWS permissions (no instance profile) and requires IMDSv2.

## Cost estimate (`us-east-2`, Ohio)

Prices from AWS's public pricing data (EC2 on-demand Linux) and the VPC and EBS pricing pages; 730 hours/month.

| | `t3.small` (recommended) | `t3.micro` | `m7i-flex.large` (assessment) |
|---|---:|---:|---:|
| Instance, $/hr | 0.0208 | 0.0104 | 0.09576 |
| Public IPv4, $/hr | 0.005 | 0.005 | 0.005 |
| 20 GB gp3, $/hr | 0.00219 | 0.00219 | 0.00219 |
| Data transfer out | about $0 (a page is about 13 KB) | | |
| **Per day** | **$0.67** | $0.42 | $2.47 |
| **Per 30 days** | **$20.2** | $12.7 | $74.1 |
| **Days that $100 of credit lasts** | **about 149** | about 237 | **about 40** |

Reading this: the plan page shows "183 days remaining", but **credits, not days, are the limit**. With
`m7i-flex.large` the $100 would be gone in about six weeks and the account would then **close automatically**
(AWS keeps data for 90 days). `t3.small` has ample headroom for an app that uses about 60 MB. A two-to-three
week review window costs roughly $9-14 of credit on `t3.small`. Credits are not "billing": on the Free Plan
AWS states charges cannot be incurred and ineligible instance types are blocked rather than billed.

## Verify the deployment

```bash
terraform output                             # url, ip, ssh command
ssh -i ~/.ssh/confer_aws_ed25519 ubuntu@<ip> 'systemctl is-active newsroom caddy; \
   sudo git -C /opt/newsroom/src rev-parse HEAD; sudo tail -3 /var/log/newsroom-bootstrap.log'
curl -s -o /dev/null -w '%{http_code}\n' https://<hostname>/            # 200 (after ~2 minutes)
curl -s https://<hostname>/api/articles | head -c 300                    # real articles, non-empty summaries
curl -s -o /dev/null -w '%{http_code}\n' https://<hostname>/docs         # 404
ssh ... 'sudo reboot'; sleep 120; curl -s -o /dev/null -w '%{http_code}\n' https://<hostname>/   # survives reboot
```

Also confirm from your Mac that only ports 80 and 443 answer publicly (22 only from your IP) and run the same
secret-leak scan used for the Quick Tunnel.

## Remove everything afterwards

```bash
terraform destroy          # instance + root volume, Elastic IP, security group and rules, key pair
```

Then check the console (EC2 instances, volumes, Elastic IPs in the region are all empty), run `aws logout`,
and delete the IAM user. An Elastic IP that is left allocated keeps billing $0.005/hr.

## Risks and caveats

- The hostname relies on the third-party `sslip.io` DNS service and a public certificate authority; if either
  is unavailable, set `public_hostname` to your own name or use the IP over HTTP.
- SSH is limited to one /32; if your IP changes you will be locked out until `ssh_allowed_cidr` is updated.
- The default VPC must exist in the region (the plan fails clearly if it does not).
- `user_data` and the AMI are ignored after creation, so later edits do not replace the VM.
- The SQLite database lives on the root volume and is lost on `destroy` (it is rebuilt by the next refresh).

## Ohio checks (offline, public data, no AWS calls)

- **`t3.small` price in `us-east-2`:** $0.0208/hr on-demand Linux, from AWS's public price list for US East (Ohio);
  the gp3 price there is $0.08/GB-month. These equal the figures in the table above, which therefore stand.
  The public IPv4 charge ($0.005/hr) is AWS's published flat rate and was not re-read from the Ohio file.
- **Ubuntu 24.04 AMI:** Canonical's published image catalogue lists an `ssd-gp3`, `hvm`, amd64 24.04 image for
  `us-east-2` (most recent listing carrying Ohio: build 20260923), matching the Terraform lookup
  (owner 099720109477, `ubuntu-noble-24.04-amd64-server-*`, `hvm-ssd-gp3`). The lookup is by name, so it resolves
  in whichever region the provider uses.
- **Not checked offline:** that `t3.small` is currently launchable in Ohio for this account (capacity and the
  Free Plan's own eligibility list can only be confirmed by `terraform plan`/apply with credentials).

## Plan result (`terraform plan`, no apply)

First run: temporary `newsroom` login in `us-east-2` with a throwaway public key. **Re-run with the real passphrase-protected
Ed25519 key (`~/.ssh/confer_aws_ed25519.pub`) gave the identical result.** The AWS CLI profile `newsroom` was also
corrected to `us-east-2`. Result:

- **`Plan: 9 to add, 0 to change, 0 to destroy`**, with no updates or replacements of existing resources.
- Resources: instance (`t3.small`, Ubuntu 24.04 `...-20261004`, IMDSv2 required, no instance profile, encrypted
  20 GB gp3 root volume, standard CPU credits), Elastic IP and association, key pair, security group with SSH
  (one /32), HTTP, HTTPS and egress rules. No other services.
- The plan reads the default VPC and one default subnet; it creates no VPC, NAT gateway, load balancer or role.
- A plan proves the configuration is accepted, not that a launch succeeds; the apply below did.

## Apply result and verification (2026-10-12)

- Two applies: the first created 8 of 9 resources and failed on the SSH rule (incident 1); the second, from a plan
  showing exactly `1 to add, 0 to change, 0 to destroy`, added it. Afterwards `terraform state list` shows 9
  resources, the instance and Elastic IP association kept their IDs, and a fresh plan shows no changes.
- Instance running, status checks `ok`, cloud-init finished (about 93 s), `newsroom` and `caddy` enabled and active.
- https://3-13-197-253.sslip.io: `/` and `/api/articles` 200, `/docs` 404, HTTP redirects (308), POST 405.
- Model key installed over SSH (see "Secrets"); 12 articles carry model summaries (`gpt-oss:20b`, plus one from the
  `nemotron-3-nano:30b` fallback), 12 are title fallbacks from the first key-less refresh. The key was not found in any
  response, journal, `systemctl show` output or unit file (exact-match checks run on the VM).
- **Reboot test (one `aws ec2 reboot-instances`, 2026-10-12):** requested 07:33:39Z; services active again within
  about 20 s of boot; **HTTPS 200 again after 76 s**; same instance and Elastic IP; the database (100 articles, 100
  distinct URLs, integrity ok) and the model key file (`root:newsroom` 0640, 57 characters) were intact; no errors in
  the new boot's journal.
- **Summary coverage:** latest 10 articles before the reboot: 6 model, 4 title fallbacks; after the reboot's startup
  refresh: 8 model, 2 fallback. Whole database after the reboot: 23 model (22 `gpt-oss:20b`, 1 `nemotron-3-nano:30b`),
  13 fallback, 64 not yet summarised. Links and dates of the latest 10 were valid and ordered, with no duplicates.
- **Scheduled refresh: pending.** Runs 1-3 were startup refreshes; the first timer-driven run is due about 10:35 UTC
  (180 minutes after run 3 finished). Check with `sudo python3` against `refresh_runs` in
  `/var/lib/newsroom/newsroom.db` (read-only) and `journalctl -u newsroom`. Not triggered artificially.
- Running as a temporary session of a full-access role; there is still no least-privilege Terraform identity and no
  access key.

## Terraform incidents (real, from the first apply)

1. **Invalid rule description.** AWS rejected `SSH from the engineer's IP only` with `InvalidParameterValue: Invalid
   rule description` (the apostrophe is not in the allowed set `a-zA-Z0-9. _-:/()#,@[]+=&;{}!$*`). Fixed to `SSH from
   engineer IP only`; the other four descriptions were checked against the same set.
2. **Public IP drift forcing instance replacement.** With `associate_public_ip_address = false` the next plan wanted
   to replace the instance (3 to add, 2 to destroy). The provider reports `true` whenever the network interface has a
   public IP, including an Elastic IP. The config now states `true` (commented in `main.tf`), which matches reality
   without `ignore_changes`; the plan then showed only the missing rule, and the apply left the instance in place.
