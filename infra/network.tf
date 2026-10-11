resource "oci_core_vcn" "newsroom" {
  compartment_id = var.tenancy_ocid
  cidr_blocks    = ["10.0.0.0/16"]
  display_name   = "confer-newsroom-vcn"
  dns_label      = "newsroom"
}

resource "oci_core_internet_gateway" "newsroom" {
  compartment_id = var.tenancy_ocid
  vcn_id         = oci_core_vcn.newsroom.id
  display_name   = "confer-newsroom-igw"
  enabled        = true
}

resource "oci_core_route_table" "newsroom" {
  compartment_id = var.tenancy_ocid
  vcn_id         = oci_core_vcn.newsroom.id
  display_name   = "confer-newsroom-routes"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.newsroom.id
  }
}

resource "oci_core_security_list" "newsroom" {
  compartment_id = var.tenancy_ocid
  vcn_id         = oci_core_vcn.newsroom.id
  display_name   = "confer-newsroom-security"

  ingress_security_rules {
    protocol    = "6"
    source      = var.ssh_allowed_cidr
    source_type = "CIDR_BLOCK"
    description = "SSH from authorized IP only"

    tcp_options {
      min = 22
      max = 22
    }
  }

  ingress_security_rules {
    protocol    = "6"
    source      = "0.0.0.0/0"
    source_type = "CIDR_BLOCK"
    description = "Public HTTP"

    tcp_options {
      min = 80
      max = 80
    }
  }

  ingress_security_rules {
    protocol    = "6"
    source      = "0.0.0.0/0"
    source_type = "CIDR_BLOCK"
    description = "Public HTTPS"

    tcp_options {
      min = 443
      max = 443
    }
  }

  egress_security_rules {
    protocol         = "all"
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    description      = "Outbound internet access"
  }
}

resource "oci_core_subnet" "newsroom" {
  compartment_id             = var.tenancy_ocid
  vcn_id                     = oci_core_vcn.newsroom.id
  cidr_block                 = "10.0.1.0/24"
  display_name               = "confer-newsroom-public-subnet"
  dns_label                  = "public"
  route_table_id             = oci_core_route_table.newsroom.id
  security_list_ids          = [oci_core_security_list.newsroom.id]
  prohibit_public_ip_on_vnic = false
}
