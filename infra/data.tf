data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "ubuntu_2404_arm" {
  compartment_id           = var.tenancy_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

locals {
  ad_name       = data.oci_identity_availability_domains.ads.availability_domains[0].name
  ubuntu_images = [for img in data.oci_core_images.ubuntu_2404_arm.images : img if !strcontains(img.display_name, "Minimal")]
  ubuntu_image  = local.ubuntu_images[0]
}
