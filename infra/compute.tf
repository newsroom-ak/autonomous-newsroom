resource "oci_core_instance" "newsroom" {
  compartment_id      = var.tenancy_ocid
  availability_domain = local.ad_name
  display_name        = "confer-autonomous-newsroom"
  shape               = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gbs
  }

  source_details {
    source_type             = "image"
    source_id               = local.ubuntu_image.id
    boot_volume_size_in_gbs = var.boot_volume_gbs
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.newsroom.id
    assign_public_ip = true
    display_name     = "confer-newsroom-vnic"
  }

  metadata = {
    ssh_authorized_keys = file(pathexpand(var.ssh_public_key_path))
  }
}
