# AWS Free Plan deployment (plan B)

Region: **`us-east-2` (Ohio).** AWS assigned the author's Free Plan project to Ohio and states that Oregon
(`us-west-2`) is unavailable without upgrading the plan, which is not being done. The first draft targeted Oregon.
Availability in Ohio was checked offline (see "Ohio checks" below); nothing has been created.

Status: **proposed, not applied.** Oracle Always Free had no capacity (see `docs/deployment.md`), so the
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

## Authentication and the manual steps (done once, in the console)

Goal: Terraform runs with **short-lived credentials** from a **least-privilege IAM user**, with **no access
keys**, and the account stays a Free Plan account (nothing here enables AWS Organizations).

1. **Create the policy** (IAM > Policies > Create policy > JSON): paste `iam/newsroom-terraform-ec2-policy.json`
   (EC2 actions in `us-east-2` only). Name it `NewsroomTerraformEc2UsEast2`.
2. **Create the user** (IAM > Users > Create user): name `newsroom-terraform`; tick *Provide user access to the
   AWS Management Console*, choose *I want to create an IAM user*, set a custom password. Attach the policies
   `NewsroomTerraformEc2UsEast2` and the AWS-managed `SignInLocalDevelopmentAccess`. **Do not create access keys.**
3. **Add MFA** to that user (user > Security credentials > Assign MFA device > authenticator app).
4. **Sign in from the terminal** (the CLI is installed and a key-free profile `newsroom` exists):
   `aws login --profile newsroom`, choose *Sign in with IAM user* in the browser (account ID or alias,
   `newsroom-terraform`, password, MFA), and approve. The CLI refreshes the credentials for up to 12 hours.
5. **Check the identity** without printing the account number:
   `aws sts get-caller-identity --profile newsroom --query Arn --output text | sed 's/[0-9]\{12\}/<account>/'`
   should end in `user/newsroom-terraform`.
6. **SSH key** (a passphrase is recommended): `ssh-keygen -t ed25519 -f ~/.ssh/confer_aws_ed25519`.
7. **Then**: `cd infra/aws && terraform init && terraform plan` and review before any apply.

Why not the alternatives: `aws login` is AWS's documented way to get temporary credentials from console sign-in
(CLI 2.32+), works with IAM users, and needs no stored keys. **IAM Identity Center is unsuitable**: access to
AWS accounts through its portal needs an *organization instance*, which needs AWS Organizations, and joining
Organizations automatically upgrades a Free Plan account to the paid plan. The **root user** is not used for
Terraform. If `aws login` does not work with Terraform in practice, AWS documents a fallback: a second profile
with `credential_process = aws configure export-credentials --profile newsroom --format process`.

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
   git -C /opt/newsroom/src rev-parse HEAD; sudo tail -3 /var/log/newsroom-bootstrap.log'
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
