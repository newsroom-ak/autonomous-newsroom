# AWS Free Plan deployment (plan B)

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

## Prerequisites that need your approval (none exist yet)

1. **AWS CLI >= 2.32** on your Mac (`brew install awscli`).
2. **A least-privilege IAM user, created in the console, with a password and MFA and no access keys.**
   Attach the AWS-managed policies `AmazonEC2FullAccess` and `SignInLocalDevelopmentAccess`.
   Do **not** use the root user for Terraform, and do **not** create root or IAM access keys.
3. **Sign in with `aws login --profile newsroom`** as that user. This issues short-lived credentials refreshed
   by the CLI (up to 12 hours); nothing long-lived is stored. Do **not** use IAM Identity Center: it requires
   AWS Organizations, and joining Organizations **automatically upgrades a Free Plan account to the paid plan**.
4. **An SSH key pair:** `ssh-keygen -t ed25519 -f ~/.ssh/confer_aws_ed25519` (use a passphrase). Only the
   `.pub` file is used by Terraform.
5. **Your public IPv4** for `ssh_allowed_cidr` (copy `terraform.tfvars.example` to `terraform.tfvars`, which is
   git-ignored).
6. **Confirm** the instance type and region below.

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

## Cost estimate (us-west-2 and us-east-1 have identical prices)

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
