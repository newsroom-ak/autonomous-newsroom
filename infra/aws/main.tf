# Smallest footprint for the FastAPI + SQLite newsroom: one EC2 instance in the account's default
# VPC, one security group, one key pair, one Elastic IP. No IAM roles, load balancer, NAT gateway,
# database service or secrets service, so no secret ever passes through Terraform or its state.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_ami" "ubuntu_2404" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  hostname = coalesce(var.public_hostname, "${replace(aws_eip.newsroom.public_ip, ".", "-")}.sslip.io")
}

resource "aws_key_pair" "newsroom" {
  key_name   = "confer-newsroom"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_security_group" "newsroom" {
  name        = "confer-newsroom"
  description = "Newsroom: SSH from one IP, HTTP/HTTPS from anywhere"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.newsroom.id
  description       = "SSH from the engineer's IP only"
  cidr_ipv4         = var.ssh_allowed_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.newsroom.id
  description       = "HTTP (ACME challenge and redirect to HTTPS)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.newsroom.id
  description       = "HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.newsroom.id
  description       = "Outbound: apt, GitHub clone, news feed, Ollama Cloud, ACME"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# One stable public IPv4 (billed $0.005/hr whether or not attached). Created before the instance so
# its address can be put in the Caddy hostname; associated afterwards.
resource "aws_eip" "newsroom" {
  domain = "vpc"
}

resource "aws_instance" "newsroom" {
  ami                         = data.aws_ami.ubuntu_2404.id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnets.default.ids[0]
  vpc_security_group_ids      = [aws_security_group.newsroom.id]
  key_name                    = aws_key_pair.newsroom.key_name
  associate_public_ip_address = false # the Elastic IP is the only public IPv4 (one IPv4 charge, not two)
  monitoring                  = false # detailed monitoring is a paid add-on

  # IMDSv2 only; no instance profile, so the VM has no AWS permissions at all.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gbs
    encrypted             = true
    delete_on_termination = true
  }

  # T3 defaults to "unlimited" CPU credits, which can bill for surplus CPU. Standard mode cannot.
  dynamic "credit_specification" {
    for_each = startswith(var.instance_type, "t") ? [1] : []
    content {
      cpu_credits = "standard"
    }
  }

  # Contains no secrets: it only installs packages, clones the PUBLIC repository at a pinned commit
  # and creates an empty, root-owned env file. The API key is delivered over SSH afterwards.
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    repo_url = var.repo_url
    app_ref  = var.app_git_ref
    hostname = local.hostname
  })

  tags = {
    Name = "confer-autonomous-newsroom"
  }

  lifecycle {
    ignore_changes = [ami, user_data] # a new Canonical AMI or a script tweak must not replace the VM
  }
}

resource "aws_eip_association" "newsroom" {
  instance_id   = aws_instance.newsroom.id
  allocation_id = aws_eip.newsroom.id
}
