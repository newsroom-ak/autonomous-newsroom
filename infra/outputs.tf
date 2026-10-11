output "availability_domain" {
  value = local.ad_name
}

output "ubuntu_image_name" {
  value = local.ubuntu_image.display_name
}

output "ubuntu_image_candidates" {
  value = [for i in data.oci_core_images.ubuntu_2404_arm.images : i.display_name]
}
