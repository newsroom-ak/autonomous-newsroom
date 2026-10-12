output "url" {
  description = "Public HTTPS address (certificate is issued a minute or two after first boot)."
  value       = "https://${local.hostname}"
}

output "public_ip" {
  value = aws_eip.newsroom.public_ip
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/confer_aws_ed25519 ubuntu@${aws_eip.newsroom.public_ip}"
}

output "instance_id" {
  value = aws_instance.newsroom.id
}

output "instance_type" {
  value = aws_instance.newsroom.instance_type
}

output "ami_name" {
  value = data.aws_ami.ubuntu_2404.name
}
